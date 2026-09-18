"""Build the image, boot it against an EMPTY volume, and prove it serves.

DEPLOYMENT.md §11.2. The acceptance test for the deployment, run with
``make deploy-check``. It is eight steps and every one of them is a claim the
spec makes that would otherwise be checked for the first time on Railway:

1. ``docker build`` for linux/amd64, which runs the image assertion layer.
2. Re-run those assertions INSIDE the built image, because an image can be
   rebuilt from cache without the assertion layer executing again.
3. Run the container with a fresh empty host directory mounted at /app/data,
   with network ingest disabled, no scheduler, and no ANTHROPIC_ in the
   environment.
4. Poll /api/health until 200 and record how long the boot took. That number
   is what the railway.toml healthcheck timeout is set from.
5. For every script the running container lists, POST its run route and assert
   the answer is a structured payload or a structured gap. The list comes from
   /api/panels, never from a copy of it here, so a new panel is covered the
   day it is registered.
6. Assert the ``{"error": true, "panel": ..., "reason": ...}`` 500 shape never
   appears. That shape is what a BROKEN panel returns and it is deliberately
   distinct from the honest ``{empty, reason}`` 200. The difference between
   those two is the whole of step 5.
7. Run one scheduler tick in-process against the empty warehouse and assert a
   ``fetch_run`` row was written. With no ``dim_event`` rows there are no
   deadlines, so the deadline-relative tasks are not owed; the calendar and
   interval ones are, and with ingest disabled they return the gated result,
   which is the ``no_source`` outcome. A row with status ``no_source`` is a
   pass. A tick that writes no row at all is a failure.
8. Tear the container down and delete the host directory.

A script that needs params is given the smallest value its own schema admits,
built from the schema rather than from a list here. A 400 naming the field is
accepted as a structured refusal: the caller asked wrongly and was told which
field, which is not a panel failure.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_IMAGE = "fpl-edge:check"
DEFAULT_PORT = 8399

#: How long boot may take before the check gives up, seconds.
HEALTH_TIMEOUT_S = 300.0

#: The assertions the image's own final layer makes, re-run inside the built
#: image. Kept as one shell string so the two copies are visibly the same
#: claims.
IMAGE_ASSERTIONS = r"""
set -eu
! python -c "import mlx" 2>/dev/null || { echo "mlx present"; exit 1; }
! python -c "import mlx_whisper" 2>/dev/null || { echo "mlx_whisper present"; exit 1; }
! command -v pbpaste >/dev/null 2>&1 || { echo "pbpaste present"; exit 1; }
! test -e "$HOME/.local/bin/claude" || { echo "claude hard path present"; exit 1; }
! find /app -name "*.duckdb" -o -name "*.duckdb.wal" | grep -q . || { echo "duckdb in image"; exit 1; }
! test -e /app/.env || { echo ".env in image"; exit 1; }
! env | grep -q "^ANTHROPIC_" || { echo "ANTHROPIC_ in the container environment"; exit 1; }
test -d /app/seed/data || { echo "no seed directory"; exit 1; }
! test -e /app/seed/data/warehouse/jobs/telegram.log || { echo "telegram.log in the seed"; exit 1; }
echo "image assertions passed"
"""

#: Smallest legal value per JSON Schema type, for a required param.
_SYNTH = {"string": "x", "integer": 1, "number": 1.0, "boolean": False,
          "array": [], "object": {}}


class CheckFailed(RuntimeError):
    """One step did not hold. The message says which and why."""


def _run(argv: list[str], *, capture: bool = False, check: bool = True,
         timeout: float = 1800.0) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(argv)}", flush=True)
    proc = subprocess.run(argv, capture_output=capture, text=True,
                          timeout=timeout, check=False)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "")[-2000:] if capture else ""
        raise CheckFailed(f"{' '.join(argv[:3])} exited {proc.returncode}\n{detail}")
    return proc


def git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, text=True, timeout=10,
                             check=False)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def build_image(image: str) -> None:
    """Step 1."""
    _run(["docker", "build", "--platform", "linux/amd64",
          "--build-arg", f"GIT_SHA={git_sha()}",
          "-t", image, str(REPO_ROOT)])
    size = _run(["docker", "image", "inspect", image, "--format",
                 "{{.Size}}"], capture=True).stdout.strip()
    try:
        print(f"image size: {int(size) / 1e9:.2f} GB ({size} bytes)")
    except ValueError:
        print(f"image size: {size}")


def assert_image_contents(image: str) -> None:
    """Step 2."""
    proc = _run(["docker", "run", "--rm", "--entrypoint", "sh", image,
                 "-c", IMAGE_ASSERTIONS], capture=True, check=False)
    print((proc.stdout or "").strip())
    if proc.returncode != 0:
        raise CheckFailed(
            f"the image assertions failed inside the built image: "
            f"{(proc.stdout or proc.stderr or '').strip()}")


def start_container(image: str, volume: Path, port: int) -> str:
    """Step 3. Returns the container id."""
    proc = _run([
        "docker", "run", "--detach",
        "--platform", "linux/amd64",
        "--publish", f"{port}:8000",
        "--volume", f"{volume}:/app/data",
        "--env", "PORT=8000",
        "--env", "FPL_EDGE_DISABLE_NETWORK_INGEST=1",
        "--env", "FPL_EDGE_SCHEDULER=0",
        # No FPL_ENTRY_ID: the spec's §5 table lists it, but nothing in the
        # package reads it. config.UserConfig.entry_id is the literal 4490171
        # until workstream D replaces the singleton with a per-request user,
        # so setting the variable here would look like configuration and be
        # nothing.
        image,
    ], capture=True)
    return proc.stdout.strip()


def _get(url: str, timeout: float = 30.0) -> tuple[int, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        body = exc.read() or b"null"
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"detail": body.decode("utf-8", "replace")[:500]}


def _post(url: str, payload: dict[str, Any], timeout: float = 60.0) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        body = exc.read() or b"null"
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"detail": body.decode("utf-8", "replace")[:500]}


def wait_for_health(base: str, container: str, *,
                    timeout_s: float = HEALTH_TIMEOUT_S) -> float:
    """Step 4. Returns how many seconds the boot took."""
    started = time.monotonic()
    last: Any = None
    while time.monotonic() - started < timeout_s:
        try:
            status, body = _get(f"{base}/api/health", timeout=10.0)
        except Exception as exc:  # noqa: BLE001 - the server may not be up yet
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0)
            continue
        if status == 200:
            took = time.monotonic() - started
            print(f"health: 200 after {took:.1f}s")
            print(json.dumps(body, indent=2)[:1500])
            return took
        last = f"HTTP {status}: {json.dumps(body)[:400]}"
        time.sleep(1.0)
    logs = _run(["docker", "logs", "--tail", "80", container],
                capture=True, check=False)
    raise CheckFailed(
        f"/api/health did not return 200 within {timeout_s:.0f}s. Last "
        f"answer: {last}\n--- container logs ---\n"
        f"{(logs.stdout or '') + (logs.stderr or '')}")


def synth_params(schema: dict[str, Any] | None) -> dict[str, Any]:
    """The smallest value each required param's own schema admits."""
    schema = schema or {}
    props = schema.get("properties") or {}
    out: dict[str, Any] = {}
    for name in schema.get("required") or []:
        spec = props.get(name) or {}
        if "default" in spec:
            out[name] = spec["default"]
            continue
        if "enum" in spec:
            out[name] = spec["enum"][0]
            continue
        if "pattern" in spec:
            # A pattern the synthetic value cannot satisfy is answered with a
            # 400 naming the field, which step 5 accepts as a structured
            # refusal rather than a panel failure.
            out[name] = "0" * 32
            continue
        kind = spec.get("type")
        if isinstance(kind, list):
            kind = kind[0]
        out[name] = _SYNTH.get(kind, "x")
    return out


def check_every_panel(base: str) -> list[str]:
    """Steps 5 and 6. Returns one status line per script."""
    status, body = _get(f"{base}/api/panels")
    if status != 200:
        raise CheckFailed(f"/api/panels returned {status}: {body}")
    scripts = body.get("scripts") or []
    if not scripts:
        raise CheckFailed("/api/panels listed no scripts at all")
    print(f"panels: {len(scripts)} registered scripts to check")

    lines: list[str] = []
    failures: list[str] = []
    for script in sorted(scripts, key=lambda s: s["name"]):
        name = script["name"]
        params = synth_params(script.get("params_schema"))
        code, payload = _post(f"{base}/api/scripts/{name}/run",
                              {"params": params})
        if code == 200:
            result = (payload or {}).get("result") or {}
            if result.get("empty") is True:
                shape = f"gap: {str(result.get('reason'))[:70]}"
            else:
                shape = f"payload, {len(result)} keys"
        elif code == 400:
            shape = f"400 params refused: {str(payload.get('detail'))[:70]}"
        else:
            shape = f"HTTP {code} {json.dumps(payload)[:160]}"
            failures.append(f"{name}: {shape}")
        # Step 6: the broken-panel shape must never appear.
        if isinstance(payload, dict) and payload.get("error") is True:
            failures.append(
                f"{name}: returned the BROKEN-panel shape "
                f"{{error, panel, reason}}, which is distinct from the honest "
                f"{{empty, reason}} gap: {payload.get('reason')}")
        lines.append(f"  {code} {name:<22} {shape}")

    for line in lines:
        print(line)
    if failures:
        raise CheckFailed("panels that did not return a structured answer:\n  "
                          + "\n  ".join(failures))
    return lines


def check_one_tick(volume: Path) -> None:
    """Step 7, run in-process against the same empty warehouse.

    In-process rather than in the container because the assertion is about the
    ledger row the tick writes, and reading it back means opening the same
    file the container just created. The container is stopped before this runs
    so there is one writer.
    """
    import os

    os.environ["FPL_EDGE_DISABLE_NETWORK_INGEST"] = "1"
    sys.path.insert(0, str(REPO_ROOT))
    from fpl_edge.platform.scheduler import Scheduler
    from fpl_edge.store.warehouse import Warehouse

    db = volume / "warehouse" / "fpl.duckdb"
    if not db.exists():
        raise CheckFailed(f"the container left no warehouse at {db}")
    scheduler = Scheduler(db)
    report = scheduler.run_one_tick()
    if report is None:
        raise CheckFailed("the tick was skipped as an overlap on a fresh "
                          "scheduler, which cannot happen")
    with Warehouse.read_copy(db) as wh:
        exists = int(wh.sql(
            "SELECT count(*) c FROM information_schema.tables "
            "WHERE table_name = 'fetch_run'").iloc[0]["c"])
        rows = wh.sql("SELECT pipeline, status, note FROM fetch_run "
                      "ORDER BY started_utc DESC") if exists else None
    if rows is None or rows.empty:
        raise CheckFailed(
            "one tick against the empty warehouse wrote no fetch_run row. A "
            "tick that records nothing is indistinguishable from a tick that "
            "never ran.")
    print(f"tick: {len(rows)} fetch_run rows written")
    for row in rows.head(20).itertuples(index=False):
        print(f"  {row.status:<10} {row.pipeline:<26} {str(row.note)[:70]}")
    statuses = set(rows["status"])
    if not statuses & {"no_source", "ok"}:
        raise CheckFailed(f"the tick wrote rows but none with a status that "
                          f"means it ran: {sorted(statuses)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--keep", action="store_true",
                    help="leave the container and the host directory in place")
    ap.add_argument("--skip-build", action="store_true",
                    help="use an image that is already built")
    args = ap.parse_args(argv)

    base = f"http://127.0.0.1:{args.port}"
    volume = Path(tempfile.mkdtemp(prefix="fpl-edge-deploy-check-"))
    container = ""
    try:
        if not args.skip_build:
            build_image(args.image)
        assert_image_contents(args.image)
        print(f"empty volume: {volume}")
        container = start_container(args.image, volume, args.port)
        print(f"container: {container[:12]}")
        boot_s = wait_for_health(base, container)
        check_every_panel(base)
        _run(["docker", "stop", container], capture=True, check=False)
        container = ""
        check_one_tick(volume)
        print()
        print(f"deploy check passed. Boot took {boot_s:.1f}s against an empty "
              f"volume; set railway.toml healthcheckTimeout comfortably above "
              f"that.")
        return 0
    except CheckFailed as exc:
        print(f"\nDEPLOY CHECK FAILED\n{exc}", file=sys.stderr)
        if container:
            logs = _run(["docker", "logs", "--tail", "120", container],
                        capture=True, check=False)
            print((logs.stdout or "") + (logs.stderr or ""), file=sys.stderr)
        return 1
    finally:
        # Step 8.
        if container and not args.keep:
            _run(["docker", "stop", container], capture=True, check=False)
        if not args.keep:
            _run(["docker", "rm", "-f", container] if container else ["true"],
                 capture=True, check=False)
            shutil.rmtree(volume, ignore_errors=True)
        else:
            print(f"kept: container {container[:12]}, volume {volume}")


if __name__ == "__main__":
    raise SystemExit(main())
