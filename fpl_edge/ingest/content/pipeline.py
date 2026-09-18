"""End to end: fetch -> extract -> resolve -> dedupe -> score -> persist.

Run it with::

    uv run python -m fpl_edge.ingest.content.pipeline --help
    uv run python -m fpl_edge.ingest.content.pipeline probe
    uv run python -m fpl_edge.ingest.content.pipeline ingest --backfill-days 900
    uv run python -m fpl_edge.ingest.content.pipeline transcribe --dry-run
    uv run python -m fpl_edge.ingest.content.pipeline transcribe --limit 5 --budget-s 900
    uv run python -m fpl_edge.ingest.content.pipeline analyze --since 21 --budget-s 1800
    uv run python -m fpl_edge.ingest.content.pipeline repair-index
    uv run python -m fpl_edge.ingest.content.pipeline repair-index --apply
    uv run python -m fpl_edge.ingest.content.pipeline link-identities
    uv run python -m fpl_edge.ingest.content.pipeline score
    uv run python -m fpl_edge.ingest.content.pipeline consensus --gw 1

It is a module entry point rather than a subcommand of ``fpl_edge/cli/main.py``
because that file is owned by another team and one import line there is one
merge conflict at a deadline.

Everything this prints is a measurement. There is no summary line that is not
backed by a count taken from the run that just happened.

The command bodies live in sibling modules
------------------------------------------
``analyse_cmd``, ``transcribe_cmd`` and ``maintenance_cmd`` hold the work; this
module keeps its name, its argparse ``main`` and its ``-m`` target, and stays
the dispatcher (ARCHITECTURE_REVIEW.md Section 3 and Section 4 row 16). Every
name that moved is re-imported below, so ``python -m
fpl_edge.ingest.content.pipeline`` and every caller that imported a ``cmd_*``
off this module are unchanged.
"""

from __future__ import annotations
import argparse
import datetime as dt
import pandas as pd
from fpl_edge.ingest.content.analyze import MIN_SUBSTANTIVE_CHARS
from fpl_edge.ingest.content.analyze import MODEL as ANALYSIS_MODEL
from fpl_edge.ingest.content.calendar import load_calendar
from fpl_edge.ingest.content.claims import ExtractionStats, extract_from_item
from fpl_edge.ingest.content.consensus import consensus_map, deduplicate, render_consensus
from fpl_edge.ingest.content.fetch import ContentFetcher
from fpl_edge.ingest.content.loaders import load_source
from fpl_edge.ingest.content.scoring import (
    ResultIndex,
    creator_scores,
    score_claims,
    weight_lookup,
)
from fpl_edge.ingest.content.sources import ALL_SOURCES, ProbeReport, Source, fetchable
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse

from fpl_edge.ingest.content.analyse_cmd import (
    RELEVANCE_THRESHOLD,
    cmd_analyze,
    cmd_backfill_insights,
    cmd_reextract,
)
from fpl_edge.ingest.content.maintenance_cmd import (
    cmd_link_identities,
    cmd_repair_index,
)
from fpl_edge.ingest.content.pipeline_common import (
    UTC,
    _now,
    build_resolver,
)
from fpl_edge.ingest.content.transcribe_cmd import (
    cmd_retention,
    cmd_transcribe,
)

# The surface this module had before the command bodies moved out. Re-exported
# with redundant aliases so linters read them as deliberate and so every caller
# that imported one of these off `pipeline` is unchanged: `interfaces/
# creators.py` (build_resolver), tests/unit/test_repair_index_probe.py
# (INDEX_PROBE_KEYS, _index_is_healthy), tests/unit/test_content_analyze.py
# (rank_candidates), tests/unit/test_content_relevance_gate.py
# (RELEVANCE_THRESHOLD, relevance_score).
from fpl_edge.ingest.content.analyse_cmd import (  # noqa: F401
    RELEVANCE_PANEL_PTS as RELEVANCE_PANEL_PTS,
    RELEVANCE_PLAYER_PTS as RELEVANCE_PLAYER_PTS,
    RELEVANCE_RECENT_DAYS as RELEVANCE_RECENT_DAYS,
    RELEVANCE_RECENT_PTS as RELEVANCE_RECENT_PTS,
    RELEVANCE_TERM_PTS as RELEVANCE_TERM_PTS,
    rank_candidates as rank_candidates,
    relevance_score as relevance_score,
)
from fpl_edge.ingest.content.maintenance_cmd import (  # noqa: F401
    INDEX_PROBE_KEYS as INDEX_PROBE_KEYS,
    _index_is_healthy as _index_is_healthy,
)
from fpl_edge.ingest.content.pipeline_common import (  # noqa: F401
    UTC as UTC,
    build_resolver as build_resolver,
)


def cmd_probe(args: argparse.Namespace) -> int:
    """Hit every registered source once and report the real status code."""
    report = ProbeReport()
    with ContentFetcher("probe", delay_s=args.delay) as fetcher:
        for source in ALL_SOURCES:
            items, result = load_source(fetcher, source, max_items=3, max_videos=2)
            _ = items
            report.add(result)
            print(
                f"{result.skipped_reason or result.error or result.http_status!s:>16}  "
                f"{result.items:>5} items  {source.key}",
                flush=True,
            )
    print()
    print(report.render())
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    since = _now() - dt.timedelta(days=args.backfill_days) if args.backfill_days else None
    sources: tuple[Source, ...] = tuple(
        s for s in fetchable()
        if args.only is None or s.key in set(args.only.split(","))
    )

    with Warehouse(args.db) as warehouse:
        store = ContentStore(warehouse)
        applied = store.migrate()
        if applied:
            print(f"migrations applied: {', '.join(applied)}")
        registry = store.upsert_sources(ALL_SOURCES)
        print(f"registry: {registry.inserted} sources added, "
              f"{registry.updated} definitions updated")

        resolver = build_resolver(warehouse)
        calendar, cal_report = load_calendar(warehouse)
        print(f"resolver: {resolver.size} players across {len(resolver.seasons)} seasons "
              f"({', '.join(resolver.seasons)})")
        print(cal_report.render())

        stats = ExtractionStats()
        report = ProbeReport()
        all_items = []
        all_claims = []

        with ContentFetcher("ingest", delay_s=args.delay) as fetcher:
            for source in sources:
                items, result = load_source(
                    fetcher, source,
                    max_items=args.max_items,
                    max_videos=args.max_videos,
                    since=since,
                )
                report.add(result)
                claims = []
                for item in items:
                    claims.extend(extract_from_item(item, resolver, calendar, stats))
                all_items.extend(items)
                all_claims.extend(claims)
                print(
                    f"{result.skipped_reason or result.error or result.http_status!s:>16}  "
                    f"{len(items):>5} items  {len(claims):>6} claims  {source.key}",
                    flush=True,
                )
                store.record_probe(
                    source.key, status=result.http_status, items=len(items),
                    error=result.error or result.skipped_reason, at=_now(),
                )

        written_items = store.insert_items(all_items)
        written_claims = store.insert_claims(all_claims)

        print()
        print(report.render())
        print()
        print(stats.render())
        print()
        print(f"persisted: {written_items} new items, {written_claims} new claims")
        print(f"dropped for an unparsable or offset-less date: {report.bad_dates} entries")
        print(f"warehouse: {store.counts()}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    now = _now()
    with Warehouse(args.db) as warehouse:
        store = ContentStore(warehouse)
        store.migrate()
        claims = store.all_claims_for_scoring()
        if claims.empty:
            print("no claims stored; run `ingest` first")
            return 1

        results = warehouse.sql(
            "SELECT * EXCLUDE (rn) FROM (SELECT *, ROW_NUMBER() OVER "
            "(PARTITION BY season, code, fixture_id ORDER BY as_of DESC) rn "
            "FROM fact_player_fixture) WHERE rn = 1"
        )
        # EVERY dim_player row, not the newest per (season, code). Position
        # selects the benchmark bucket, so it is an input to the verdict, and
        # FPL reclassifies players mid-season; ResultIndex needs the history to
        # read the position a claim's gameweek was actually judged under. The
        # latest-row query that used to be here made a March reclassification
        # rewrite an August verdict silently.
        players = warehouse.sql(
            "SELECT season, code, position, as_of FROM dim_player"
        )
        calendar, cal_report = load_calendar(warehouse)
        print(cal_report.render())

        deadlines = {(s, g): d for s, g, d in calendar._rows}
        index = ResultIndex(results, players, deadlines=deadlines)
        outcomes, stats = score_claims(claims, index, calendar, now=now)
        written = store.insert_outcomes(outcomes)

        scores = creator_scores(outcomes, claims, as_of=now)
        store.insert_scores(scores)

        print()
        print(stats.render())
        print(f"  outcomes: {written.inserted} new, {written.revised} revised, "
              f"{written.unchanged} unchanged")
        if written.revised:
            # The count above is a report and dies with this process. The rows
            # are the record: without them, two verdicts flipping in opposite
            # directions leave every aggregate identical and nothing to audit.
            revisions = store.outcome_revisions()
            this_run = revisions[
                pd.to_datetime(revisions["superseded_utc"], utc=True) == pd.Timestamp(now)
            ]
            print(f"  revisions logged to claim_outcome_revision: {len(this_run)} this "
                  f"run, {len(revisions)} in total")
        print()
        overall = scores[scores["scope"] == "all"].sort_values(
            ["claims_scored", "wilson_lo95"], ascending=False
        )
        print(f"{'creator':<26} {'total':>6} {'scored':>7} {'hits':>5} "
              f"{'rate':>7} {'wilson_lo':>10} {'weight':>7}")
        for row in overall.itertuples(index=False):
            rate = f"{row.hit_rate:.1%}" if pd.notna(row.hit_rate) else "n/a"
            print(f"{row.creator[:26]:<26} {row.claims_total:>6} {row.claims_scored:>7} "
                  f"{row.hits:>5} {rate:>7} {row.wilson_lo95:>10.4f} {row.weight:>7.4f}")
        nonzero = int((overall["weight"] > 0).sum())
        print(f"\ncreators with a non-zero earned weight: {nonzero} of {len(overall)}")
        if nonzero == 0:
            print(
                "  All weights are zero. That is the CORRECT output when no creator has "
                "yet demonstrated a hit rate whose 95% lower bound clears 0.5 at "
                "n >= 25 scored claims. The consensus contributes nothing to the model "
                "in this state, by design."
            )
    return 0


def cmd_consensus(args: argparse.Namespace) -> int:
    as_of = (
        dt.datetime.fromisoformat(args.as_of).astimezone(UTC)
        if args.as_of else _now()
    )
    with Warehouse(args.db, read_only=True) as warehouse:
        store = ContentStore.__new__(ContentStore)
        store.wh = warehouse
        claims = store.claims_visible_at(as_of, season=args.season, gameweek=args.gw)
        deduped, dropped = deduplicate(claims)
        # The weights are filtered at the SAME instant as the claims. Taking the
        # newest creator_score row outright would weight correctly-filtered past
        # claims by a track record measured after the deadline being asked about
        # -- point-in-time on the left of the multiplication and hindsight on the
        # right. creator_score is append-only and keyed by as_of precisely so the
        # weight in force at a past instant is still recoverable.
        scores = warehouse.sql(
            "SELECT * EXCLUDE (rn) FROM (SELECT *, ROW_NUMBER() OVER "
            "(PARTITION BY creator, scope ORDER BY as_of DESC) rn FROM creator_score "
            "WHERE as_of <= ?) WHERE rn = 1",
            [as_of],
        )
        weights = weight_lookup(scores)
        print(f"as_of {as_of.isoformat()}: {len(claims)} claims visible, "
              f"{dropped} duplicate republications collapsed, {len(deduped)} distinct")
        print(f"weights in force: {sum(1 for v in weights.values() if v > 0)} non-zero "
              f"of {len(weights)} creators")
        table = consensus_map(claims, weights, season=args.season, gameweek=args.gw)
        print(render_consensus(table, top=args.top))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fpl-content")
    parser.add_argument("--db", default="data/warehouse/fpl.duckdb")
    parser.add_argument("--delay", type=float, default=1.0)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="hit every source and report real HTTP status")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("ingest", help="fetch, extract claims, persist")
    p.add_argument("--backfill-days", type=int, default=0)
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--max-videos", type=int, default=6)
    # There is deliberately no --no-transcripts. It existed, was documented in
    # --help, and did nothing: load_source swallowed the keyword. Nothing in
    # the ingest path fetches transcripts in the first place -- youtube.py's
    # fetch_transcript refuses unless a caller passes allow_disallowed_routes,
    # and no caller here does, because both routes to captions go through
    # /youtubei/, which youtube.com/robots.txt disallows. So there is no
    # transcript fetching for a flag to suppress, and the only way to make the
    # flag mean something would be to start doing the thing the policy forbids.
    # A switch that advertises control it does not have is worse than no
    # switch; removed rather than faked.
    p.add_argument("--only", default=None, help="comma-separated source keys")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("reextract", help="re-run extraction over stored items, offline")
    p.add_argument("--replace", action="store_true",
                   help="delete existing claims and outcomes first")
    p.set_defaults(func=cmd_reextract)

    p = sub.add_parser(
        "backfill-insights",
        help="recover insights from analyses already stored (no model calls)")
    p.set_defaults(func=cmd_backfill_insights)

    p = sub.add_parser("analyze", help="semantic read of stored text, resumable")
    p.add_argument("--limit", type=int, default=None, help="max items to queue")
    p.add_argument("--since", type=int, default=21,
                   help="only items published in the last N days (0 = all)")
    p.add_argument("--budget-s", type=float, default=0.0,
                   help="stop starting new model calls after N seconds")
    p.add_argument("--workers", type=int, default=3,
                   help="concurrent model calls")
    p.add_argument("--model", default=ANALYSIS_MODEL)
    p.add_argument("--creator", default=None, help="restrict to one creator")
    p.add_argument("--min-chars", type=int, default=MIN_SUBSTANTIVE_CHARS,
                   help="skip items with less prose than this once links and "
                        "separator furniture are discounted")
    p.add_argument("--retry-skipped", action="store_true",
                   help="re-attempt items previously recorded in "
                        "content_analysis_skip")
    p.add_argument("--dry-run", action="store_true",
                   help="print the queue and its ordering; spend nothing")
    p.add_argument("--token-budget", type=int, default=0,
                   help="stop starting new model calls once the reported "
                        "tokens for this run reach N (0 = no token ceiling)")
    p.add_argument("--analyse-notes", action="store_true",
                   help="also analyse items whose only text is a description; "
                        "off by default because such a call can produce "
                        "neither a claim nor an insight")
    p.add_argument("--summary-json", default=None,
                   help="write this run's measured totals to this path")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("transcribe",
                       help="local ASR / panel captions -> timestamped segments")
    p.add_argument("--limit", type=int, default=None, help="max items to attempt")
    p.add_argument("--since", type=int, default=21,
                   help="only items published in the last N days (0 = all)")
    p.add_argument("--budget-s", type=float, default=0.0,
                   help="stop starting new items after N seconds")
    p.add_argument("--kinds", default="podcast,youtube",
                   help="comma-separated content_item.kind values to consider")
    p.add_argument("--creator", default=None,
                   help="restrict to one creator (must still be on the panel "
                        "for the youtube path)")
    p.add_argument("--any-creator", action="store_true",
                   help="podcast ASR beyond the curated panel. The YouTube "
                        "caption path IGNORES this and still refuses "
                        "off-panel creators: the scale limit is the policy.")
    p.add_argument("--model", default=None,
                   help="MLX-Whisper weights id")
    p.add_argument("--min-relevance", type=float, default=RELEVANCE_THRESHOLD,
                   help="deterministic relevance-score threshold; queued items "
                        "scoring below it are recorded in "
                        "content_transcribe_skip as relevance:<score> and stay "
                        "description-only. 0 disables the gate.")
    p.add_argument("--dry-run", action="store_true",
                   help="print the queue and which items have audio; fetch and "
                        "transcribe nothing")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("retention",
                       help="delete cached audio whose transcript is stored "
                            "with full provenance (sha256 outlives the file)")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be deleted; delete nothing")
    p.set_defaults(func=cmd_retention)

    p = sub.add_parser(
        "repair-index",
        help="verify (and with --apply, rebuild) primary-key indexes")
    p.add_argument("--table", default=None,
                   help="comma-separated table names; default: every content "
                        "table that carries a primary key")
    p.add_argument("--apply", action="store_true",
                   help="rebuild the tables that fail the check. Without it "
                        "this command writes nothing.")
    p.add_argument("--force", action="store_true",
                   help="with --apply, rebuild every named table even if its "
                        "check passed. The probe only round-trips ONE key, and "
                        "this corruption is local to part of the key space.")
    p.set_defaults(func=cmd_repair_index)

    p = sub.add_parser("link-identities",
                       help="link creators to verified FPL entries; never guess")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_link_identities)

    p = sub.add_parser("score", help="resolve outcomes and compute earned weights")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("consensus", help="the consensus map at a point in time")
    p.add_argument("--as-of", default=None)
    p.add_argument("--season", default=None)
    p.add_argument("--gw", type=int, default=None)
    p.add_argument("--top", type=int, default=8)
    p.set_defaults(func=cmd_consensus)

    args = parser.parse_args(argv)
    return int(args.func(args))
