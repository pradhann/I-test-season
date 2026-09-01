"""Run the projection ingest.

::

    uv run python -m fpl_edge.ingest.projections.cli ingest --season 2026-27
    uv run python -m fpl_edge.ingest.projections.cli probe
    uv run python -m fpl_edge.ingest.projections.cli report
    uv run python -m fpl_edge.ingest.projections.cli providers

Deliberately in this package rather than in ``scripts/``: this team owns
``fpl_edge/ingest/projections/**`` and nothing else, and an entry point that
lives outside the directory it belongs to is how two teams end up editing one
file.

Failure isolation
-----------------
Every provider runs inside its own ``try``. This is not defensive habit; it is
the deadline requirement. The run that matters happens in the ninety minutes
before a Friday 17:30 deadline, and on that run a provider that has changed its
HTML, let its certificate expire or simply gone dark must cost us *that
provider's* rows and nothing else. A bare loop without isolation converts one
site's bad afternoon into a blind transfer.

A failed provider is reported with its real exception type and message and
recorded as ``ok=False``. It is never retried into silence and never replaced
with a stale copy, a zero, or an interpolation: the ensemble downstream is
allowed to see that a source is missing, and is not allowed to be lied to about
why.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import sys
import traceback

import pandas as pd

from fpl_edge.ingest.projections import (
    fpl_ep,
    fplform,
    github_csv,
    livefpl,
    premierinjuries,
    rotowire,
)
from fpl_edge.ingest.projections.providers import PROVIDERS, probe_all
from fpl_edge.ingest.projections.store import ProjectionStore
from fpl_edge.store import Warehouse

SEASON = "2026-27"


@dataclasses.dataclass
class StepResult:
    """What one provider's ingest actually did, success or failure.

    ``rows`` is rows *appended*, which is 0 on an idempotent re-run of an
    unchanged feed -- that is a success, not a failure, and the two are
    distinguished by ``ok`` rather than by the count.
    """

    provider: str
    ok: bool
    rows: int = 0
    parsed: int = 0
    unresolved: int = 0
    detail: str = ""
    error: str = ""

    def line(self) -> str:
        if not self.ok:
            return f"{self.provider:16} FAILED  {self.error}"
        return (f"{self.provider:16} ok      {self.rows:>6,} appended  "
                f"{self.parsed:>6,} parsed  {self.unresolved:>4} unresolved  "
                f"{self.detail}")


def element_catalogs(warehouse: Warehouse, as_of: dt.datetime) -> dict[str, set[int]]:
    """Every season's element_id set, so a feed can be attributed to one of them."""
    frame = warehouse.sql(
        "SELECT DISTINCT season, element_id FROM dim_player WHERE as_of <= ?", [as_of]
    )
    return {s: set(g["element_id"].astype(int))
            for s, g in frame.groupby("season", sort=True)}


def element_id_to_code(warehouse: Warehouse, season: str,
                       as_of: dt.datetime) -> dict[int, int]:
    """The FPL API's own mapping, read point-in-time.

    Providers that key on ``element_id`` resolve through ``dim_player`` rather
    than through their own name strings, so a rename, a transfer or a duplicate
    surname cannot move a projection onto the wrong player.
    """
    frame = warehouse.snapshot_at(as_of).table(
        "dim_player", where="season = ?", params=[season]
    )
    if frame.empty:
        raise RuntimeError(f"no dim_player rows for {season} at {as_of:%Y-%m-%dT%H:%M:%SZ}")
    return dict(zip(frame["element_id"].astype(int), frame["code"].astype(int)))


def known_codes(warehouse: Warehouse, season: str, as_of: dt.datetime) -> set[int]:
    """Every stable player code the season knows about at ``as_of``.

    A feed that publishes ``code`` directly still has to be checked against
    this: a code we have never seen is either a typo, a different key space, or
    a player who signed after our last dim_player refresh, and all three are
    reasons to drop-and-count rather than to write.
    """
    frame = warehouse.snapshot_at(as_of).table(
        "dim_player", where="season = ?", params=[season]
    )
    return set(frame["code"].astype(int))


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def ingest(season: str = SEASON, *, first_gw: int = 1, last_gw: int = 8,
           db: str | None = None, only: tuple[str, ...] = (),
           verbose: bool = False,
           skip_if_fresh_h: float | None = None) -> dict[str, StepResult]:
    """Fetch every reachable provider and land it in the warehouse.

    Returns one :class:`StepResult` per provider attempted. Nothing raises out
    of here except a failure to open the warehouse itself: a provider's
    exception belongs in its own row of the report, not at the top of a
    traceback that hides the four providers that worked.
    """
    steps = [
        ("fplform", _ingest_fplform),
        ("livefpl", _ingest_livefpl),
        ("fpl_ep", _ingest_fpl_ep),
        ("rotowire", _ingest_rotowire),
        ("premierinjuries", _ingest_premierinjuries),
    ]
    steps += [(f.key, _github_step(f.key)) for f in github_csv.FEEDS]
    if only:
        steps = [s for s in steps if s[0] in only]
        missing = set(only) - {s[0] for s in steps}
        if missing:
            raise SystemExit(f"unknown provider(s) {sorted(missing)}")

    from fpl_edge.store import fetch_ledger

    results: dict[str, StepResult] = {}
    with Warehouse(db) if db else Warehouse() as warehouse:
        # The "already latest" gate (PIPELINES.md §4.2): a successful pass
        # inside the window means every provider was checked recently; skip
        # the whole run and say so in the ledger. Error runs never satisfy
        # the gate, so failures always retry.
        if skip_if_fresh_h and fetch_ledger.checked_within(
                warehouse, "ingest_projections", skip_if_fresh_h):
            with fetch_ledger.record_run(warehouse, "ingest_projections") as rec:
                rec.status = "skipped_fresh"
                rec.note = f"last ok run younger than {skip_if_fresh_h}h"
            print(f"skipped: providers checked within {skip_if_fresh_h}h")
            return {}

        store = ProjectionStore(warehouse)
        # Write-on-change for every provider in this run -- the projection
        # tables are the measured bloat source (60k value-identical fplform
        # rows over 13 pulls).
        store.change_dedup_default = True
        if store.applied_migrations:
            print(f"applied migrations: {', '.join(store.applied_migrations)}")

        for name, step in steps:
            store.unchanged_acc = 0
            try:
                with fetch_ledger.record_run(
                        warehouse, "ingest_projections", name) as rec:
                    results[name] = step(warehouse, store, season,
                                         first_gw=first_gw, last_gw=last_gw)
                    rec.add(results[name].rows, store.unchanged_acc)
                    if not results[name].ok:
                        rec.status = "error"
                        rec.note = results[name].error
            except Exception as exc:  # noqa: BLE001 -- isolation is the point
                if verbose:
                    traceback.print_exc()
                results[name] = StepResult(
                    provider=name, ok=False,
                    error=f"{type(exc).__name__}: {exc}".replace("\n", " ")[:400],
                )
            print(results[name].line())

    ok = sum(1 for r in results.values() if r.ok)
    print(f"\n{ok}/{len(results)} providers ok, "
          f"{sum(r.rows for r in results.values()):,} rows appended, "
          f"{sum(r.unresolved for r in results.values()):,} names/ids unresolved")
    return results


def _ingest_fplform(warehouse: Warehouse, store: ProjectionStore, season: str,
                    *, first_gw: int, last_gw: int) -> StepResult:
    got = fplform.fetch_csv(first_gw=first_gw, last_gw=last_gw)
    warehouse.record_fetch(
        source="projections_fplform", endpoint=fplform.EXPORT_PATH,
        params=f"firstgw={first_gw}&lastgw={last_gw}&all=1",
        fetched_at=got.fetched_at, sha256=got.sha256,
        body_path=str(got.body_path), http_status=got.http_status,
    )
    parsed = fplform.parse_csv(got.body)
    id_to_code = element_id_to_code(warehouse, season, got.fetched_at)
    rows, unresolved = fplform.to_projection_rows(
        parsed, season=season, as_of=got.fetched_at, id_to_code=id_to_code
    )
    n = store.append("fact_projection", rows)
    if not unresolved.empty:
        print("  fplform unresolved element_ids:",
              unresolved[["element_id", "provider_name", "provider_team"]]
              .drop_duplicates().head(10).to_dict("records"))
    return StepResult(
        provider="fplform", ok=True, rows=n, parsed=len(parsed),
        unresolved=int(unresolved["element_id"].nunique()) if not unresolved.empty else 0,
        detail=(f"HTTP {got.http_status}, {parsed['element_id'].nunique()} players "
                f"x GW{first_gw}-{last_gw}"),
    )


def _ingest_livefpl(warehouse: Warehouse, store: ProjectionStore, season: str,
                    *, first_gw: int, last_gw: int) -> StepResult:
    info = livefpl.fetch("player_info")
    warehouse.record_fetch(
        source="projections_livefpl", endpoint="/planner/all_player_info.json",
        params=None, fetched_at=info.fetched_at, sha256=info.sha256,
        body_path=str(info.body_path), http_status=info.http_status,
    )
    id_to_code = element_id_to_code(warehouse, season, info.fetched_at)
    provider_map = livefpl.parse_code_map(info.body)
    agree = sum(1 for k, v in provider_map.items() if id_to_code.get(k) == v)
    print(f"  livefpl code map: {len(provider_map)} entries, {agree} agree with "
          f"dim_player, {len(provider_map) - agree} differ")

    catalogs = element_catalogs(warehouse, info.fetched_at)
    total = parsed_total = unresolved_total = 0
    notes: list[str] = []
    for kind in ("predicted_eo", "top10k", "elite"):
        got_own = livefpl.fetch(kind, gw=first_gw)
        warehouse.record_fetch(
            source="projections_livefpl", endpoint=livefpl._path(kind, first_gw),
            params=None, fetched_at=got_own.fetched_at, sha256=got_own.sha256,
            body_path=str(got_own.body_path), http_status=got_own.http_status,
        )
        parsed_own = livefpl.parse_ownership(got_own.body, kind)
        # Which season's element_ids is this file keyed on? Asked of the data,
        # never assumed: top10k.json and elite.json still described 2025-26
        # when predictedEOs/1.json had already rolled over to 2026-27.
        file_season = livefpl.infer_season(
            set(parsed_own["element_id"].astype(int)), catalogs
        )
        file_gw = first_gw if file_season == season else _last_finished_gw(
            warehouse, file_season, got_own.fetched_at
        )
        file_map = (id_to_code if file_season == season
                    else element_id_to_code(warehouse, file_season, got_own.fetched_at))
        own_rows, own_unres = livefpl.to_ownership_rows(
            parsed_own, kind=kind, season=file_season, gw=file_gw,
            as_of=got_own.fetched_at, id_to_code=file_map,
        )
        n = store.append("fact_external_ownership", own_rows)
        total += n
        parsed_total += len(parsed_own)
        unresolved_total += len(own_unres)
        notes.append(f"{kind}@{file_season}/gw{file_gw}:{n}")
    return StepResult(provider="livefpl", ok=True, rows=total, parsed=parsed_total,
                      unresolved=unresolved_total, detail=" ".join(notes))


def _ingest_fpl_ep(warehouse: Warehouse, store: ProjectionStore, season: str,
                   *, first_gw: int, last_gw: int) -> StepResult:
    got = fpl_ep.fetch_bootstrap()
    warehouse.record_fetch(
        source=fpl_ep.SOURCE, endpoint="/bootstrap-static/", params=None,
        fetched_at=got.fetched_at, sha256=got.sha256,
        body_path=str(got.body_path), http_status=got.http_status,
    )
    rows = fpl_ep.to_projection_rows(got.body, season=season, as_of=got.fetched_at)
    # ep_next is keyed on `code` inside the same document, so there is no
    # cross-document id crossing and therefore nothing that can fail to
    # resolve. Recorded as 0 rather than omitted, so the report's unresolved
    # column means the same thing in every row.
    n = store.append("fact_projection", rows)
    return StepResult(
        provider="fpl_ep", ok=True, rows=n, parsed=len(rows), unresolved=0,
        detail=f"HTTP {got.http_status}, ep_next for GW{int(rows['gw'].iloc[0])}",
    )


def _ingest_rotowire(warehouse: Warehouse, store: ProjectionStore, season: str,
                     *, first_gw: int, last_gw: int) -> StepResult:
    got = rotowire.fetch()
    warehouse.record_fetch(
        source="projections_rotowire", endpoint=rotowire.LINEUPS_PATH, params=None,
        fetched_at=got.fetched_at, sha256=got.sha256,
        body_path=str(got.body_path), http_status=got.http_status,
    )
    entries = rotowire.parse_lineups(got.body)
    gw = _next_gw(warehouse, season, got.fetched_at)
    snap = warehouse.snapshot_at(got.fetched_at)
    teams = snap.table("dim_team", where="season = ?", params=[season])
    short_to_code = dict(zip(teams["short_name"], teams["team_code"].astype(int)))
    fixtures = snap.table(
        "fact_fixture", where="season = ? AND gw = ?", params=[season, gw]
    )
    rotowire.validate_fixture_pairs(
        entries,
        set(zip(fixtures["home_team_code"].astype(int),
                fixtures["away_team_code"].astype(int))),
        short_to_code,
    )
    rosters = snap.table("dim_player", where="season = ?", params=[season])
    rows, unresolved = rotowire.to_lineup_rows(
        entries, season=season, gw=gw, as_of=got.fetched_at,
        rosters=rosters[["code", "team_code", "web_name", "first_name", "second_name"]],
        short_to_code=short_to_code,
    )
    n = store.append("fact_predicted_lineup", rows)
    starters = int(rows["predicted_start"].sum()) if not rows.empty else 0
    if not unresolved.empty:
        print("  rotowire unresolved names:", unresolved.head(12).to_dict("records"))
    return StepResult(
        provider="rotowire", ok=True, rows=n, parsed=len(entries),
        unresolved=len(unresolved),
        detail=(f"HTTP {got.http_status}, {len({e.team_abbr for e in entries})} "
                f"team sheets for GW{gw}, {starters} starters"),
    )


def _ingest_premierinjuries(warehouse: Warehouse, store: ProjectionStore, season: str,
                            *, first_gw: int, last_gw: int) -> StepResult:
    got = premierinjuries.fetch()
    warehouse.record_fetch(
        source=f"projections_{premierinjuries.PROVIDER}",
        endpoint=premierinjuries.TABLE_PATH, params=None,
        fetched_at=got.fetched_at, sha256=got.sha256,
        body_path=str(got.body_path), http_status=got.http_status,
    )
    entries = premierinjuries.parse_table(got.body)
    gw = _next_gw(warehouse, season, got.fetched_at)
    snap = warehouse.snapshot_at(got.fetched_at)
    teams = snap.table("dim_team", where="season = ?", params=[season])
    rosters = snap.table("dim_player", where="season = ?", params=[season])
    rows, unresolved = premierinjuries.to_projection_rows(
        entries, season=season, gw=gw, as_of=got.fetched_at,
        rosters=rosters[["code", "team_code", "web_name", "first_name", "second_name"]],
        name_to_team_code=dict(zip(teams["name"], teams["team_code"].astype(int))),
    )
    n = store.append("fact_projection", rows)
    if not unresolved.empty:
        print("  premierinjuries unresolved:", unresolved.head(10).to_dict("records"))
    ruled_out = int((rows["p_appear"] == 0.0).sum()) if not rows.empty else 0
    return StepResult(
        provider="premierinjuries", ok=True, rows=n, parsed=len(entries),
        unresolved=len(unresolved),
        detail=(f"HTTP {got.http_status}, {len({e.club for e in entries})} clubs "
                f"for GW{gw}, {ruled_out} ruled out, p_appear only"),
    )


def _github_step(key: str):
    """Build the ingest step for one community GitHub feed."""

    def step(warehouse: Warehouse, store: ProjectionStore, season: str,
             *, first_gw: int, last_gw: int) -> StepResult:
        feed = github_csv.BY_KEY[key]
        got = github_csv.fetch(feed, season=season, gw=first_gw)
        warehouse.record_fetch(
            source=f"projections_{feed.key}", endpoint=got.body_path.name,
            params=f"gw={first_gw}", fetched_at=got.fetched_at, sha256=got.sha256,
            body_path=str(got.body_path), http_status=got.http_status,
        )
        parsed = github_csv.parse(feed, got.body)
        rows, unresolved = github_csv.to_projection_rows(
            feed, parsed, season=season, as_of=got.fetched_at,
            id_to_code=(element_id_to_code(warehouse, season, got.fetched_at)
                        if feed.key_column_kind == "element_id" else None),
            valid_codes=known_codes(warehouse, season, got.fetched_at),
            default_gw=first_gw,
        )
        n = store.append("fact_projection", rows)
        if not unresolved.empty:
            print(f"  {feed.key} unresolved:",
                  unresolved.head(8).to_dict("records"))
        gws = sorted(rows["gw"].unique().tolist()) if not rows.empty else []
        return StepResult(
            provider=feed.key, ok=True, rows=n, parsed=len(parsed),
            unresolved=len(unresolved),
            detail=(f"HTTP {got.http_status}, {feed.repo}@{got.body_path.name}, "
                    f"gw={gws}, xmins={'yes' if feed.xmins_column else 'no'}"),
        )

    return step


def _next_gw(warehouse: Warehouse, season: str, as_of: dt.datetime) -> int:
    """The first gameweek whose deadline is still ahead of ``as_of``."""
    frame = warehouse.sql(
        "SELECT min(gw) AS gw FROM dim_event "
        "WHERE season = ? AND deadline_utc > ? AND as_of <= ?",
        [season, as_of, as_of],
    )
    gw = frame.iloc[0]["gw"]
    if pd.isna(gw):
        raise RuntimeError(f"no future deadline for {season} at {as_of}")
    return int(gw)


def _last_finished_gw(warehouse: Warehouse, season: str, as_of: dt.datetime) -> int:
    """The last gameweek of ``season`` with results visible at ``as_of``."""
    frame = warehouse.sql(
        "SELECT max(gw) AS gw FROM fact_player_fixture WHERE season = ? AND as_of <= ?",
        [season, as_of],
    )
    gw = frame.iloc[0]["gw"]
    if pd.isna(gw):
        raise RuntimeError(f"no finished gameweek for {season} at {as_of}")
    return int(gw)


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def probe() -> None:
    results = probe_all()
    width = max(len(r.url) for r in results)
    for r in results:
        status = r.error or f"{r.status} {r.bytes_:,}B {r.content_type[:30]}"
        print(f"{r.provider:22} {r.url:<{width}} robots={str(r.robots_allows):5} {status}")


def report() -> None:
    rows = [{
        "provider": p.name, "verdict": p.verdict,
        "2026-27": {True: "yes", False: "no", None: "?"}[p.covers_2026_27],
        "interface": p.interface.split(".")[0][:48], "cost": p.cost.split(".")[0][:28],
    } for p in PROVIDERS]
    print(pd.DataFrame(rows).to_string(index=False))


def providers(db: str | None = None) -> None:
    """What is actually in the warehouse, per source."""
    with Warehouse.read_copy(db) if db else Warehouse.read_copy() as wh:
        for table in ("fact_projection", "fact_external_ownership",
                      "fact_predicted_lineup"):
            try:
                frame = wh.sql(
                    f"SELECT provider, count(*) AS rows, count(DISTINCT code) AS players, "
                    f"min(gw) AS first_gw, max(gw) AS last_gw, max(as_of) AS last_seen "
                    f"FROM {table} GROUP BY 1 ORDER BY 1"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"\n{table}: {type(exc).__name__}: {str(exc).splitlines()[0]}")
                continue
            print(f"\n{table}")
            print(frame.to_string(index=False) if not frame.empty else "  (empty)")
        try:
            norm = wh.sql(
                "SELECT source, count(*) AS rows, count(xmins) AS with_xmins, "
                "count(xpts) AS with_xpts, max(fetched_at) AS fetched_at "
                "FROM projection_normalized GROUP BY 1 ORDER BY 1"
            )
            print("\nprojection_normalized (source, player_code, gw, xmins, xpts, fetched_at)")
            print(norm.to_string(index=False) if not norm.empty else "  (empty)")
        except Exception as exc:  # noqa: BLE001
            print(f"\nprojection_normalized: {type(exc).__name__}: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["ingest", "probe", "report", "providers"])
    parser.add_argument("--season", default=SEASON)
    parser.add_argument("--first-gw", type=int, default=1)
    parser.add_argument("--last-gw", type=int, default=8)
    parser.add_argument("--db", default=None)
    parser.add_argument("--only", default="",
                        help="comma-separated provider keys; default is all")
    parser.add_argument("--verbose", action="store_true",
                        help="print a full traceback for each failed provider")
    parser.add_argument("--skip-if-fresh", type=float, default=None,
                        metavar="HOURS",
                        help="skip the whole run when the last successful run "
                             "finished within HOURS (fetch_run ledger gate)")
    args = parser.parse_args(argv)
    if args.command == "probe":
        probe()
    elif args.command == "report":
        report()
    elif args.command == "providers":
        providers(args.db)
    else:
        results = ingest(
            args.season, first_gw=args.first_gw, last_gw=args.last_gw, db=args.db,
            only=tuple(k for k in args.only.split(",") if k), verbose=args.verbose,
            skip_if_fresh_h=args.skip_if_fresh,
        )
        # A provider failing is a reported fact, not a non-zero exit: the run
        # succeeded at the thing it exists to do, which is landing whatever was
        # reachable. Only a total wipe-out is worth failing a cron job over.
        if results and not any(r.ok for r in results.values()):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
