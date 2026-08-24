"""The solve runner's one promise: exactly one solve, honestly reported.

These tests never run a real solve -- the ``command`` injection point runs a
harmless sleep (a stand-in for minutes of MILP) or a trivially exiting shell
line. What they hold is the contract the button in the web UI depends on:
a second POST attaches to the running solve instead of spawning a rival
process, the status file has the shape the poller reads, and a pid that died
without writing its exit record is reported as failed rather than "running"
forever.
"""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import pytest

from fpl_edge.platform import solve_runner


def _wait_for(predicate, timeout: float = 10.0, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _kill_quietly(pid: int | None) -> None:
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


@pytest.fixture
def jobs_dir(tmp_path: Path):
    d = tmp_path / "jobs"
    yield d
    # Never leave a fake solve sleeping past the test.
    status = solve_runner._read_status(d)
    if status:
        _kill_quietly(status.get("pid"))


def test_single_flight_second_start_attaches_not_spawns(jobs_dir: Path) -> None:
    first = solve_runner.start("both", jobs_dir=jobs_dir, command="sleep 60")
    assert first["state"] == "running"
    logs_after_first = sorted(jobs_dir.glob("solve_*.log"))

    second = solve_runner.start("rank", jobs_dir=jobs_dir, command="sleep 60")

    assert second["state"] == "running"
    assert second.get("already_running") is True
    # Attached to the SAME run: same pid, same mode, and -- the part that
    # proves no spawn happened -- no new log file appeared.
    assert second["pid"] == first["pid"]
    assert second["mode"] == "both", "the running solve's mode wins, not the request's"
    assert sorted(jobs_dir.glob("solve_*.log")) == logs_after_first


def test_status_file_shape(jobs_dir: Path) -> None:
    solve_runner.start("points", jobs_dir=jobs_dir, command="sleep 60")

    on_disk = json.loads((jobs_dir / "solve_status.json").read_text())
    for key in ("state", "mode", "started_utc", "finished_utc", "pid",
                "log_path", "log_tail"):
        assert key in on_disk, f"status file is missing {key!r}"
    assert on_disk["state"] == "running"
    assert on_disk["mode"] == "points"
    assert isinstance(on_disk["log_tail"], list)
    assert on_disk["finished_utc"] is None

    live = solve_runner.status(jobs_dir=jobs_dir)
    assert live["state"] == "running"
    assert live["pid"] == on_disk["pid"]


def test_completion_is_read_from_the_exit_file(jobs_dir: Path) -> None:
    started = solve_runner.start("both", jobs_dir=jobs_dir, command="echo solved; true")
    assert _wait_for(lambda: Path(started["exit_path"]).exists())

    done = solve_runner.status(jobs_dir=jobs_dir)
    assert done["state"] == "done"
    assert done["exit_code"] == 0
    assert done["finished_utc"] is not None
    assert any("solved" in line for line in done["log_tail"])


def test_nonzero_exit_is_failed(jobs_dir: Path) -> None:
    started = solve_runner.start("both", jobs_dir=jobs_dir, command="sh -c 'exit 3'")
    assert _wait_for(lambda: Path(started["exit_path"]).exists())

    failed = solve_runner.status(jobs_dir=jobs_dir)
    assert failed["state"] == "failed"
    assert failed["exit_code"] == 3


def test_dead_pid_without_exit_record_is_failed_and_unwedges(jobs_dir: Path) -> None:
    """kill -9 mid-solve must not leave the button wedged on 'running'."""
    started = solve_runner.start("both", jobs_dir=jobs_dir, command="sleep 60")
    os.kill(started["pid"], signal.SIGKILL)
    assert _wait_for(lambda: not solve_runner._pid_alive(started["pid"]))

    after = solve_runner.status(jobs_dir=jobs_dir)
    assert after["state"] == "failed"
    assert after["exit_code"] is None
    assert "without recording an exit code" in after.get("reason", "")

    # And the slot is free again: a new start actually spawns.
    fresh = solve_runner.start("rank", jobs_dir=jobs_dir, command="sleep 60")
    assert fresh["state"] == "running"
    assert fresh.get("already_running") is None
    assert fresh["pid"] != started["pid"]


def test_bad_mode_is_rejected(jobs_dir: Path) -> None:
    with pytest.raises(ValueError):
        solve_runner.start("fastest", jobs_dir=jobs_dir, command="true")
    assert not (jobs_dir / "solve_status.json").exists()
