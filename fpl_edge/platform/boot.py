"""The boot sequence a container runs before it answers a single request.

DEPLOYMENT.md §7, in order. Each step either succeeds or fails the boot with a
message naming the path it was working on. None of them guesses, and none of
them rebuilds an artefact: a boot that blocked on a model fit would turn every
restart into a multi-minute outage, so absence is measured and reported and the
scheduler's first tick rebuilds what its tasks own.

The steps
---------

1. **Mount check.** The data directory exists, is a directory, and takes a
   probe file. A missing volume is a hard failure rather than a fall back to
   the container layer, where the service would look healthy, accept writes
   and lose them on the next restart.
2. **Empty the temp directory.** ``Warehouse.read_copy`` copies the whole
   database file per heavy read and its finalizer has already failed in the
   field (457 orphaned directories, 5.1 GB, found on one machine). No read
   copy legitimately survives a restart.
3. **Open the warehouse as a writer once.** The constructor creates the file,
   applies ``store/schema.sql``, runs the additive column migrations and
   applies ``store/views.sql``. This is also where a WAL left by a killed
   container is replayed, so the step ends with a ``CHECKPOINT`` and a failure
   here names the WAL path. An empty volume comes out of this step with a
   schema-only warehouse, which is what lets every panel answer with a
   structured gap instead of a stack trace.
4. **Apply the per-package migrations.** They are lazy today, applied at first
   use, so a migration failure surfaces as a 500 on whichever request happened
   to touch it first. Running them here makes it a boot failure instead.
5. **Seed from the image, never overwriting.** ``/app/seed/data`` holds the
   git-tracked files under ``data/``, which the volume mount would otherwise
   hide. A file is copied only when the target is absent, so a first boot
   lands the committed artefacts and every later boot leaves the live ones
   alone.
6. **Artefact presence check.** Present or absent, with mtime. Nothing is
   created and nothing is fabricated. The result rides into the health payload
   so an operator can see that, for example, ``forecast.parquet`` is absent
   and the Planner will report an honest empty state until the next
   ``forecast_refresh``.

Steps 7 and 8, starting the scheduler and flipping health to ready, belong to
``create_app``: the loop needs a running event loop and the health route needs
the report this module returns.
"""

from __future__ import annotations

import datetime as dt
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

UTC = dt.UTC

#: Where the volume is mounted in the image, and the default this module works
#: against. Every path constant in the repo is relative to the working
#: directory, so with ``WORKDIR /app`` and the volume here they all resolve
#: onto the volume with no code change.
DEFAULT_DATA_DIR = Path("data")

#: The read-only copy of the git-tracked files under ``data/`` that the image
#: carries, because the volume mount hides the ones in the repo tree.
DEFAULT_SEED_DIR = Path("/app/seed/data")

#: The artefacts panels read as ``source_dir(wh) / NAME``. Presence only.
ARTEFACTS: tuple[str, ...] = (
    "fixture_ratings.parquet",
    "fixture_calibration.parquet",
    "fixture_difficulty.parquet",
    "forecast.parquet",
    "forecast.meta.json",
    "gw1_projection.parquet",
    "gw1_plan.json",
    "transfer_plan.json",
    "briefing_intel.json",
    "elite_list.json",
    "retro_report.html",
)

#: Boot writes and deletes this to prove the mount takes writes.
PROBE_NAME = ".boot_probe"


class BootFailure(RuntimeError):
    """A boot step that must not be served past. The message names the path."""


@dataclass
class BootReport:
    """What boot found and did. Serialised into the health payload."""

    data_dir: str
    db_path: str
    mounted: bool = False
    writable: bool = False
    free_bytes: int | None = None
    tmp_cleared: int = 0
    warehouse_present: bool = False
    migrations: dict[str, str] = field(default_factory=dict)
    seeded: list[str] = field(default_factory=list)
    artefacts: dict[str, Any] = field(default_factory=dict)
    booted_utc: str | None = None

    @property
    def ok(self) -> bool:
        """Steps 1 to 6 all completed. Drives the health status code."""
        return (
            self.mounted
            and self.writable
            and self.warehouse_present
            and all(v == "ok" for v in self.migrations.values())
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "booted_utc": self.booted_utc,
            "volume": {
                "path": self.data_dir,
                "mounted": self.mounted,
                "writable": self.writable,
                "free_bytes": self.free_bytes,
                "tmp_cleared": self.tmp_cleared,
            },
            "warehouse": self.db_path,
            "warehouse_present": self.warehouse_present,
            "migrations": dict(self.migrations),
            "seeded": list(self.seeded),
            "artefacts": dict(self.artefacts),
        }


#: The last report this process produced, for the health route to read. A
#: module global rather than app state because boot may be run from the CMD
#: before any app object exists.
LAST_REPORT: BootReport | None = None


def check_mount(data_dir: Path, report: BootReport) -> None:
    """Step 1. Exists, is a directory, and takes a write."""
    if data_dir.exists() and not data_dir.is_dir():
        raise BootFailure(f"{data_dir} exists and is not a directory")
    data_dir.mkdir(parents=True, exist_ok=True)
    report.mounted = data_dir.is_dir()
    probe = data_dir / PROBE_NAME
    try:
        probe.write_text(dt.datetime.now(UTC).isoformat())
        probe.unlink()
    except OSError as exc:
        raise BootFailure(
            f"{data_dir} is not writable: {type(exc).__name__}: {exc}. The "
            f"volume must be mounted here before the service serves anything, "
            f"because a container-layer fallback would accept writes and lose "
            f"them on the next restart."
        ) from exc
    report.writable = True
    try:
        usage = shutil.disk_usage(data_dir)
        report.free_bytes = int(usage.free)
    except OSError:
        report.free_bytes = None


def clear_tmp(data_dir: Path, report: BootReport) -> None:
    """Step 2. Create the read-copy scratch directory and empty it."""
    tmp = data_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    removed = 0
    for child in tmp.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
            removed += 1
        except OSError:
            continue
    report.tmp_cleared = removed


def open_warehouse_once(db_path: Path, report: BootReport) -> None:
    """Step 3. Schema, column migrations, views, then one CHECKPOINT."""
    from fpl_edge.store.warehouse import Warehouse

    wal = db_path.with_suffix(db_path.suffix + ".wal")
    try:
        with Warehouse(db_path) as wh:
            wh.sql("CHECKPOINT")
    except Exception as exc:  # noqa: BLE001 - re-raised as a boot failure
        raise BootFailure(
            f"could not open {db_path} as a writer: {type(exc).__name__}: "
            f"{exc}. A container killed mid-write leaves {wal}, and DuckDB "
            f"replays it on the next write open. If the replay is what "
            f"failed, quarantine that file before restarting."
        ) from exc
    report.warehouse_present = db_path.exists()


#: Step 4's sets, in the order they may be applied. The content set must run
#: after ``schema.sql`` because it references tables that file creates; the
#: rest are independent. Each set is idempotent and records its own versions.
def _migration_sets() -> list[tuple[str, Any]]:
    from fpl_edge.ingest.content import asr
    from fpl_edge.ingest.content.store import ContentStore
    from fpl_edge.jobs import deadline_dag

    def dag_and_outbox(wh) -> None:
        deadline_dag.apply_migrations(wh)

    def content(wh) -> None:
        ContentStore(wh)

    def transcript_provenance(wh) -> None:
        asr.ensure_schema(wh)

    def intel(wh) -> None:
        from fpl_edge.intel.store import IntelStore

        IntelStore(wh)

    def projections(wh) -> None:
        from fpl_edge.ingest.projections.store import ProjectionStore

        ProjectionStore(wh)

    def understat(wh) -> None:
        from fpl_edge.ingest.understat import UnderstatStore

        UnderstatStore(wh)

    return [
        ("dag_and_outbox", dag_and_outbox),
        ("content", content),
        ("transcript_provenance", transcript_provenance),
        ("intel", intel),
        ("projections", projections),
        ("understat", understat),
    ]


def apply_migrations(db_path: Path, report: BootReport) -> None:
    """Step 4. Every lazily-applied migration set, at boot, in one lease."""
    from fpl_edge.store.warehouse import Warehouse

    with Warehouse(db_path, lock_timeout_s=180.0) as wh:
        for name, fn in _migration_sets():
            try:
                fn(wh)
                report.migrations[name] = "ok"
            except Exception as exc:  # noqa: BLE001 - recorded, then raised
                report.migrations[name] = f"{type(exc).__name__}: {exc}"
        wh.sql("CHECKPOINT")
    failed = {k: v for k, v in report.migrations.items() if v != "ok"}
    if failed:
        raise BootFailure(
            f"migration sets failed against {db_path}: {failed}. Serving past "
            f"this would turn the failure into a 500 on whichever request "
            f"touched the missing table first."
        )


def seed_from_image(seed_dir: Path, data_dir: Path, report: BootReport) -> None:
    """Step 5. Copy what is absent, overwrite nothing."""
    if not seed_dir.is_dir():
        return
    for src in sorted(seed_dir.rglob("*")):
        if not src.is_file():
            continue
        target = data_dir / src.relative_to(seed_dir)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        report.seeded.append(str(target.relative_to(data_dir)))


def check_artefacts(db_path: Path, report: BootReport) -> None:
    """Step 6. Present or absent, with mtime. Nothing is created."""
    source = db_path.parent
    for name in ARTEFACTS:
        path = source / name
        if path.exists():
            report.artefacts[name] = {
                "present": True,
                "mtime_utc": dt.datetime.fromtimestamp(
                    path.stat().st_mtime, UTC).isoformat(),
                "bytes": path.stat().st_size,
            }
        else:
            report.artefacts[name] = {"present": False, "mtime_utc": None,
                                      "bytes": None}


def boot(
    data_dir: Path | str = DEFAULT_DATA_DIR,
    *,
    db_path: Path | str | None = None,
    seed_dir: Path | str = DEFAULT_SEED_DIR,
) -> BootReport:
    """Run steps 1 to 6 and return what they found.

    Raises :class:`BootFailure` on any step whose failure means serving a
    request would lose data or return a wrong answer.
    """
    global LAST_REPORT

    # One line in the startup log when a deployment still sets the old name
    # for this directory. See users.DEPRECATED_DATA_ROOT_ENV.
    from fpl_edge.platform.users import deprecated_data_root

    deprecated_data_root()

    data_dir = Path(data_dir)
    db = Path(db_path) if db_path is not None else data_dir / "warehouse" / "fpl.duckdb"
    report = BootReport(data_dir=str(data_dir), db_path=str(db))
    check_mount(data_dir, report)
    clear_tmp(data_dir, report)
    db.parent.mkdir(parents=True, exist_ok=True)
    open_warehouse_once(db, report)
    apply_migrations(db, report)
    seed_from_image(Path(seed_dir), data_dir, report)
    check_artefacts(db, report)
    report.booted_utc = dt.datetime.now(UTC).isoformat()
    LAST_REPORT = report
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Run the container boot sequence.")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--seed-dir", default=str(DEFAULT_SEED_DIR))
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    report = boot(args.data_dir, db_path=args.db, seed_dir=args.seed_dir)
    print(json.dumps(report.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    # Guarded, because the audit suite imports every module in the package and
    # a module-scope SystemExit would fire during that walk.
    raise SystemExit(main())
