"""End to end: fetch -> extract -> resolve -> dedupe -> score -> persist.

Run it with::

    uv run python -m fpl_edge.ingest.content.pipeline --help
    uv run python -m fpl_edge.ingest.content.pipeline probe
    uv run python -m fpl_edge.ingest.content.pipeline ingest --backfill-days 900
    uv run python -m fpl_edge.ingest.content.pipeline score
    uv run python -m fpl_edge.ingest.content.pipeline consensus --gw 1

It is a module entry point rather than a subcommand of ``fpl_edge/cli/main.py``
because that file is owned by another team and one import line there is one
merge conflict at a deadline.

Everything this prints is a measurement. There is no summary line that is not
backed by a count taken from the run that just happened.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import pandas as pd

from fpl_edge.ingest.content.calendar import load_calendar
from fpl_edge.ingest.content.claims import ExtractionStats, extract_from_item
from fpl_edge.ingest.content.consensus import consensus_map, deduplicate, render_consensus
from fpl_edge.ingest.content.fetch import ContentFetcher
from fpl_edge.ingest.content.loaders import load_source
from fpl_edge.ingest.content.models import ContentItem
from fpl_edge.ingest.content.resolve import SeasonResolvers
from fpl_edge.ingest.content.scoring import (
    ResultIndex,
    creator_scores,
    score_claims,
    weight_lookup,
)
from fpl_edge.ingest.content.sources import ALL_SOURCES, ProbeReport, Source, fetchable
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse

UTC = dt.UTC


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def build_resolver(warehouse: Warehouse) -> SeasonResolvers:
    """One index per season, built from the latest dim_player row per player.

    Per-season because a claim is resolved against the squad that existed when
    it was published; see :class:`SeasonResolvers` for what that recovers.
    """
    players = warehouse.sql(
        "SELECT * EXCLUDE (rn) FROM (SELECT *, ROW_NUMBER() OVER "
        "(PARTITION BY season, code ORDER BY as_of DESC) rn FROM dim_player) WHERE rn = 1"
    )
    return SeasonResolvers(players)


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


def cmd_reextract(args: argparse.Namespace) -> int:
    """Re-run claim extraction over stored items, without touching the network.

    The extractor is the part of this package most likely to need iterating, and
    re-fetching forty sources to test a lexicon change is both slow and rude to
    the hosts. ``content_item`` stores the text precisely so extraction is
    reproducible from the archive.

    Claim ids are content-addressed over (item, player, action, gameweek), so a
    re-run is idempotent for claims that did not change and additive for new
    ones. It does NOT delete claims a stricter extractor no longer produces;
    ``--replace`` does that explicitly, because silently dropping rows another
    process may already have scored is not something to do by default.
    """
    with Warehouse(args.db) as warehouse:
        store = ContentStore(warehouse)
        store.migrate()
        resolver = build_resolver(warehouse)
        calendar, cal_report = load_calendar(warehouse)
        print(f"resolver: {resolver.size} players across {len(resolver.seasons)} seasons")
        print(cal_report.render())

        items = warehouse.sql("SELECT * FROM content_item ORDER BY published_at")
        print(f"re-extracting from {len(items)} stored items")

        if args.replace:
            before = int(warehouse.sql(
                "SELECT count(*) c FROM content_claim").iloc[0]["c"])
            warehouse.sql("DELETE FROM claim_outcome")
            warehouse.sql("DELETE FROM content_claim")
            print(f"--replace: deleted {before} claims and their outcomes")

        stats = ExtractionStats()
        claims = []
        for row in items.itertuples(index=False):
            published = pd.Timestamp(row.published_at)
            if published.tzinfo is None:
                published = published.tz_localize(UTC)
            item = ContentItem(
                item_id=row.item_id, source_key=row.source_key, creator=row.creator,
                kind=row.kind, title=row.title, url=row.url,
                published_at=published.to_pydatetime(),
                text=row.text, fetched_at=published.to_pydatetime(),
                text_source=row.text_source,
            )
            claims.extend(extract_from_item(item, resolver, calendar, stats))

        written = store.insert_claims(claims)
        print()
        print(stats.render())
        print(f"\npersisted: {written} new claims; warehouse: {store.counts()}")
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
        players = warehouse.sql(
            "SELECT * EXCLUDE (rn) FROM (SELECT *, ROW_NUMBER() OVER "
            "(PARTITION BY season, code ORDER BY as_of DESC) rn FROM dim_player) WHERE rn = 1"
        )
        calendar, cal_report = load_calendar(warehouse)
        print(cal_report.render())

        index = ResultIndex(results, players)
        outcomes, stats = score_claims(claims, index, calendar, now=now)
        written = store.insert_outcomes(outcomes)

        scores = creator_scores(outcomes, claims, as_of=now)
        store.insert_scores(scores)

        print()
        print(stats.render())
        print(f"  outcomes: {written.inserted} new, {written.revised} revised, "
              f"{written.unchanged} unchanged")
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
        scores = warehouse.sql(
            "SELECT * EXCLUDE (rn) FROM (SELECT *, ROW_NUMBER() OVER "
            "(PARTITION BY creator, scope ORDER BY as_of DESC) rn FROM creator_score) "
            "WHERE rn = 1"
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


if __name__ == "__main__":
    sys.exit(main())
