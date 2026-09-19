"""Suite-wide guards.

FPL_EDGE_DISABLE_PRIVATE is set for every test because report/CLI code paths
construct their own PrivateTeamClient: without the guard, a unit test once
refreshed the developer's REAL FPL tokens over the network as a side effect of
rendering a report fixture. Tests that exercise the private path explicitly
inject fakes and are unaffected.

The second guard watches every path under ``data/warehouse``. That relative
path is the owner's live warehouse directory in the main checkout: the 160 MB
DuckDB file, and beside it the artefacts the panels read. A test that opens
``fpl.duckdb`` for writing takes DuckDB's single writer lock on real data and
runs schema.sql, the additive column migrations and views.sql against it; in a
fresh worktree the same bug is visible as a 2.3 MB schema-only file appearing
in the repo root.

The artefacts are the same class of accident with no lock to make it loud. At
07:31 UTC on 2026-09-19, two minutes into a full-suite gate, the live
``forecast.parquet`` and ``forecast.meta.json`` were rewritten as an
engine-only forecast with no ``fetch_run`` row, replacing the consensus
forecast the scheduled ``forecast_refresh`` had written the evening before.
The dashboard then recommended captaining a 4.5 percent owned midfielder off
those numbers. A guard that watched only the database file would not have seen
it, so this one watches the directory: any path that appeared, changed size or
mtime, or was removed.

The guard stats paths and never opens one, so it cannot become the thing it is
watching for, and it is satisfied by a read-only read, which changes neither
size nor mtime.

The third and fourth guards are about identity. ``fpl_edge.config.secret``
reads the environment first and the repo's ``.env`` second, and the owner sets
Google sign-in up in that file for local development (DEPLOYMENT.md 13.7).
Without ``FPL_EDGE_ANON_IS_OWNER`` pinned here, the suite's answer to "who is
this request" would depend on whether the developer had configured sign-in,
and every test that builds an app would start answering 401 on the machine
where they had. ``AUTH_DB`` is pinned to a temp path for the same reason the
warehouse is watched: a test that stored a session or a key would otherwise
leave a SQLite file in the repo tree. Tests that exercise sign-in set both
themselves with ``monkeypatch.setenv``.
"""

import os
import tempfile
import warnings
from pathlib import Path

import pytest

os.environ.setdefault("FPL_EDGE_DISABLE_PRIVATE", "1")
os.environ.setdefault("FPL_EDGE_ANON_IS_OWNER", "1")
os.environ.setdefault(
    "AUTH_DB",
    str(Path(tempfile.mkdtemp(prefix="fpl-edge-auth-test-")) / "auth.sqlite3"),
)

#: The warehouse directory, relative exactly as the production code resolves
#: it (``fpl_edge.store.warehouse.DEFAULT_DB``'s parent). Named literally
#: rather than imported, so importing the store is not a precondition for the
#: guard.
WAREHOUSE_DIR = Path("data/warehouse")

#: The file inside it whose mutation is worst, named so the failure message
#: can say which accident this was.
LIVE_DB = WAREHOUSE_DIR / "fpl.duckdb"

#: What a directory's fingerprint is. Directories are fingerprinted too, so a
#: test that creates an empty one (``data/warehouse/chat/assets/``, which
#: every ``create_app(db)`` used to make) is caught as well.
_DIR = (-1, -1)


def _fingerprint() -> dict[str, tuple[int, int]]:
    """{path: (size, mtime_ns)} for everything under the warehouse directory.

    ``os.scandir`` and ``os.stat`` only. Opening a file to inspect it would be
    the very thing this guard exists to catch. An unreadable entry is skipped
    rather than raised: the guard must never be able to fail a run by itself.
    """
    out: dict[str, tuple[int, int]] = {}
    stack = [WAREHOUSE_DIR]
    if not WAREHOUSE_DIR.exists():
        return out
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    out[entry.path] = _DIR
                    stack.append(Path(entry.path))
                else:
                    stat = entry.stat(follow_symlinks=False)
                    out[entry.path] = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                continue
    return out


def _changes(before: dict[str, tuple[int, int]],
             after: dict[str, tuple[int, int]]) -> list[str]:
    """Every path that appeared, changed size or mtime, or was removed."""
    out = []
    for path in sorted(set(before) | set(after)):
        was, now = before.get(path), after.get(path)
        if was == now:
            continue
        if was is None:
            out.append(f"appeared: {path}")
        elif now is None:
            out.append(f"removed: {path}")
        else:
            out.append(f"changed: {path} {was!r} -> {now!r}")
    return out


#: Taken when this conftest is imported, which is before any test module is
#: imported, so an import-time side effect is caught too.
_BASELINE = _fingerprint()
_LAST_SEEN = [_BASELINE]
_RAN_SINCE_CHECK: list[str] = []
_OFFENCES: list[str] = []

#: How many changed paths one offence line names before it is elided. A test
#: that rewrote the whole directory should not print a thousand lines.
_MAX_PATHS = 12


def pytest_runtest_logfinish(nodeid, location):
    """Compare the fingerprint after every test and name what moved it."""
    del location
    _RAN_SINCE_CHECK.append(nodeid)
    current = _fingerprint()
    if current != _LAST_SEEN[0]:
        moved = _changes(_LAST_SEEN[0], current)
        shown = moved[:_MAX_PATHS]
        if len(moved) > _MAX_PATHS:
            shown.append(f"(+{len(moved) - _MAX_PATHS} more)")
        _OFFENCES.append(
            "; ".join(shown) + " after: " + ", ".join(_RAN_SINCE_CHECK)
        )
        _LAST_SEEN[0] = current
    _RAN_SINCE_CHECK.clear()


@pytest.fixture(scope="session", autouse=True)
def live_warehouse_is_never_touched():
    """Fail the session if anything under the warehouse directory moved."""
    yield
    final = _fingerprint()
    if not _OFFENCES and final == _BASELINE:
        return
    detail = "\n  ".join(_OFFENCES) or "\n  ".join(_changes(_BASELINE, final))
    message = (
        f"paths under {WAREHOUSE_DIR} appeared, changed or were removed "
        f"during the run. In the main checkout that directory is the live "
        f"warehouse and the artefacts the panels read. Either a test wrote "
        f"one of them (a test's warehouse and its artefacts belong under "
        f"tmp_path, and {LIVE_DB} is refused for writing under pytest by "
        f"Warehouse.__init__), or a server, scheduler or ingest step wrote "
        f"while the suite ran, in which case the ids below are whoever "
        f"finished next and not the writer.\n"
        f"  {detail}"
    )
    # On the owner's Mac the server and its scheduler write this directory all
    # day, so a change during a suite is often not a test's doing and cannot
    # be attributed by timing. The per-process refusal in Warehouse.__init__
    # is the enforcement for the database; this check is strict only where
    # nothing else writes the directory (make test, CI), advisory everywhere
    # else.
    if os.environ.get("FPL_EDGE_GUARD_LIVE_DB") == "strict":
        pytest.fail(message, pytrace=False)
    warnings.warn(message, stacklevel=1)
