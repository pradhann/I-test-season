"""The build job, which a panel must never call.

Separated from the panel modules because brief rule 5 makes a panel that writes
an artefact a bug. `fixture_ratings_refit` runs this, the panels only read what
it leaves behind."""

from __future__ import annotations

import argparse
import datetime as dt
import time
from pathlib import Path
from typing import Any

from fpl_edge.platform.scripts.fixtures.constants import (
    CALIBRATION_NAME,
    RATINGS_NAME,
    SEASON_DEFAULT,
)
from fpl_edge.platform.scripts.fixtures.ratings import build_board_ratings, build_calibration

# ---------------------------------------------------------------------------
# the build job -- NEVER called by a panel
# ---------------------------------------------------------------------------


def write_artefacts(
    db_path: Path | str | None = None, *, season: str = SEASON_DEFAULT,
    out_dir: Path | str | None = None, now: dt.datetime | None = None,
    calibration: bool = True,
) -> dict[str, Any]:
    """Fit from a private read copy and overwrite both cached artefacts.

    ``read_copy`` keeps the fit off the live file so ingest writers are never
    blocked -- DuckDB is one-writer-XOR-many-readers and the Telegram bot holds
    write leases between polls. Overwriting is correct: these are caches, not
    append-only facts, and yesterday's ratings for fixtures that have since
    kicked off are not history worth keeping.
    """
    from fpl_edge.store import DEFAULT_DB, Warehouse

    db_path = Path(db_path) if db_path is not None else Path(DEFAULT_DB)
    out = Path(out_dir) if out_dir is not None else db_path.parent
    report: dict[str, Any] = {}
    with Warehouse.read_copy(db_path) as wh:
        t0 = time.monotonic()
        ratings = build_board_ratings(wh, season=season, now=now)
        report["ratings_rows"] = len(ratings)
        report["ratings_seconds"] = round(time.monotonic() - t0, 2)
        ratings.to_parquet(out / RATINGS_NAME, index=False)
        report["ratings_path"] = str(out / RATINGS_NAME)
        if calibration:
            t1 = time.monotonic()
            calib = build_calibration(wh)
            report["calibration_rows"] = len(calib)
            report["calibration_seconds"] = round(time.monotonic() - t1, 2)
            calib.to_parquet(out / CALIBRATION_NAME, index=False)
            report["calibration_path"] = str(out / CALIBRATION_NAME)
    return report


def main(argv: list[str] | None = None) -> int:
    from fpl_edge.store import DEFAULT_DB

    parser = argparse.ArgumentParser(
        # Named explicitly: the package answers -m from __main__.py, and
        # argparse would otherwise prefix every error with "__main__.py".
        prog="python -m fpl_edge.platform.scripts.fixtures",
        description="Build the cached artefacts the fixtures panels read.")
    parser.add_argument("--build", action="store_true",
                        help="fit and write fixture_ratings.parquet (+ calibration)")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--season", default=SEASON_DEFAULT)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--no-calibration", action="store_true",
                        help="skip the four-season regression (it is the slow half)")
    args = parser.parse_args(argv)
    if not args.build:
        parser.error("nothing to do; pass --build")
    report = write_artefacts(
        args.db, season=args.season, out_dir=args.out_dir,
        calibration=not args.no_calibration,
    )
    for key, value in report.items():
        print(f"{key}: {value}")
    return 0






# The CLI guard lives at the very END of the module on purpose: `python -m`
# executes top to bottom, and a SystemExit raised mid-file would leave
# fixture_detail undefined and unregistered in that process.
if __name__ == "__main__":
    raise SystemExit(main())
