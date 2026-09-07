"""Start and observe `fpl solve` runs from the platform, without owning them.

The solve is minutes of MILP that takes the DuckDB write lock when it commits
its artefacts. Two rules follow, and everything in this module exists to keep
them:

* **The runner holds no warehouse handle.** The subprocess is the CLI and does
  its own locking exactly as a terminal invocation would; this module only
  touches files under ``data/warehouse/jobs/`` (a log per run, one status
  file). A read of the DuckDB from here would put a second handle on the
  single-writer store for no benefit.
* **At most one solve at a time.** A second POST while one runs returns the
  running status and spawns nothing. Liveness is checked against the recorded
  pid, not the status file's word for it -- a server restart or a kill -9
  leaves a stale "running" on disk, and trusting it would wedge the button
  forever.

The child is detached (its own session) and the server never waits on it, so
completion cannot be observed via an exit status the POSIX way. Instead the
command is wrapped in ``sh -c '... ; echo $? > <log>.exit'``: the exit file is
the durable completion record, written by the child's shell whether or not
this server is still alive to see it. ``status()`` reconciles the status file
against that exit file and the pid on every read.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

UTC = dt.UTC

REPO_ROOT = Path(__file__).resolve().parents[2]
JOBS_DIR = REPO_ROOT / "data" / "warehouse" / "jobs"

MODES = ("both", "rank", "points", "transfers")
LOG_TAIL_LINES = 30

#: The four chips, in the optimiser's own vocabulary (OptimizerConfig.allowed_chips).
CHIPS = ("wildcard", "freehit", "bboost", "3xc")

#: What the Planner's solve rail sends when the user touches nothing. These are
#: the settings that solved the GW4-8 problem (2026-09-07): 60s per MILP found
#: no incumbent; 150s x 20 candidates solved all nine moves in 219-238s. Chips
#: off because a chip is the owner's decision, not the objective's (allowed,
#: the first run wildcarded for +51). Hits capped at 0 for the headline; the
#: optimiser's hit-taking best still rides along as `unconstrained`.
TRANSFER_DEFAULTS: dict[str, Any] = {
    "horizon": 5,
    "max_hits": 0,
    "chips": (),
    "must_keep": (),
    "ban": (),
    "seconds": 150,
    "max_candidates": 20,
}
_OPTION_BOUNDS = {
    "horizon": (1, 8),
    "max_hits": (-1, 8),
    "seconds": (10, 900),
    "max_candidates": (5, 80),
}


def _int_list(name: str, value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a list of player codes, not a string")
    try:
        codes = tuple(sorted({int(v) for v in value}))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a list of integer player codes") from exc
    if any(c <= 0 for c in codes):
        raise ValueError(f"{name} carries a non-positive player code")
    if len(codes) > 30:
        raise ValueError(f"{name} lists more than 30 players")
    return codes


def normalise_options(options: Mapping[str, Any] | None) -> dict[str, Any]:
    """The transfers-mode options, validated and filled with the defaults.

    Raises ``ValueError`` with a message fit for a 400: the argv is built from
    these, so nothing unvalidated may reach the shell, and a bad request must
    be refused before a five-minute process is spawned for it.
    """
    opts = dict(TRANSFER_DEFAULTS)
    if not options:
        return opts
    unknown = set(options) - set(TRANSFER_DEFAULTS)
    if unknown:
        raise ValueError(
            f"unknown solve option(s) {sorted(unknown)}; "
            f"known: {sorted(TRANSFER_DEFAULTS)}"
        )
    for key, (lo, hi) in _OPTION_BOUNDS.items():
        if key in options and options[key] is not None:
            try:
                v = int(options[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be an integer") from exc
            if not lo <= v <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}, not {v}")
            opts[key] = v
    if options.get("chips") is not None:
        chips = options["chips"]
        if isinstance(chips, (str, bytes)):
            raise ValueError("chips must be a list of chip names, not a string")
        bad = [c for c in chips if c not in CHIPS]
        if bad:
            raise ValueError(f"unknown chip(s) {bad}; known: {list(CHIPS)}")
        opts["chips"] = tuple(c for c in CHIPS if c in set(chips))
    opts["must_keep"] = _int_list("must_keep", options.get("must_keep"))
    opts["ban"] = _int_list("ban", options.get("ban"))
    overlap = set(opts["must_keep"]) & set(opts["ban"])
    if overlap:
        raise ValueError(f"players both kept and banned: {sorted(overlap)}")
    return opts


def transfers_command(options: Mapping[str, Any] | None = None) -> str:
    """The `fpl recommend` argv for a set of (normalised) rail options."""
    o = normalise_options(options)
    argv = [
        "uv", "run", "fpl", "recommend", "--commit",
        "--seconds", str(o["seconds"]),
        "--max-candidates", str(o["max_candidates"]),
        "--max-hits", str(o["max_hits"]),
        "--horizon", str(o["horizon"]),
    ]
    if o["chips"]:
        argv.append("--chips")
        for c in o["chips"]:
            argv += ["--chip", c]
    else:
        argv.append("--no-chips")
    if o["must_keep"]:
        argv += ["--must-keep", ",".join(str(c) for c in o["must_keep"])]
    if o["ban"]:
        argv += ["--ban", ",".join(str(c) for c in o["ban"])]
    return shlex.join(argv)


def _status_path(jobs_dir: Path) -> Path:
    return jobs_dir / "solve_status.json"


def _now_iso() -> str:
    return dt.datetime.now(UTC).isoformat()


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists but is not ours to signal; that is still alive.
        return True
    except (ValueError, OSError):
        return False
    return True


def _tail(log_path: Path, lines: int = LOG_TAIL_LINES) -> list[str]:
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def _write_status(jobs_dir: Path, status: dict[str, Any]) -> None:
    """Atomic write: a poll must never read a half-written file."""
    path = _status_path(jobs_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, indent=2))
    tmp.replace(path)


def _read_status(jobs_dir: Path) -> dict[str, Any] | None:
    path = _status_path(jobs_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _reconcile(jobs_dir: Path, status: dict[str, Any]) -> dict[str, Any]:
    """Bring a stored status up to date with reality, persisting any change.

    Precedence matters: the exit file beats pid liveness (the shell writes it
    in its dying breath, so "exit file present, pid still up" is just the
    shell exiting), and pid liveness beats the file's own claim of "running".
    """
    if status.get("state") != "running":
        return status

    log_path = Path(status.get("log_path", ""))
    exit_path = Path(status.get("exit_path", str(log_path) + ".exit"))

    exit_code: int | None = None
    if exit_path.exists():
        try:
            exit_code = int(exit_path.read_text().strip() or 1)
        except (OSError, ValueError):
            exit_code = 1

    if exit_code is not None:
        status["state"] = "done" if exit_code == 0 else "failed"
        status["exit_code"] = exit_code
        status["finished_utc"] = dt.datetime.fromtimestamp(
            exit_path.stat().st_mtime, UTC
        ).isoformat()
        status["log_tail"] = _tail(log_path)
        _write_status(jobs_dir, status)
    elif not _pid_alive(status.get("pid")):
        status["state"] = "failed"
        status["exit_code"] = None
        status["finished_utc"] = _now_iso()
        status["log_tail"] = _tail(log_path)
        status["reason"] = (
            "the solve process is gone without recording an exit code "
            "(killed, or the machine restarted mid-solve)"
        )
        _write_status(jobs_dir, status)
    else:
        # Genuinely running: refresh the tail for the poller, in memory only --
        # rewriting the file every 3s poll buys nothing.
        status["log_tail"] = _tail(log_path)
    return status


def status(*, jobs_dir: Path = JOBS_DIR) -> dict[str, Any]:
    """The current (reconciled) solve status. Never raises on a bad file."""
    stored = _read_status(jobs_dir)
    if stored is None:
        return {"state": "idle", "mode": None, "started_utc": None,
                "finished_utc": None, "log_tail": [], "pid": None}
    return _reconcile(jobs_dir, stored)


def _default_command(mode: str,
                     options: Mapping[str, Any] | None = None) -> str:
    # "transfers" is `fpl recommend`: the current-squad transfer plan, not the
    # from-scratch ideal-squad solve. Same runner, same single-flight rules.
    # The Planner's rail options map onto the CLI's own flags; with none given
    # the defaults above apply. The forecast it reads is refreshed daily by
    # forecast_refresh, never here.
    if mode == "transfers":
        return transfers_command(options)
    return f"uv run fpl solve --mode {shlex.quote(mode)}"


def start(mode: str, *, jobs_dir: Path = JOBS_DIR,
          command: str | None = None,
          options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Start a solve, or return the running one. Exactly one at a time.

    ``options`` are the Planner rail's transfers-mode settings (see
    :func:`normalise_options`); they are validated before anything is spawned
    and recorded in the status file so a poller can say what is being solved.
    ``command`` is injectable so tests can run a harmless sleep instead of a
    five-minute MILP; production callers pass only ``mode`` and ``options``.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    opts = normalise_options(options) if mode == "transfers" else None

    current = status(jobs_dir=jobs_dir)
    if current["state"] == "running":
        current["already_running"] = True
        return current

    jobs_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = jobs_dir / f"solve_{ts}.log"
    exit_path = Path(str(log_path) + ".exit")
    pid_path = Path(str(log_path) + ".pid")
    cmd = command if command is not None else _default_command(mode, opts)

    # A true double-fork detach, not just start_new_session: the wrapper shell
    # backgrounds the worker subshell (whose pid it records) and exits at
    # once, and we reap the wrapper here. The worker is reparented to init.
    # Without this the worker stays OUR child; if it is killed it lingers as a
    # zombie this server never waits on, os.kill(pid, 0) keeps "succeeding",
    # and the single-flight check wedges on a corpse.
    shell = (
        f"({cmd} >> {shlex.quote(str(log_path))} 2>&1; "
        f"echo $? > {shlex.quote(str(exit_path))}) & "
        f"echo $! > {shlex.quote(str(pid_path))}"
    )
    log_path.write_text(f"$ {cmd}\n")
    proc = subprocess.Popen(  # cmd is ours (mode validated), paths are quoted
        ["/bin/sh", "-c", shell],
        cwd=str(REPO_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    proc.wait()  # instant: the wrapper only forks and writes the pid file
    try:
        pid = int(pid_path.read_text().strip())
    except (OSError, ValueError):
        pid = None  # worker pid unknown; the exit file still records the end

    fresh = {
        "state": "running",
        "mode": mode,
        "options": None if opts is None else {
            k: (list(v) if isinstance(v, tuple) else v) for k, v in opts.items()
        },
        "started_utc": _now_iso(),
        "finished_utc": None,
        "pid": pid,
        "log_path": str(log_path),
        "exit_path": str(exit_path),
        "log_tail": _tail(log_path),
    }
    _write_status(jobs_dir, fresh)
    return fresh
