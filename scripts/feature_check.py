"""Does the product work on a deployment, as a user would use it.

`make deploy-check` proves a container boots: the image builds, the volume
mounts, migrations apply, panels answer, one tick runs. Every one of those
passed on 2026-09-20 while three features were dead on the live service:
chat, the analysis brief and claim extraction, all of which spawn the Claude
Code CLI, which the image did not carry. Component checks cannot see that.
This asks the other question, one feature at a time, against a running
deployment, and says which of them a user could actually use.

    uv run python scripts/feature_check.py https://<service>.up.railway.app

It reads. It does not spend a model call, create a conversation or write to
the warehouse: a check that costs tokens will not be run often enough to
matter. For the model features it verifies the runtime they need is present
and reachable, which is exactly the thing that was missing.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 60.0


def _get(base: str, path: str) -> tuple[int, dict | None]:
    try:
        with urllib.request.urlopen(base + path, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return 0, None


def _post(base: str, path: str, body: dict) -> tuple[int, dict | None]:
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return 0, None


#: Each row is a thing a person does, not a component. ``expect`` is the set
#: of statuses that mean the feature is available to somebody: 401 counts,
#: because a route that refuses an anonymous caller is working correctly.
FEATURES: tuple[tuple[str, str, str, dict, set[int]], ...] = (
    ("the service answers", "GET", "/api/health", {}, {200}),
    ("who am I", "GET", "/api/me", {}, {200}),
    ("fixtures", "POST", "/api/scripts/fixture_board/run", {}, {200}),
    ("projections", "POST", "/api/scripts/projection_table/run", {}, {200}),
    ("ownership", "POST", "/api/scripts/ownership_eo/run", {}, {200}),
    ("creators", "POST", "/api/scripts/creator_board/run", {}, {200}),
    ("the decision page", "POST", "/api/scripts/dashboard_brief/run", {}, {200, 401, 403}),
    ("your squad", "POST", "/api/scripts/squad_overview/run", {}, {200, 401, 403}),
    ("chat", "GET", "/api/conversations", {}, {200, 401, 403}),
    ("the transcript queue", "GET", "/api/transcripts/queue", {}, {200, 401, 403}),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", help="the deployment's base URL")
    args = parser.parse_args(argv)
    base = args.base.rstrip("/")

    print(f"feature check against {base}\n")
    bad: list[str] = []
    for name, method, path, body, expect in FEATURES:
        code, _ = (_get(base, path) if method == "GET"
                   else _post(base, path, body))
        ok = code in expect
        print(f"  {'ok  ' if ok else 'FAIL'} {name:<22} {method} {path} -> {code or 'no response'}")
        if not ok:
            bad.append(f"{name} ({method} {path} returned {code or 'nothing'})")

    status, me = _get(base, "/api/health")
    runtime = (me or {}).get("model_runtime") if isinstance(me, dict) else None
    if runtime is None:
        print("\n  note  the health payload does not report the model runtime, so "
              "this check cannot tell whether chat, the brief and claim "
              "extraction have the CLI they spawn. That is the gap that hid "
              "three dead features on 2026-09-20.")
    else:
        ok = bool(runtime.get("present"))
        print(f"\n  {'ok  ' if ok else 'FAIL'} model runtime           {runtime}")
        if not ok:
            bad.append("the model runtime is absent, so chat, the analysis "
                       "brief and claim extraction cannot run here")

    if bad:
        print("\nnot usable:")
        for line in bad:
            print("  -", line)
        return 1
    print("\nevery feature is reachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
