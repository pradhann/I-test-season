"""Paid projection exports the owner drops on disk by hand.

Some of the best projections on the market have no API, no free tier worth
reading and a client bundle that was obfuscated on purpose. FPL Review is the
example: ``providers.py`` records the measurement that stopped the scraping
attempt, and the honest route it recommended was to take the data by hand. The
owner holds a paid account, clicks Export, and drops the CSV here::

    data/projections/<directory>/<prefix>_<unix_epoch>.csv

That directory is gitignored. The data is paid, it is private to the owner,
and nothing in this package republishes it.

Why the epoch in the filename is the whole provenance
-----------------------------------------------------
A hand export carries no fetch header, no sidecar and no publish stamp. The
only instant it can honestly claim is the moment the owner pulled it, and the
filename is where that instant lives. So the epoch IS ``as_of``, with the same
meaning it has for every other provider in this package: the instant the fact
became observable to us. A file whose name carries no parseable epoch is
refused and named in the run report, never ingested under "now" -- stamping a
hand drop with the ingest time would move a Tuesday export onto Friday's
deadline and leak a later opinion backwards into an earlier decision.

The element-id mapping is read at that same instant. ``ID`` in an FPL Review
export is the per-season element id, which FPL reassigns every August and
edits during a season, so the file is resolved through ``dim_player`` as the
roster stood at the export epoch, not as it stands today. An id that does not
resolve is dropped and counted, exactly as in :mod:`github_csv`.

Idempotency, twice over
-----------------------
``raw_fetch`` is the drop ledger: one row per ingested file, keyed by
``source = 'projections_<key>'``, ``endpoint = <filename>`` and ``sha256``.
A re-run lists the directory once, reads that ledger once, and skips every
file it already holds. Underneath, ``ProjectionStore.append`` anti-joins on
``(provider, season, gw, code, as_of)``, so even a ledger that was wiped
would write nothing new. A filename already in the ledger whose bytes have
changed is refused rather than ingested: the epoch names one instant, and two
different files claiming it is a contradiction the owner has to resolve by
re-exporting under a new epoch.

Adding a second provider, for example Solio Analytics
-----------------------------------------------------
1. Write ``solio_to_long(frame, key_column)`` returning columns
   ``key, gw, xp, xmins`` (one row per player per gameweek).
2. Add a :class:`LocalDrop` to :data:`DROPS` with the directory name, the
   filename regex (it must capture a group named ``epoch``), the key column
   and its kind, and that mapping function.
3. Add a :class:`fpl_edge.ingest.projections.providers.Provider` entry with
   the licence stance, so the source's terms are recorded next to every other
   source's.
4. Copy five synthetic rows into ``tests/fixtures/projections/`` and extend
   ``tests/unit/test_projections_local_csv.py``.
5. Nothing else: the CLI builds a step per entry in :data:`DROPS`, and
   ``sem_projections`` reads it through the same normalised view.

No second entry exists yet. One is not added on a description of a file; it
is added when a sample export is on disk and its columns have been read.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd

UTC = dt.UTC

#: Where the owner drops exports. Relative to the repo root, gitignored.
DROP_ROOT = Path("data/projections")

#: Epochs outside this range are refused. A millisecond epoch, a 4-digit year
#: typed by hand and a truncated paste all land far outside it, and all three
#: would otherwise become a confident and wrong ``as_of``.
EPOCH_MIN = dt.datetime(2020, 1, 1, tzinfo=UTC)
EPOCH_MAX = dt.datetime(2040, 1, 1, tzinfo=UTC)

KeyKind = Literal["player_code", "element_id"]

#: ``(frame, key_column) -> DataFrame[key, gw, xp, xmins]``.
LongMapper = Callable[[pd.DataFrame, str], pd.DataFrame]

#: ``(frame, key_column) -> DataFrame[key, metric, value]``, or None when the
#: export publishes no ownership-style number.
OwnershipMapper = Callable[[pd.DataFrame, str], pd.DataFrame]


class LocalDropError(RuntimeError):
    """The drop is not the shape this module knows how to read."""


@dataclass(frozen=True, slots=True)
class LocalDrop:
    """One paid provider whose export the owner lands on disk by hand."""

    key: str                        # provider key, as stored in fact_projection
    name: str                       # display name
    directory: str                  # data/projections/<directory>/
    filename_re: str                # must capture a group named 'epoch'
    key_column: str
    key_column_kind: KeyKind
    to_long: LongMapper
    licence: str
    cadence: str
    coverage: str
    #: Mapper for an ownership-style column, plus the metric name it writes
    #: into fact_external_ownership. Both None when the export has none.
    to_ownership: OwnershipMapper | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: Why this drop is no longer read, or None while it is live. Mirrors
    #: github_csv.Feed.retired: the entry stays so the judgement stays
    #: checkable, and sem_projection_retired() in store/views.sql must agree.
    retired: str | None = None

    @property
    def source(self) -> str:
        """The ``raw_fetch.source`` this provider's drop ledger lives under."""
        return f"projections_{self.key}"

    def directory_path(self, root: Path | None = None) -> Path:
        """The drop directory. ``root`` defaults to :data:`DROP_ROOT`.

        Resolved on each call rather than baked into a signature default, so
        a test can point the whole module at a tmp_path by rebinding the
        module constant and every caller follows.
        """
        return Path(DROP_ROOT if root is None else root) / self.directory


# ---------------------------------------------------------------------------
# FPL Review
# ---------------------------------------------------------------------------

#: An FPL Review export is wide: two columns per gameweek, named by the
#: gameweek itself (5_xMins, 5_Pts, ... 14_xMins, 14_Pts). The gameweek
#: numbers are read off the header rather than assumed to start at any
#: particular one, because the export starts at whatever gameweek is next
#: when the owner clicks Export.
_FPLREVIEW_GW_COLUMN = re.compile(r"^(?P<gw>\d{1,2})_(?P<field>xMins|Pts)$")


def fplreview_to_long(frame: pd.DataFrame, key_column: str) -> pd.DataFrame:
    """One wide FPL Review row per player becomes one row per gameweek.

    ``Pts`` is the number to ensemble: FPL Review's expected FPL points for
    that player in that gameweek, already weighted by its own minutes model,
    which is why it is stored as ``xp`` and never as ``xp_if_appears``.
    ``xMins`` is expected minutes in the units the export publishes them,
    0 to 90ish, so it lands in ``xmins`` and never in ``p_appear``.
    """
    pairs: dict[int, dict[str, str]] = {}
    for col in frame.columns:
        match = _FPLREVIEW_GW_COLUMN.match(str(col).strip())
        if match:
            pairs.setdefault(int(match.group("gw")), {})[match.group("field")] = col
    if not pairs:
        raise LocalDropError(
            "no <gw>_Pts / <gw>_xMins column pair in the export. Columns "
            f"present: {list(frame.columns)}. The export format has changed "
            "and the mapping must be re-read against the new file rather "
            "than filled with nulls."
        )
    blocks: list[pd.DataFrame] = []
    for gw in sorted(pairs):
        cols = pairs[gw]
        if "Pts" not in cols:
            raise LocalDropError(
                f"gameweek {gw} carries {sorted(cols)} but no Pts column, so "
                "there is no expected-points number to store for it."
            )
        blocks.append(pd.DataFrame({
            "key": frame[key_column].to_numpy(),
            "gw": gw,
            "xp": pd.to_numeric(frame[cols["Pts"]], errors="coerce").to_numpy(),
            "xmins": (pd.to_numeric(frame[cols["xMins"]], errors="coerce").to_numpy()
                      if "xMins" in cols else float("nan")),
        }))
    return pd.concat(blocks, ignore_index=True)


def fplreview_to_ownership(frame: pd.DataFrame, key_column: str) -> pd.DataFrame:
    """``Elite%`` becomes one ``own_elite`` row per player, as a 0-1 share.

    The metric name is the one migration 001 already defines for a share of
    an elite cohort. The cohort itself is FPL Review's, not LiveFPL's, and
    the two are told apart by ``provider`` rather than by inventing a second
    metric name for the same quantity.
    """
    if "Elite%" not in frame.columns:
        raise LocalDropError(
            f"no Elite% column in the export. Columns present: {list(frame.columns)}."
        )
    share = (frame["Elite%"].astype(str).str.strip().str.rstrip("%"))
    return pd.DataFrame({
        "key": frame[key_column].to_numpy(),
        "metric": "own_elite",
        "value": pd.to_numeric(share, errors="coerce").to_numpy() / 100.0,
    })


DROPS: tuple[LocalDrop, ...] = (
    LocalDrop(
        key="fplreview",
        name="FPLReview",
        directory="fplreview",
        filename_re=r"^fplreview_(?P<epoch>\d+)\.csv$",
        key_column="ID",
        key_column_kind="element_id",
        to_long=fplreview_to_long,
        to_ownership=fplreview_to_ownership,
        licence=(
            "PAID and PRIVATE. The owner holds a paid FPL Review account and "
            "exports the file by hand from the account he pays for. The "
            "bytes stay in this warehouse and on the owner's disk: nothing "
            "here republishes them, redistributes them, serves them to a "
            "third party or puts them behind any public surface. The site "
            "has no API and its client bundle is obfuscated, so no automated "
            "route is used or attempted; see the fplreview entry in "
            "providers.py for the measurement that settled that."
        ),
        cadence=(
            "Manual. The export happens when the owner does it, so the "
            "cadence is whatever he does and the provider goes stale like "
            "any other when he does not. The ingest never invents a fetch: "
            "no new file means no new rows and the as_of stays where the "
            "last export put it."
        ),
        coverage=(
            "Every element in the current season, ten gameweeks ahead, with "
            "xMins and xPts on every gameweek, plus Elite% ownership."
        ),
        notes=(
            ("Keys on `ID`, the per-season element id, mapped through "
             "dim_player at the export epoch. Ids above the live element "
             "range (youth and released players the export still carries) "
             "do not resolve and are dropped and counted."),
            ("BV and SV (buy value and sell value) are not stored. "
             "fact_projection carries no price column and fact_player_state "
             "already holds FPL's own price first-hand, so taking a second "
             "copy would put two prices for one player in the warehouse."),
        ),
    ),
)

BY_KEY: dict[str, LocalDrop] = {d.key: d for d in DROPS}


def live_drops() -> tuple[LocalDrop, ...]:
    """The drops an ingest run should read: everything not retired.

    A function rather than a module constant, for the same reason
    ``github_csv.live_feeds`` is one: a constant freezes the list at import
    time and breaks every test that substitutes :data:`DROPS`.
    """
    return tuple(d for d in DROPS if d.retired is None)


# ---------------------------------------------------------------------------
# scanning the drop directory
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DropFile:
    """One export on disk, with the instant its filename claims."""

    path: Path
    as_of: dt.datetime
    sha256: str


def epoch_from_name(drop: LocalDrop, filename: str) -> dt.datetime:
    """The export instant the filename carries, or a refusal that says why."""
    match = re.match(drop.filename_re, filename)
    if not match:
        raise LocalDropError(
            f"{filename}: no unix epoch in the filename. A drop for "
            f"{drop.key} must be named to match {drop.filename_re}, for "
            f"example {drop.directory}_1789744594.csv. The epoch is the only "
            f"as_of a hand export carries, so a file without one cannot be "
            f"stamped without inventing the instant it was taken."
        )
    try:
        stamp = dt.datetime.fromtimestamp(int(match.group("epoch")), tz=UTC)
    except (OverflowError, OSError, ValueError):
        raise LocalDropError(
            f"{filename}: epoch {match.group('epoch')} is not a second-"
            f"resolution unix timestamp. Seconds, not milliseconds."
        ) from None
    if not EPOCH_MIN <= stamp <= EPOCH_MAX:
        raise LocalDropError(
            f"{filename}: epoch {match.group('epoch')} resolves to "
            f"{stamp:%Y-%m-%dT%H:%M:%SZ}, outside {EPOCH_MIN:%Y}-"
            f"{EPOCH_MAX:%Y}. Seconds, not milliseconds."
        )
    return stamp


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan(drop: LocalDrop, root: Path | None = None,
         ) -> tuple[list[DropFile], list[tuple[str, str]]]:
    """Every readable export in the drop directory, and every refusal.

    Returns ``(files, refused)``. ``files`` is sorted oldest export first, so
    a directory holding several drops is ingested in the order they were
    taken. ``refused`` is ``(filename, reason)`` and is reported rather than
    dropped: a mistyped filename that vanished silently would look exactly
    like a provider that had gone quiet.
    """
    directory = drop.directory_path(root)
    if not directory.is_dir():
        return [], []
    files: list[DropFile] = []
    refused: list[tuple[str, str]] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            as_of = epoch_from_name(drop, path.name)
        except LocalDropError as exc:
            refused.append((path.name, str(exc)))
            continue
        files.append(DropFile(path=path, as_of=as_of, sha256=sha256_of(path)))
    files.sort(key=lambda f: (f.as_of, f.path.name))
    return files, refused


def ledger(warehouse, drop: LocalDrop) -> dict[str, str]:
    """``{filename: sha256}`` for every file of this drop already ingested.

    One query per provider per run. ``raw_fetch`` is the ledger: a hand drop
    is a fetch whose transport was a browser and a Downloads folder, and it
    is recorded with the same columns as every other fetch so one table still
    answers "where did this row come from".
    """
    frame = warehouse.sql(
        "SELECT endpoint, sha256 FROM raw_fetch WHERE source = ?", [drop.source]
    )
    if frame.empty:
        return {}
    return dict(zip(frame["endpoint"].astype(str), frame["sha256"].astype(str)))


# ---------------------------------------------------------------------------
# parse and map
# ---------------------------------------------------------------------------


def read(path: Path) -> pd.DataFrame:
    """The export as a frame. ``utf-8-sig`` because the file carries a BOM."""
    frame = pd.read_csv(path, encoding="utf-8-sig")
    if frame.empty:
        raise LocalDropError(f"{path.name}: parsed to zero rows")
    return frame


def to_projection_rows(
    drop: LocalDrop,
    parsed: pd.DataFrame,
    *,
    season: str,
    as_of: dt.datetime,
    id_to_code: dict[int, int] | None,
    valid_codes: set[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Long rows for ``fact_projection``, plus everything that did not map.

    Same contract as :func:`github_csv.to_projection_rows`: unresolved keys
    come back rather than vanishing, so a mapping gap is a number in the run
    report instead of a shorter table nobody notices.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware UTC")
    if drop.key_column not in parsed.columns:
        raise LocalDropError(
            f"{drop.key}: no {drop.key_column!r} column. Columns present: "
            f"{list(parsed.columns)}."
        )
    long = drop.to_long(parsed, drop.key_column)
    labels = _key_labels(parsed, drop.key_column)
    code, why = _resolve_keys(drop, long["key"], id_to_code, valid_codes)

    long = long.assign(code=code)
    bad = long["code"].isna() | long["gw"].isna()
    unresolved = long.loc[bad, ["key", "gw"]].copy()
    if not unresolved.empty:
        unresolved["reason"] = why
        unresolved["name"] = unresolved["key"].map(labels)
    keep = long.loc[~bad]

    rows = pd.DataFrame({
        "provider": drop.key,
        "season": season,
        "gw": keep["gw"].astype(int),
        "code": keep["code"].astype(int),
        "xp": pd.to_numeric(keep["xp"], errors="coerce").astype("float64"),
        "xp_if_appears": pd.Series([None] * len(keep), dtype="float64").to_numpy(),
        "p_appear": pd.Series([None] * len(keep), dtype="float64").to_numpy(),
        "xmins": pd.to_numeric(keep["xmins"], errors="coerce").astype("float64"),
        "as_of": as_of,
    })
    rows["as_of"] = pd.to_datetime(rows["as_of"], utc=True)
    rows = rows.reset_index(drop=True)

    # One export, one opinion per player per gameweek. A duplicate key with
    # two different numbers is the export contradicting itself, and writing
    # either would be a coin flip, so both go to unresolved.
    dup = rows.duplicated(["gw", "code"], keep=False)
    if dup.any():
        clashed = rows.loc[dup, ["gw", "code"]].copy()
        clashed["key"] = pd.NA
        clashed["name"] = pd.NA
        clashed["reason"] = "duplicate (gw, code) within one export"
        unresolved = pd.concat([unresolved, clashed], ignore_index=True)
        rows = rows.loc[~dup].reset_index(drop=True)

    return rows, unresolved.reset_index(drop=True)


def to_ownership_rows(
    drop: LocalDrop,
    parsed: pd.DataFrame,
    *,
    season: str,
    gw: int,
    as_of: dt.datetime,
    id_to_code: dict[int, int] | None,
    valid_codes: set[int],
) -> pd.DataFrame:
    """Rows for ``fact_external_ownership``, or an empty frame.

    ``gw`` is the first gameweek the export projects. An ownership share read
    at the export instant describes the squads standing for the next
    gameweek, and stamping it onto all ten would claim the provider had
    forecast ownership it never published.
    """
    if drop.to_ownership is None:
        return pd.DataFrame()
    own = drop.to_ownership(parsed, drop.key_column)
    code, _ = _resolve_keys(drop, own["key"], id_to_code, valid_codes)
    own = own.assign(code=code)
    keep = own.loc[own["code"].notna() & own["value"].notna()]
    rows = pd.DataFrame({
        "provider": drop.key,
        "season": season,
        "gw": int(gw),
        "code": keep["code"].astype(int),
        "metric": keep["metric"].astype(str),
        "value": pd.to_numeric(keep["value"], errors="coerce").astype("float64"),
        "as_of": as_of,
    })
    rows["as_of"] = pd.to_datetime(rows["as_of"], utc=True)
    rows = rows.loc[~rows.duplicated(["code", "metric"], keep=False)]
    return rows.reset_index(drop=True)


def _key_labels(parsed: pd.DataFrame, key_column: str) -> dict[object, str]:
    """``{key: display name}``, for naming unresolved ids in the report."""
    for name_column in ("Name", "name", "web_name"):
        if name_column in parsed.columns:
            return dict(zip(pd.to_numeric(parsed[key_column], errors="coerce"),
                            parsed[name_column].astype(str)))
    return {}


def _resolve_keys(drop: LocalDrop, keys: pd.Series,
                  id_to_code: dict[int, int] | None,
                  valid_codes: set[int]) -> tuple[pd.Series, str]:
    """Provider keys to stable player codes, never guessed, never fuzzy.

    The element-id branch reads the mapping the caller built from
    ``dim_player`` AT THE EXPORT INSTANT. Element 449 was one player last
    season and another this one, and an export taken in September has to be
    read against September's roster.
    """
    raw = pd.to_numeric(keys, errors="coerce")
    if drop.key_column_kind == "element_id":
        if id_to_code is None:
            raise ValueError(f"{drop.key} keys on element_id but no id_to_code given")
        return (raw.map(lambda v: id_to_code.get(int(v)) if pd.notna(v) else None),
                "element_id not in dim_player at the export instant")
    return (raw.map(lambda v: int(v) if pd.notna(v) and int(v) in valid_codes else None),
            "player_code not in dim_player at the export instant")


# ---------------------------------------------------------------------------
# the ingest
# ---------------------------------------------------------------------------


@dataclass
class DropIngestResult:
    """What one provider's drop directory produced on this run."""

    provider: str
    rows: int = 0
    ownership_rows: int = 0
    parsed: int = 0
    unresolved: int = 0
    ingested: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    unresolved_names: list[str] = field(default_factory=list)
    as_ofs: list[dt.datetime] = field(default_factory=list)
    #: A fetch_ledger status when the run did not ingest: "no_source" for a
    #: missing or empty directory, "refused" when every file was refused.
    status: str = ""
    reason: str = ""

    def note(self) -> str:
        """One line for the fetch_run note, naming the files either way."""
        parts: list[str] = []
        if self.ingested:
            parts.append("ingested " + ", ".join(self.ingested))
        if self.skipped:
            parts.append(f"{len(self.skipped)} already in ledger: "
                         + ", ".join(self.skipped))
        if self.refused:
            parts.append("refused " + "; ".join(
                f"{name}: {why.splitlines()[0]}" for name, why in self.refused))
        if self.reason:
            parts.append(self.reason)
        return " | ".join(parts)


def ingest_drop(
    warehouse,
    store,
    drop: LocalDrop,
    *,
    season: str,
    root: Path | None = None,
    resolver: Callable[[dt.datetime], tuple[dict[int, int], set[int]]] | None = None,
) -> DropIngestResult:
    """Read every new export in ``drop``'s directory into the warehouse.

    A missing directory and an empty one are ``no_source`` with a reason, not
    an error: a provider the owner has not exported this week is a fact about
    the week, and failing the nightly run over it would train everyone to
    ignore the run.

    ``resolver`` maps an instant to ``(element_id -> code, valid codes)``. It
    defaults to the CLI's own point-in-time lookups against ``dim_player``,
    imported here rather than at module scope because the CLI imports this
    module.
    """
    if resolver is None:
        from fpl_edge.ingest.projections.cli import element_id_to_code, known_codes

        def resolver(instant: dt.datetime) -> tuple[dict[int, int], set[int]]:
            return (element_id_to_code(warehouse, season, instant),
                    known_codes(warehouse, season, instant))

    result = DropIngestResult(provider=drop.key)
    directory = drop.directory_path(root)
    files, refused = scan(drop, root)
    result.refused = refused

    if not directory.is_dir():
        result.status = "no_source"
        result.reason = (
            f"{directory} does not exist. Drop a paid export there, named "
            f"{drop.directory}_<unix_epoch>.csv, and the next run reads it."
        )
        return result
    if not files:
        result.status = "refused" if refused else "no_source"
        result.reason = (
            f"{len(refused)} file(s) in {directory} carry no usable epoch"
            if refused else
            f"{directory} holds no export yet. The cadence of a hand drop is "
            f"whatever the owner does."
        )
        return result

    held = ledger(warehouse, drop)
    names: list[str] = []
    for item in files:
        known = held.get(item.path.name)
        if known == item.sha256:
            result.skipped.append(item.path.name)
            continue
        if known is not None:
            result.refused.append((item.path.name, (
                f"{item.path.name}: already ingested with sha256 "
                f"{known[:12]}, the file on disk now hashes to "
                f"{item.sha256[:12]}. One epoch names one instant. Re-export "
                f"under a new epoch rather than editing a drop in place."
            )))
            continue

        parsed = read(item.path)
        id_to_code, valid_codes = resolver(item.as_of)
        rows, unresolved = to_projection_rows(
            drop, parsed, season=season, as_of=item.as_of,
            id_to_code=id_to_code, valid_codes=valid_codes,
        )
        written = store.append("fact_projection", rows)
        own_written = 0
        if drop.to_ownership is not None and not rows.empty:
            own_rows = to_ownership_rows(
                drop, parsed, season=season, gw=int(rows["gw"].min()),
                as_of=item.as_of, id_to_code=id_to_code, valid_codes=valid_codes,
            )
            own_written = store.append("fact_external_ownership", own_rows)

        warehouse.record_fetch(
            source=drop.source, endpoint=item.path.name,
            params=f"season={season}", fetched_at=item.as_of,
            sha256=item.sha256, body_path=str(item.path), http_status=None,
        )
        result.rows += written
        result.ownership_rows += own_written
        result.parsed += len(parsed)
        result.ingested.append(item.path.name)
        result.as_ofs.append(item.as_of)
        if not unresolved.empty:
            keys = unresolved["key"].dropna().unique()
            result.unresolved += len(keys)
            names += [str(n) for n in unresolved["name"].dropna().unique()]
    result.unresolved_names = names[:20]
    if not result.ingested:
        # Every file refused and none skipped means the directory holds
        # something unusable, which is a different fact from an empty one.
        if result.refused and not result.skipped:
            result.status = "refused"
        result.reason = result.reason or (
            f"{len(result.skipped)} export(s) already ingested, "
            f"{len(result.refused)} refused; nothing new in {directory}"
        )
    return result
