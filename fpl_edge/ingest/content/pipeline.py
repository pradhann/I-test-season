"""End to end: fetch -> extract -> resolve -> dedupe -> score -> persist.

Run it with::

    uv run python -m fpl_edge.ingest.content.pipeline --help
    uv run python -m fpl_edge.ingest.content.pipeline probe
    uv run python -m fpl_edge.ingest.content.pipeline ingest --backfill-days 900
    uv run python -m fpl_edge.ingest.content.pipeline transcribe --dry-run
    uv run python -m fpl_edge.ingest.content.pipeline transcribe --limit 5 --budget-s 900
    uv run python -m fpl_edge.ingest.content.pipeline analyze --since 21 --budget-s 1800
    uv run python -m fpl_edge.ingest.content.pipeline link-identities
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

from fpl_edge.ingest.content.analyze import MIN_SUBSTANTIVE_CHARS
from fpl_edge.ingest.content.analyze import MODEL as ANALYSIS_MODEL
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


# ---------------------------------------------------------------------------
# analyze: the semantic read, in bulk
# ---------------------------------------------------------------------------

#: Ledger of items we looked at and deliberately did NOT store an analysis for.
#:
#: Without it, "store nothing when there is nothing" and "a re-run is a no-op"
#: contradict each other: every barren item would be re-sent to the model on
#: every run, forever, at a model call each. So the decision is recorded --
#: item, model, reason -- which also gives the Creators tab a real
#: ``take_reason`` instead of an empty state it has to invent words for.
#:
#: Keyed (item_id, model) to match content_analysis: a better model may well
#: find something in text an earlier one could not.
_SKIP_DDL = """
CREATE TABLE IF NOT EXISTS content_analysis_skip (
    item_id      VARCHAR NOT NULL,
    model        VARCHAR NOT NULL,
    reason       VARCHAR NOT NULL,
    detail       VARCHAR,
    text_source  VARCHAR,
    at_utc       TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (item_id, model)
)
"""

#: Which text a model call is worth spending on first. A transcript is the
#: thing itself; an article is written argument; a description is show notes.
_DEPTH_RANK = {"transcript": 0, "article": 1, "notes": 2, "unknown": 3}


def rank_candidates(items: pd.DataFrame) -> pd.DataFrame:
    """Order items so a truncated run still covers the most creators.

    Two rules, in this order:

    1. **Round-robin by creator.** Within each creator, items are ranked best
       first (deepest text, then freshest). The global order takes every
       creator's #1 before anyone's #2. The Creators tab needs *a* take per
       creator far more than it needs four takes from the one creator who
       publishes daily, and a budget-limited run is the normal case.
    2. **Depth, then recency, breaks ties across creators.** A creator with a
       transcript is served before a creator with only show notes, and within
       a rank the newest item goes first.

    Returns the frame with ``depth``, ``depth_rank``, ``creator_rank`` and
    ``substantive_chars`` added, sorted.
    """
    from fpl_edge.ingest.content.analyze import depth_for, substantive_text

    if items.empty:
        return items.assign(depth=[], depth_rank=[], creator_rank=[],
                            substantive_chars=[])
    out = items.copy()
    out["published_at"] = pd.to_datetime(out["published_at"], utc=True)
    out["depth"] = [depth_for(t) for t in out["text_source"]]
    out["depth_rank"] = [_DEPTH_RANK.get(d, 3) for d in out["depth"]]
    out["substantive_chars"] = [len(substantive_text(t)) for t in out["text"]]
    out = out.sort_values(["creator", "depth_rank", "published_at"],
                          ascending=[True, True, False])
    out["creator_rank"] = out.groupby("creator").cumcount()
    return out.sort_values(["creator_rank", "depth_rank", "published_at"],
                           ascending=[True, True, False]).reset_index(drop=True)


def _writer(db: str):
    """A leased writer: holds DuckDB's single write lock only while writing.

    Three other agents write this file. A run that held the lock for its whole
    wall-clock (half an hour of model calls) would starve every one of them.
    """
    from fpl_edge.store.warehouse import LeasedWarehouse

    return LeasedWarehouse(db, lock_timeout_s=120.0)


def _write_with_retry(db: str, fn, *, attempts: int = 5) -> None:
    """Run ``fn(warehouse)`` inside a short lease, retrying on contention.

    Losing a race for the write lock is expected here, not exceptional, so it
    backs off and tries again instead of throwing away a model call that has
    already been paid for.
    """
    import random
    import time

    last: Exception | None = None
    for attempt in range(attempts):
        lease = _writer(db)
        try:
            fn(lease)
            return
        except Exception as exc:  # retried on contention, re-raised otherwise
            last = exc
            if "lock" not in str(exc).lower() and "conflict" not in str(exc).lower():
                raise
            time.sleep(min(30.0, 2.0 ** attempt) * (0.5 + random.random()))
        finally:
            lease.release()
    raise RuntimeError(f"could not take the write lock after {attempts} tries") from last


def cmd_analyze(args: argparse.Namespace) -> int:
    """Send stored text to Claude for a structured read; store what comes back.

    Resumable and time-budgeted. It never fetches anything: every byte it
    analyses is already in ``content_item``, put there by ``ingest``. In
    particular it does NOT transcribe audio and does not touch the robots gate
    in ``youtube.py`` -- items that only have show notes are analysed as show
    notes, and labelled as such.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor

    from fpl_edge.ingest.content.analyze import (
        AnalysisUnavailable,
        analysis_is_empty,
        analyze_transcript,
        claims_from_analysis,
        is_scoreable,
        store_analysis,
        validate_model_id,
    )
    from fpl_edge.ingest.content.store import ContentStore

    model = validate_model_id(args.model)
    started = time.monotonic()
    deadline = started + args.budget_s if args.budget_s else None

    # The ledger is the only thing that needs a writer before the work starts.
    _write_with_retry(args.db, lambda wh: wh.sql(_SKIP_DDL))

    since = _now() - dt.timedelta(days=args.since) if args.since else None
    with Warehouse(args.db, read_only=True) as wh:
        params: list[object] = [model, model]
        where = ["i.text IS NOT NULL", "i.text <> ''"]
        if since is not None:
            where.append("i.published_at >= ?")
            params.append(since)
        if args.creator:
            where.append("i.creator = ?")
            params.append(args.creator)
        skip_join = (
            "" if args.retry_skipped else
            "LEFT JOIN content_analysis_skip s "
            "  ON s.item_id = i.item_id AND s.model = ? "
        )
        if args.retry_skipped:
            params.pop(1)
        where.append("a.item_id IS NULL")
        if not args.retry_skipped:
            where.append("s.item_id IS NULL")
        items = wh.sql(
            "SELECT i.item_id, i.source_key, i.creator, i.kind, i.title, i.url, "
            "       i.published_at, i.text_source, i.text "
            "FROM content_item i "
            "LEFT JOIN content_analysis a "
            "  ON a.item_id = i.item_id AND a.model = ? "
            f"{skip_join}"
            f"WHERE {' AND '.join(where)}",
            params,
        )
        total_in_window = int(wh.sql(
            "SELECT count(*) c FROM content_item WHERE ? IS NULL OR published_at >= ?",
            [since, since],
        ).iloc[0]["c"])
        resolver = build_resolver(wh)
        calendar, cal_report = load_calendar(wh)

    ranked = rank_candidates(items)
    if args.limit:
        ranked = ranked.head(args.limit)

    print(f"model: {model}")
    print(f"window: {args.since or 'all'} days -> {total_in_window} stored items, "
          f"{len(items)} not yet analysed by this model, {len(ranked)} queued")
    print(cal_report.render())
    by_depth = ranked["depth"].value_counts().to_dict() if not ranked.empty else {}
    print(f"queued by depth: {by_depth}; creators queued: "
          f"{ranked['creator'].nunique() if not ranked.empty else 0}")
    if args.budget_s:
        print(f"budget: {args.budget_s}s wall-clock, {args.workers} worker(s)")

    if ranked.empty:
        print("\nnothing to do: every item in this window is already analysed "
              "or already recorded as skipped")
        return 0

    skipped: list[tuple[str, str, str, str]] = []  # item_id, ts, reason, detail
    queue = []
    for row in ranked.itertuples(index=False):
        if row.substantive_chars < args.min_chars:
            skipped.append((
                row.item_id, row.text_source, "too_thin",
                (f"{row.substantive_chars} substantive chars after links and "
                 f"separators (< {args.min_chars}); the notes are promotional "
                 f"furniture only"),
            ))
            continue
        queue.append(row)

    print(f"pre-filtered: {len(skipped)} items carry too little prose to analyse; "
          f"{len(queue)} will be sent to the model")
    if args.dry_run:
        for row in queue[:20]:
            print(f"  {row.creator_rank}  {row.depth:<10} {row.substantive_chars:>6}c  "
                  f"{str(row.published_at)[:10]}  {row.creator[:22]:<22} {row.title[:52]}")
        print(f"  ... {max(0, len(queue) - 20)} more")
        return 0

    stored = empty = failed = 0
    claims_written = 0
    unresolved_names: list[str] = []
    fatal: str | None = None
    spent: list[float] = []

    def analyse(row):
        t0 = time.monotonic()
        return row, analyze_transcript(
            title=row.title, creator=row.creator, text=row.text,
            text_source=row.text_source,
        ), time.monotonic() - t0

    def persist(row, analysis) -> None:
        nonlocal stored, claims_written
        # gw/season are inferred from THIS item's own published_at, so a call
        # is filed against the gameweek that was next when it was published --
        # never one that had already happened.
        inferred = calendar.next_after(pd.Timestamp(row.published_at).to_pydatetime())
        default_gw = int(inferred[1]) if inferred else 1
        season = inferred[0] if inferred else "2026-27"
        claims = []
        if is_scoreable(row.text_source):
            item = ContentItem(
                item_id=row.item_id, source_key=row.source_key, creator=row.creator,
                kind=row.kind, title=row.title, url=row.url,
                published_at=pd.Timestamp(row.published_at).to_pydatetime(),
                text=row.text,
                fetched_at=pd.Timestamp(row.published_at).to_pydatetime(),
                text_source=row.text_source,
            )
            claims, dropped = claims_from_analysis(
                analysis, item=item, resolver=resolver,
                default_gw=default_gw, season=season, model=model,
            )
            unresolved_names.extend(dropped)

        def _write(wh):
            nonlocal claims_written
            store_analysis(wh, row.item_id, analysis, model=model,
                           text_source=row.text_source, chars=len(row.text),
                           substantive_chars=int(row.substantive_chars))
            if claims:
                store = ContentStore.__new__(ContentStore)
                store.wh = wh
                claims_written += store.insert_claims(claims)

        _write_with_retry(args.db, _write)
        stored += 1

    def note_skip(row, reason, detail) -> None:
        skipped.append((row.item_id, row.text_source, reason, detail))

    def out_of_time() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            pending, cursor = [], 0
            while (cursor < len(queue) or pending) and fatal is None:
                while (len(pending) < max(1, args.workers) and cursor < len(queue)
                       and not out_of_time()):
                    pending.append(pool.submit(analyse, queue[cursor]))
                    cursor += 1
                if not pending:
                    break
                future = pending.pop(0)
                try:
                    row, analysis, took = future.result()
                except AnalysisUnavailable as exc:
                    # No backend is not a per-item failure: it is the whole
                    # run. Stop rather than march through 400 items failing.
                    fatal = str(exc)
                    break
                except Exception as exc:  # noqa: BLE001 - one bad item
                    failed += 1
                    print(f"  FAIL  {type(exc).__name__}: {str(exc)[:120]}", flush=True)
                    continue
                spent.append(took)
                if analysis_is_empty(analysis):
                    empty += 1
                    note_skip(row, "no_positions",
                              "the model read the text and found no summary and "
                              "no calls in it")
                    print(f"  none  {took:5.1f}s  {row.depth:<10} "
                          f"{row.creator[:22]:<22} {row.title[:44]}", flush=True)
                    continue
                persist(row, analysis)
                print(f"  ok    {took:5.1f}s  {row.depth:<10} "
                      f"{row.creator[:22]:<22} {row.title[:44]}", flush=True)
            for future in pending:
                future.cancel()
    finally:
        if skipped:
            now = _now()
            rows = [(i, model, r, d, ts, now) for i, ts, r, d in skipped]

            def _write_skips(wh):
                for r in rows:
                    wh.sql("INSERT OR REPLACE INTO content_analysis_skip "
                           "VALUES (?, ?, ?, ?, ?, ?)", list(r))

            _write_with_retry(args.db, _write_skips)

    elapsed = time.monotonic() - started
    print()
    print(f"analysed:        {stored} items stored in content_analysis")
    print(f"claims written:  {claims_written} (extractor llm:{model}; show-note "
          f"sources deliberately produce none -- see analyze.is_scoreable)")
    if unresolved_names:
        uniq = sorted(set(unresolved_names))
        print(f"names dropped rather than guessed: {len(unresolved_names)} calls, "
              f"{len(uniq)} distinct, e.g. {', '.join(uniq[:6])}")
    print(f"empty results:   {empty} items where the model found no positions "
          f"(recorded in content_analysis_skip, NOT stored as a take)")
    print(f"pre-skipped:     {len(skipped) - empty} items below "
          f"{args.min_chars} substantive chars")
    print(f"errors:          {failed} items failed mid-call and were left for a re-run")
    print(f"not reached:     {max(0, len(queue) - stored - empty - failed)} queued "
          f"items left (budget or limit)")
    if spent:
        print(f"cost:            {len(spent)} model calls, {elapsed:.0f}s wall-clock, "
              f"{sum(spent) / len(spent):.1f}s mean per call "
              f"(min {min(spent):.1f}s, max {max(spent):.1f}s)")
    else:
        print(f"cost:            0 model calls, {elapsed:.0f}s wall-clock")
    if fatal:
        print()
        print(f"STOPPED: no usable analysis backend. {fatal}")
        print("Nothing was invented in its place; the queue is untouched and a "
              "re-run resumes from here.")
        return 1
    return 0


_TRANSCRIBE_SKIP_DDL = """
CREATE TABLE IF NOT EXISTS content_transcribe_skip (
    item_id  VARCHAR PRIMARY KEY,
    reason   VARCHAR NOT NULL,
    detail   VARCHAR,
    at_utc   TIMESTAMP WITH TIME ZONE NOT NULL
)
"""


def _asr_fetcher(delay: float):
    """The HTTP client the transcription step uses. Politeness lives here.

    ``archive=False`` is deliberate and is not a loss of provenance. The
    fetcher's archive writes a fresh timestamped copy of every body it sees,
    which for a 20 MB episode means storing the same audio twice on the first
    run and again on every later one. The audio cache in
    :mod:`fpl_edge.ingest.content.asr` is the archive for this path instead:
    content-addressed by URL, written by atomic rename, and consulted BEFORE
    the network, so a re-run of a failed batch downloads nothing. Feed bodies
    on this path are transient; ``ingest`` and ``probe`` already archive those.

    The delay floor is 2 seconds regardless of ``--delay``: podcast audio is
    tens of megabytes per request off individual creators' hosting.
    """
    from fpl_edge.ingest.content.fetch import ContentFetcher

    return ContentFetcher("asr", delay_s=max(delay, 2.0), archive=False)


def cmd_transcribe(args: argparse.Namespace) -> int:
    """Local ASR and panel captions -> timestamped ``transcript_segment`` rows.

    Resumable, time-budgeted, and single-item-at-a-time against the write lock.
    Three properties are worth stating because each was a decision:

    **Nothing here spends Anthropic tokens.** The transcription engine is
    MLX-Whisper running locally on the Metal GPU. If it is not installed the
    command prints the install line and exits 1; there is no remote fallback,
    by design (see :mod:`fpl_edge.ingest.content.asr`).

    **The write lock is never held across a transcription.** A 40-minute
    episode takes minutes to decode; two other agents are writing this file.
    So the queue is read through a read-only connection, each item is
    transcribed with no connection open at all, and the write is a single
    short lease holding a few INSERTs against rows already in memory.

    **A failure stores nothing and says why.** Partial transcripts, empty
    results, refused downloads and missing audio all land in
    ``content_transcribe_skip`` with a reason, and the item keeps its existing
    text. The one thing this command will not do is write a transcript it
    knows to be incomplete.
    """
    import time

    from fpl_edge.ingest.content import asr
    from fpl_edge.ingest.content.sources import BY_KEY
    from fpl_edge.ingest.content.youtube import (
        PANEL_CREATORS,
        PANEL_WITHOUT_SOURCE,
        divergence_from_roster,
        fetch_panel_captions,
        panel_fetcher,
    )

    started = time.monotonic()
    deadline = started + args.budget_s if args.budget_s else None

    status = asr.backend_status()
    print(status.render())
    if not status.ready and not args.dry_run:
        print("\nSTOPPED: no local transcription engine. Nothing was transcribed "
              "and nothing was written. There is deliberately no remote "
              "fallback -- transcription must not spend Anthropic tokens.")
        return 1

    kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
    creators = (frozenset({args.creator}) if args.creator
                else frozenset() if args.any_creator else PANEL_CREATORS)
    print(f"panel:    {len(PANEL_CREATORS)} creators; "
          f"{'ALL creators (--any-creator)' if not creators else str(len(creators)) + ' queued'}"
          f"; no source in registry for: {', '.join(PANEL_WITHOUT_SOURCE)}")
    print(f"kinds:    {', '.join(kinds)}")

    since = _now() - dt.timedelta(days=args.since) if args.since else None
    with Warehouse(args.db, read_only=True) as wh:
        stored_enclosures, enclosure_origin = asr.enclosure_lookup(wh)
        # The ledger is READ here and CREATED later, after --dry-run has had
        # its chance to return. A dry run that creates two tables in a shared
        # warehouse is not a dry run, and "it is only DDL" is exactly the
        # argument that makes a --dry-run flag untrustworthy.
        ledger_exists = int(wh.sql(
            "SELECT count(*) c FROM information_schema.tables "
            "WHERE table_name = 'content_transcribe_skip'"
        ).iloc[0]["c"]) > 0
        cols = ("i.item_id, i.source_key, i.creator, i.kind, i.title, i.url, "
                "i.published_at, i.text_source")
        where = ["i.text_source <> 'transcript'",
                 f"i.kind IN ({', '.join('?' * len(kinds))})",
                 "t.item_id IS NULL"]
        if ledger_exists:
            where.append("s.item_id IS NULL")
        params: list[object] = list(kinds)
        if creators:
            where.append(f"i.creator IN ({', '.join('?' * len(creators))})")
            params.extend(sorted(creators))
        if since is not None:
            where.append("i.published_at >= ?")
            params.append(since)
        queue = wh.sql(
            f"SELECT {cols} FROM content_item i "
            + ("LEFT JOIN content_transcribe_skip s ON s.item_id = i.item_id "
               if ledger_exists else "")
            + f"LEFT JOIN (SELECT DISTINCT item_id FROM transcript_segment) t "
            f"  ON t.item_id = i.item_id "
            f"WHERE {' AND '.join(where)} "
            # Newest first: a creator's take on the gameweek that has not been
            # played is worth more than their take on one that has.
            f"ORDER BY i.published_at DESC",
            params,
        )
        already = int(wh.sql(
            "SELECT count(*) c FROM content_item WHERE text_source = 'transcript'"
        ).iloc[0]["c"])
        # The curated roster (panel.py) may have moved ahead of the caption
        # ceiling in youtube.PANEL_CREATORS. Say so; do not quietly widen.
        drift = divergence_from_roster(wh)

    col_note = (f"{enclosure_origin} ({len(stored_enclosures)} urls)"
                if enclosure_origin != "none" else
                "no stored column yet -- re-parsing each podcast feed's "
                "<enclosure> instead")
    print(f"audio urls from:      {col_note}")
    print(f"already transcribed:  {already} items (skipped, never re-done)")
    if drift:
        print(f"roster ahead of the caption ceiling: {', '.join(drift)} are on "
              f"the curated panel but NOT in youtube.PANEL_CREATORS, so their "
              f"videos are refused. Raising the ceiling is an owner decision "
              f"and an edit to that constant.")
    print(f"queued:               {len(queue)} items"
          + (f", newest {str(queue.iloc[0]['published_at'])[:10]}" if len(queue) else ""))
    if args.limit:
        queue = queue.head(args.limit)
        print(f"limited to:           {len(queue)}")
    if queue.empty:
        print("\nnothing to do")
        return 0

    # -- audio urls, resolved before any transcription starts ---------------
    # Only for queued podcast items whose audio is not already in a stored
    # column, and at most one feed fetch per source. This runs on --dry-run
    # too: "which of these can actually be transcribed" is the question the
    # dry run exists to answer, and it cannot be answered without the feed.
    # It reads feeds and downloads no audio; the message below says so rather
    # than claiming the run touched nothing.
    fetcher = None
    feeds_read = 0
    enclosures: dict[str, str] = dict(stored_enclosures)
    needs_feed = sorted({
        str(row.source_key) for row in queue.itertuples(index=False)
        if row.kind == "podcast" and str(row.item_id) not in enclosures
        and str(row.source_key) in BY_KEY
    })
    if needs_feed and "podcast" in kinds:
        fetcher = _asr_fetcher(args.delay)
        for key in needs_feed:
            found, http = asr.enclosures_from_feed(fetcher, BY_KEY[key])
            feeds_read += 1
            print(f"  feed {key}: HTTP {http}, {len(found)} enclosures")
            enclosures.update(found)

    if args.dry_run:
        print()
        for row in queue.head(25).itertuples(index=False):
            url = enclosures.get(row.item_id)
            print(f"  {str(row.published_at)[:10]}  {row.kind:<8} "
                  f"{row.creator[:22]:<22} {'audio' if url or row.kind == 'youtube' else 'NO-AUDIO':<8} "
                  f"{row.title[:46]}")
        print(f"  ... {max(0, len(queue) - 25)} more")
        print(f"\ndry run: {feeds_read} feed(s) read to resolve audio urls. "
              f"No audio downloaded, nothing transcribed, nothing written -- "
              f"not even the ledger DDL.")
        return 0

    # First write of the run, and the point past which --dry-run cannot reach.
    _write_with_retry(args.db, lambda wh: (wh.sql(_TRANSCRIBE_SKIP_DDL),
                                           asr.ensure_schema(wh)))

    if fetcher is None:
        fetcher = _asr_fetcher(args.delay)
    yt_fetcher = panel_fetcher() if "youtube" in kinds else None

    done = failed = skipped = 0
    audio_s = 0.0
    asr_wall = 0.0
    segments_written = 0
    stale_dropped = 0
    refused: str | None = None
    skips: list[tuple[str, str, str]] = []

    def out_of_time() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    try:
        for row in queue.itertuples(index=False):
            if out_of_time():
                print("  -- budget reached; the rest of the queue is untouched")
                break
            if refused:
                break
            item_id = str(row.item_id)
            try:
                if row.kind == "youtube":
                    from fpl_edge.ingest.content.youtube import is_panel_creator
                    from fpl_edge.platform.scripts.creators import youtube_id

                    if not is_panel_creator(str(row.creator)):
                        # --any-creator widens podcast ASR, which is local work
                        # on audio the creator published for download. It does
                        # NOT widen the caption route: bounded scale is the
                        # entire basis of the 2026-08-27 policy change.
                        skips.append((item_id, "off_panel_youtube",
                                      f"{row.creator} is not on the curated panel"))
                        skipped += 1
                        continue

                    vid = youtube_id(str(row.url))
                    if not vid:
                        skips.append((item_id, "no_video_id",
                                      f"could not read a video id from {row.url!r}"))
                        skipped += 1
                        continue
                    t0 = time.monotonic()
                    caps = fetch_panel_captions(yt_fetcher, vid, creator=str(row.creator))
                    if caps.refused:
                        # The source declining. Recorded, obeyed, run stopped.
                        refused = (f"YouTube returned {caps.status} on "
                                   f"{caps.route} for {vid}")
                        skips.append((item_id, f"refused_{caps.status}", caps.route))
                        break
                    if not caps.ok:
                        skips.append((item_id, "no_captions", caps.route))
                        skipped += 1
                        continue
                    result = asr.transcription_from_captions(
                        caps.lines, video_id=vid, route=caps.route,
                        wall_seconds=time.monotonic() - t0)
                    derivation = "captions"
                else:
                    url = enclosures.get(item_id, "")
                    if not url:
                        skips.append((
                            item_id, "no_audio_url",
                            (f"no enclosure for this item in "
                             f"{enclosure_origin}, nor in the feed's window")))
                        skipped += 1
                        continue
                    got = asr.fetch_audio(fetcher, url)
                    if not got.ok:
                        if got.error and got.error.startswith("refused_"):
                            refused = f"{url} returned {got.status}"
                            skips.append((item_id, got.error, url))
                            break
                        skips.append((item_id, got.error or "audio_unavailable", url))
                        skipped += 1
                        continue
                    assert got.path is not None
                    result = asr.transcribe_file(
                        got.path, audio_url=url,
                        model=args.model or asr.DEFAULT_MODEL, status=status)
                    derivation = "asr"
                    audio_s += result.audio_seconds or 0.0
                    asr_wall += result.wall_seconds
            except asr.AsrUnavailable:
                raise
            except (asr.PartialTranscript, asr.AudioUnavailable) as exc:
                # The loud failure the brief asks for: nothing stored, reason
                # recorded, item left for a re-run.
                failed += 1
                skips.append((item_id, type(exc).__name__, str(exc)[:400]))
                print(f"  FAIL  {type(exc).__name__}: {str(exc)[:140]}", flush=True)
                continue
            except Exception as exc:  # noqa: BLE001 - one bad item, not the run
                failed += 1
                skips.append((item_id, type(exc).__name__, str(exc)[:400]))
                print(f"  FAIL  {type(exc).__name__}: {str(exc)[:140]}", flush=True)
                continue

            def _write(wh, _id=item_id, _res=result, _der=derivation):
                nonlocal segments_written, stale_dropped
                segments_written += asr.store_transcription(
                    wh, _id, _res, derivation=_der)
                stale_dropped += asr.stale_analyses(wh, _id)

            _write_with_retry(args.db, _write)
            done += 1
            print(f"  ok    {derivation:<8} {str(row.published_at)[:10]}  "
                  f"{row.creator[:20]:<20} {result.render()}", flush=True)
    finally:
        if skips:
            now = _now()
            rows = [(i, r, d, now) for i, r, d in skips]

            def _write_skips(wh):
                for r in rows:
                    wh.sql("INSERT OR REPLACE INTO content_transcribe_skip "
                           "VALUES (?, ?, ?, ?)", list(r))

            _write_with_retry(args.db, _write_skips)
        if fetcher is not None:
            fetcher.close()
        if yt_fetcher is not None:
            yt_fetcher.close()

    elapsed = time.monotonic() - started
    print()
    print(f"transcribed:     {done} items, {segments_written} timestamped segments")
    print(f"failed:          {failed} items -- NOTHING was stored for these; "
          f"reasons in content_transcribe_skip")
    print(f"skipped:         {skipped} items with no audio or no captions")
    print(f"stale analyses:  {stale_dropped} show-notes reads deleted so "
          f"`analyze` re-reads the transcript (re-run analyze to refill)")
    if asr_wall > 0:
        print(f"ASR rate:        {audio_s / 60:.1f} min of audio in "
              f"{asr_wall / 60:.1f} min of transcription = "
              f"{audio_s / asr_wall:.1f} min audio per min wall clock")
    print(f"wall clock:      {elapsed / 60:.1f} min total "
          f"(includes downloads and DB writes)")
    print(f"not reached:     {max(0, len(queue) - done - failed - skipped)} queued "
          f"items left; re-run resumes from here")
    if refused:
        print()
        print(f"STOPPED ON REFUSAL: {refused}")
        print("A 403 or 429 is the source declining. It is recorded and obeyed; "
              "the run stops rather than routing around it.")
        return 1
    return 0


def cmd_link_identities(args: argparse.Namespace) -> int:
    """Link creators to FPL entries where verified evidence already exists.

    Deliberately small. It writes a link ONLY where a creator's name is
    exactly, after accent folding, a name the FPL API itself reported for an
    entry. No nickname matching, no channel-name resemblance, no guessed IDs.
    Everything else is written down as unresolved WITH its reason.
    """
    from fpl_edge.interfaces.creators import link_creator_entries

    ddl = """
    CREATE TABLE IF NOT EXISTS creator_entry (
        creator     VARCHAR NOT NULL,
        entry_id    BIGINT,
        player_name VARCHAR,
        entry_name  VARCHAR,
        method      VARCHAR NOT NULL,
        verified    BOOLEAN NOT NULL,
        reason      VARCHAR,
        as_of       TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (creator)
    )
    """
    with Warehouse(args.db, read_only=True) as wh:
        links = link_creator_entries(wh)

    as_of = _now()
    resolved = [x for x in links if x.entry_id is not None]

    def _write(wh):
        wh.sql(ddl)
        for x in links:
            wh.sql("INSERT OR REPLACE INTO creator_entry VALUES (?,?,?,?,?,?,?,?)",
                   [x.creator, x.entry_id, x.player_name, x.entry_name,
                    x.method, x.verified, x.reason or None, as_of])

    if not args.dry_run:
        _write_with_retry(args.db, _write)

    for x in resolved:
        print(f"  LINK  {x.creator:<26} -> entry {x.entry_id} "
              f"({x.player_name}) via {x.method}")
    print(f"\nlinked:     {len(resolved)} of {len(links)} creators")
    print(f"unresolved: {len(links) - len(resolved)}")
    from collections import Counter
    for reason, n in Counter(x.reason for x in links if x.entry_id is None).most_common():
        print(f"  {n:>3}  {reason}")
    if not resolved:
        print("\nZero links is the honest result here, not a failure: every "
              "creator in the roster is a CHANNEL name, and no channel name "
              "equals a name the FPL API reported for an entry. The alternative "
              "-- matching 'Let's Talk FPL' to 'Andy LTFPL' on resemblance -- "
              "would be a guess written down as an identity.")
    if args.dry_run:
        print("\n--dry-run: nothing written")
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
    p.add_argument("--dry-run", action="store_true",
                   help="print the queue and which items have audio; fetch and "
                        "transcribe nothing")
    p.set_defaults(func=cmd_transcribe)

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


if __name__ == "__main__":
    sys.exit(main())
