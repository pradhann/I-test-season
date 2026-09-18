"""Suite-wide guards.

FPL_EDGE_DISABLE_PRIVATE is set for every test because report/CLI code paths
construct their own PrivateTeamClient: without the guard, a unit test once
refreshed the developer's REAL FPL tokens over the network as a side effect of
rendering a report fixture. Tests that exercise the private path explicitly
inject fakes and are unaffected.

The second guard watches ``data/warehouse/fpl.duckdb``. That relative path is
the owner's live 160 MB warehouse in the main checkout, so a test that opens it
for writing takes DuckDB's single writer lock on real data and runs schema.sql,
the additive column migrations and views.sql against it. In a fresh worktree the
same bug is visible as a 2.3 MB schema-only file appearing in the repo root. The
guard stats the path, never opens it, so it cannot become the thing it is
watching for, and it is satisfied by a read-only audit read, which changes
neither size nor mtime.
"""

import os
import warnings
from pathlib import Path

import pytest

os.environ.setdefault("FPL_EDGE_DISABLE_PRIVATE", "1")

#: The default warehouse path, relative exactly as the production code resolves
#: it (``fpl_edge.store.warehouse.DEFAULT_DB``). Named literally rather than
#: imported, so importing the store is not a precondition for the guard.
LIVE_DB = Path("data/warehouse/fpl.duckdb")


def _fingerprint() -> tuple[int, int] | None:
    """(size, mtime_ns) of the live warehouse, or None when it is absent.

    ``os.stat`` only. Opening the file to inspect it would be the very thing
    this guard exists to catch.
    """
    try:
        stat = LIVE_DB.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


#: Taken when this conftest is imported, which is before any test module is
#: imported, so an import-time side effect is caught too.
_BASELINE = _fingerprint()
_LAST_SEEN = [_BASELINE]
_RAN_SINCE_CHECK: list[str] = []
_OFFENCES: list[str] = []


def pytest_runtest_logfinish(nodeid, location):
    """Compare the fingerprint after every test and name what moved it."""
    del location
    _RAN_SINCE_CHECK.append(nodeid)
    current = _fingerprint()
    if current != _LAST_SEEN[0]:
        _OFFENCES.append(
            f"{_LAST_SEEN[0]!r} -> {current!r} after: "
            f"{', '.join(_RAN_SINCE_CHECK)}"
        )
        _LAST_SEEN[0] = current
    _RAN_SINCE_CHECK.clear()


@pytest.fixture(scope="session", autouse=True)
def live_warehouse_is_never_touched():
    """Fail the session if the default warehouse appeared or changed."""
    yield
    final = _fingerprint()
    if not _OFFENCES and final == _BASELINE:
        return
    detail = "\n  ".join(_OFFENCES) or f"{_BASELINE!r} -> {final!r}"
    message = (
        f"{LIVE_DB} appeared or changed during the run. In the main checkout "
        f"that path is the live warehouse. Either a test opened the owner's "
        f"real database (Warehouse.__init__ refuses that under pytest, so it "
        f"would have to be a read_only or raw duckdb open), or a server, "
        f"scheduler or ingest step wrote it while the suite ran, in which case "
        f"the ids below are whoever finished next and not the writer.\n"
        f"  {detail}"
    )
    # On the owner's Mac the server and its scheduler write the live file all
    # day, so a size or mtime change during a suite is usually not a test's
    # doing and cannot be attributed by timing. The per-process refusal in
    # Warehouse.__init__ is the enforcement; this check is strict only where
    # nothing else writes the file (make test, CI), advisory everywhere else.
    if os.environ.get("FPL_EDGE_GUARD_LIVE_DB") == "strict":
        pytest.fail(message, pytrace=False)
    warnings.warn(message, stacklevel=1)
