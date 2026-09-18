"""Claim extraction and the relevance gate: the commands that spend tokens.

``cmd_analyze`` is the single largest consumer of the model quota in this repo,
so the candidate ranking and the relevance threshold that bound it live beside
it rather than in the dispatcher. ``_write_with_retry`` is here because every
command that writes under contention needs it and this is the module that
writes most.

Split out of ``fpl_edge/ingest/content/pipeline.py`` (ARCHITECTURE_REVIEW.md
Section 3 and Section 4 row 16). ``pipeline.py`` keeps its name, its argparse
main and its ``-m`` target, and re-exports every name moved here.
"""

from __future__ import annotations
import argparse
import datetime as dt
import json
import pandas as pd
from fpl_edge.ingest.content.calendar import load_calendar
from fpl_edge.ingest.content.claims import ExtractionStats, extract_from_item
from fpl_edge.ingest.content.models import ContentItem
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse
import re as _re

from fpl_edge.ingest.content.pipeline_common import UTC, _now, build_resolver


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


def cmd_backfill_insights(args: argparse.Namespace) -> int:
    """Recover insights from analyses already on disk, without paying again.

    ``content_insight`` was written by nothing for its whole existence, so
    every analysis stored before the wiring landed carries observations that
    were parsed, validated and then dropped. They are still in
    ``content_analysis.analysis_json``. Re-reading them costs nothing: no
    network, no model call, no re-fetch.

    The one distinction that matters here is between an analysis that HAS an
    empty ``insights`` list -- a model read the item and found no observations,
    which is an answer -- and one with no ``insights`` key at all, written by a
    prompt that predated the field and therefore never asked. The first is
    complete; the second needs a paid re-analysis, and this command counts them
    separately rather than reporting one number that hides the difference.
    """
    from fpl_edge.ingest.content.analyze import (
        TranscriptAnalysis,
        analysis_has_insight_field,
        insights_from_analysis,
        store_insights,
    )
    from fpl_edge.ingest.content.clubs import club_resolver
    from fpl_edge.ingest.content.store import ContentStore

    with Warehouse(args.db) as warehouse:
        ContentStore(warehouse).migrate()
        resolver = build_resolver(warehouse)
        calendar, _ = load_calendar(warehouse)
        clubs_by_season: dict[str, object] = {}

        rows = warehouse.sql(
            "SELECT a.item_id, a.analysis_json, i.source_key, i.creator, i.kind, "
            "       i.title, i.url, i.published_at, i.text, i.text_source "
            "FROM content_analysis a JOIN content_item i USING (item_id) "
            "ORDER BY i.published_at"
        ).to_dict("records")

        never_asked, empty, written, dropped = 0, 0, 0, []
        for r in rows:
            try:
                payload = json.loads(r["analysis_json"])
            except (TypeError, ValueError):
                dropped.append(("unreadable_json", str(r["item_id"])))
                continue
            if not analysis_has_insight_field(payload):
                never_asked += 1
                continue
            analysis = TranscriptAnalysis.model_validate(payload)
            if not (analysis.insights or []):
                empty += 1
                continue

            published = pd.Timestamp(r["published_at"])
            if published.tzinfo is None:
                published = published.tz_localize(UTC)
            item = ContentItem(
                item_id=r["item_id"], source_key=r["source_key"],
                creator=r["creator"], kind=r["kind"], title=r["title"],
                url=r["url"], published_at=published.to_pydatetime(),
                text=r["text"] or "", fetched_at=published.to_pydatetime(),
                text_source=r["text_source"],
            )
            # Same gameweek rule the live path uses: inferred from THIS item's
            # own published_at, so an insight is filed against the gameweek that
            # was next when it was said.
            inferred = calendar.next_after(item.published_at)
            season = inferred[0] if inferred else "2026-27"
            default_gw = int(inferred[1]) if inferred else 1
            if season not in clubs_by_season:
                clubs_by_season[season] = club_resolver(warehouse, season)
            ins, ins_dropped = insights_from_analysis(
                analysis, item=item, resolver=resolver, default_gw=default_gw,
                season=season, text_source=item.text_source,
                clubs=clubs_by_season[season],
            )
            dropped.extend(ins_dropped)
            if ins:
                written += store_insights(warehouse, ins)

        total = int(warehouse.sql(
            "SELECT count(*) c FROM content_insight").iloc[0]["c"])

    print(f"analyses read:            {len(rows)}")
    print(f"  no `insights` key:      {never_asked}  (written before the field "
          f"existed -- these need a paid re-analysis, not a backfill)")
    print(f"  asked, found none:      {empty}  (a real answer, nothing to do)")
    print(f"insights written:         {written}")
    if dropped:
        from collections import Counter
        why = Counter(reason for reason, _ in dropped)
        print("dropped rather than stored: "
              + ", ".join(f"{n} {reason}" for reason, n in why.most_common()))
    print(f"content_insight now holds {total} rows")
    return 0


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


_CONTENTION_MARKERS: tuple[str, ...] = (
    "could not set lock on file",
    "conflicting lock is held",
    "is locked by another process",
    "database is locked",
    "lock timeout",
    "waiting for the lock",
    "transactioncontext error",
    "write-write conflict",
)


_TRANSIENT_ENGINE_MARKERS: tuple[str, ...] = (
    "invalid node type",
    "failed to delete all rows from index",
    "database has been invalidated",
)


def _is_contention(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _CONTENTION_MARKERS)


def _is_transient_engine_fault(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_ENGINE_MARKERS)


def _write_with_retry(db: str, fn, *, attempts: int = 5) -> None:
    """Run ``fn(warehouse)`` inside a short lease, retrying on contention.

    Losing a race for the write lock is expected here, not exceptional, so it
    backs off and tries again instead of throwing away a model call that has
    already been paid for. Anything that is NOT contention is raised
    immediately and unchanged -- a corrupted index, a constraint violation and
    a busy neighbour are three different problems and only one of them is
    fixed by waiting.
    """
    import random
    import time

    last: Exception | None = None
    for attempt in range(attempts):
        lease = _writer(db)
        try:
            fn(lease)
            # Fold the WAL into the database file before releasing the lease.
            #
            # This is the mitigation for the failure that cost 2026-09-01
            # through -09-03: DuckDB rebuilds a primary-key ART index by
            # REPLAYING the WAL every time the file is opened for writing, and
            # on 2026-09-03 that replay started answering ``Corrupted unique
            # ART index "PRIMARY_content_analysis_4": encountered an existing
            # gated leaf`` -- fatally, to every writer, so the whole warehouse
            # became unopenable until the WAL was quarantined. A checkpoint
            # ends each write with the index materialised in the file, so the
            # next open has almost no index replay to get wrong.
            #
            # Best-effort on purpose: a checkpoint that cannot run (another
            # reader mid-query) must not undo a write that succeeded.
            try:
                lease.sql("CHECKPOINT")
            except Exception as ckpt:  # noqa: BLE001 - write already committed
                print(f"  note  checkpoint after the write did not run "
                      f"({type(ckpt).__name__}: {str(ckpt)[:100]}); the write "
                      f"itself is committed", flush=True)
            return
        except Exception as exc:  # retried on contention, re-raised otherwise
            last = exc
            if not (_is_contention(exc) or _is_transient_engine_fault(exc)):
                raise
            time.sleep(min(30.0, 2.0 ** attempt) * (0.5 + random.random()))
        finally:
            lease.release()
    # Name the error that actually kept happening. "could not take the write
    # lock after 5 tries" on its own has sent two investigations to the wrong
    # place; the last exception is chained AND quoted.
    what = ("the write lock was busy" if _is_contention(last)
            else "the database engine kept failing "
                 "(try `pipeline repair-index --apply`)")
    raise RuntimeError(
        f"gave up writing after {attempts} tries: {what}. The last attempt "
        f"failed with {type(last).__name__}: {str(last)[:300]}"
    ) from last


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
        insights_from_analysis,
        is_scoreable,
        store_analysis,
        store_insights,
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
        # One resolver per season, built here because the pool below runs
        # after this warehouse is closed and opens none of its own. Keyed by
        # season because a club's name and code are season-scoped and an item
        # is filed against the season its own published_at lands in.
        from fpl_edge.ingest.content.clubs import club_resolver
        clubs_by_season = {
            str(sn): club_resolver(wh, str(sn))
            for sn in wh.sql("SELECT DISTINCT season FROM dim_team")["season"]
        }

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
    insights_written = 0
    insights_dropped: list[tuple[str, str]] = []
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
        nonlocal stored, claims_written, insights_written
        # gw/season are inferred from THIS item's own published_at, so a call
        # is filed against the gameweek that was next when it was published --
        # never one that had already happened.
        inferred = calendar.next_after(pd.Timestamp(row.published_at).to_pydatetime())
        default_gw = int(inferred[1]) if inferred else 1
        season = inferred[0] if inferred else "2026-27"
        claims = []
        # Initialised here, not only inside the branch below. `_write` closes
        # over `insights`, and a non-scoreable item (every show-notes row --
        # 432 of the 643 in this backlog) skipped the branch entirely, so the
        # closure raised ``NameError: cannot access free variable 'insights'``
        # and, before the per-item guard in the loop, took the whole run down
        # with it. An analyse pass could therefore never get past its first
        # show-notes item, which is the second reason -- alongside there being
        # no scheduled analyse task at all -- that this backlog sat at 122
        # analyses for a week.
        insights: list = []
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
            # The same analysis carries observations as well as calls. They are
            # extracted here rather than in a later pass because the analysis
            # object is what holds them, and re-deriving it would mean paying
            # the model twice for one reading. `text_source` gates show notes
            # out: an insight without a verbatim quote is not storable.
            insights, ins_dropped = insights_from_analysis(
                analysis, item=item, resolver=resolver,
                default_gw=default_gw, season=season, model=model,
                text_source=row.text_source,
                clubs=clubs_by_season.get(season),
            )
            insights_dropped.extend(ins_dropped)

        def _write(wh):
            nonlocal claims_written, insights_written
            store_analysis(wh, row.item_id, analysis, model=model,
                           text_source=row.text_source, chars=len(row.text),
                           substantive_chars=int(row.substantive_chars))
            if claims:
                store = ContentStore.__new__(ContentStore)
                store.wh = wh
                claims_written += store.insert_claims(claims)
            # Same write as the claims: an analysis whose insights failed to
            # store is an analysis that will never be re-read for them, because
            # store_analysis has already marked the item done.
            if insights:
                insights_written += store_insights(wh, insights)

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
                try:
                    persist(row, analysis)
                except Exception as exc:  # noqa: BLE001 - one bad write
                    # The same rule the model call already follows: one item
                    # that cannot be stored is one item, not the end of the
                    # run. Losing the remaining budget to a single failed
                    # write is how a 644-item backlog stays a 644-item
                    # backlog. Nothing was stored, so the item is still
                    # queued and the next firing retries it.
                    failed += 1
                    print(f"  FAIL  store {type(exc).__name__}: "
                          f"{str(exc)[:160]}", flush=True)
                    continue
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
    print(f"insights written: {insights_written} (content_insight; observations, "
          f"never scored -- nothing settles an observation)")
    if insights_dropped:
        from collections import Counter
        why = Counter(reason for reason, _ in insights_dropped)
        print("insights dropped rather than stored: "
              + ", ".join(f"{n} {reason}" for reason, n in why.most_common()))
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


RELEVANCE_PLAYER_PTS = 1.0


RELEVANCE_TERM_PTS = 1.0


RELEVANCE_PANEL_PTS = 2.0


RELEVANCE_RECENT_PTS = 1.0


RELEVANCE_RECENT_DAYS = 7


RELEVANCE_THRESHOLD = 3.0


_RELEVANCE_TERMS: tuple[tuple[str, _re.Pattern[str]], ...] = tuple(
    (name, _re.compile(pattern, _re.IGNORECASE))
    for name, pattern in (
        ("gameweek", (r"\b(?:gw\s*\d+|gameweek|game\s+week|double\s+gameweek|"
                      r"blank\s+gameweek|dgw|bgw)\b")),
        ("transfer", r"\btransfers?\b"),
        ("captain", r"\bcaptain(?:cy|s)?\b|\btriple\s+captain\b"),
        ("chip", r"\bwildcard\b|\bfree\s+hit\b|\bbench\s+boost\b"),
        ("fpl", (r"\bfpl\b|\bfantasy\s+premier\s+league\b|\bdifferentials?\b|"
                 r"\bprice\s+(?:rise|fall|change)s?\b")),
    )
)


def relevance_score(
    *,
    title: str,
    text: str,
    resolver=None,
    creator: str | None = None,
    published_at=None,
    now: dt.datetime | None = None,
) -> tuple[float, str]:
    """(score, breakdown) for one queued item. Deterministic and auditable.

    ``resolver`` is a :class:`~fpl_edge.ingest.content.resolve.
    SeasonResolvers` (or a bare PlayerResolver, or None when the warehouse
    has no players -- name points simply contribute nothing then, stated in
    the breakdown rather than guessed around).
    """
    corpus = f"{title or ''}\n{text or ''}"
    parts: list[str] = []
    score = 0.0

    if resolver is not None:
        res = resolver.for_season(None) if hasattr(resolver, "for_season") else resolver
        mentions = res.find_mentions(corpus)
        codes = {int(m.code) for m in mentions if m.reason == "ok" and m.code is not None}
        if codes:
            score += RELEVANCE_PLAYER_PTS * len(codes)
            parts.append(f"players:{len(codes)}")
    else:
        parts.append("players:unavailable")

    hits = [name for name, pattern in _RELEVANCE_TERMS if pattern.search(corpus)]
    if hits:
        score += RELEVANCE_TERM_PTS * len(hits)
        parts.append("terms:" + ",".join(hits))

    if creator:
        from fpl_edge.ingest.content.youtube import is_panel_creator

        if is_panel_creator(creator):
            score += RELEVANCE_PANEL_PTS
            parts.append("panel")

    if published_at is not None:
        published = pd.Timestamp(published_at)
        if published.tzinfo is None:
            published = published.tz_localize(UTC)
        now = now or _now()
        if (now - published.to_pydatetime()) <= dt.timedelta(days=RELEVANCE_RECENT_DAYS):
            score += RELEVANCE_RECENT_PTS
            parts.append("recent")

    return score, " ".join(parts) or "nothing matched"
