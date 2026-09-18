"""Transcription and the audio cache sweep.

``cmd_transcribe`` runs mlx-whisper locally on the Metal GPU and spends no model
tokens; ``cmd_retention`` deletes cached audio whose transcript and provenance
hash both survive, so integrity outlives the file.

Split out of ``fpl_edge/ingest/content/pipeline.py`` (ARCHITECTURE_REVIEW.md
Section 3 and Section 4 row 16).
"""

from __future__ import annotations
import argparse
import datetime as dt
from fpl_edge.store import Warehouse

from fpl_edge.ingest.content.analyse_cmd import RELEVANCE_THRESHOLD, _write_with_retry, relevance_score
from fpl_edge.ingest.content.pipeline_common import _now, build_resolver


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
                "i.published_at, i.text_source, i.text")
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
        # The strict player index the relevance gate scores against. A
        # warehouse with no dim_player yet (content-only test dbs) is not an
        # error: the gate then scores on terms/panel/recency alone and the
        # breakdown says "players:unavailable" rather than guessing.
        try:
            gate_resolver = build_resolver(wh)
        except Exception:  # noqa: BLE001 - absence of players, not a failure
            gate_resolver = None

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

    # The relevance gate, BEFORE --limit so the budget is spent on the items
    # worth it. Below-threshold items are recorded (not on --dry-run, which
    # writes nothing) in content_transcribe_skip as ``relevance:<score>``
    # with the point breakdown -- a named reason, never silence.
    gated: list[tuple[str, str, str]] = []
    # getattr, because tests drive this command with hand-built Namespaces
    # that predate the gate; absent means "the default bar", not "off".
    min_relevance = float(getattr(args, "min_relevance", RELEVANCE_THRESHOLD))
    if min_relevance > 0 and len(queue):
        scoring_now = _now()
        keep: list[int] = []
        for pos, row in enumerate(queue.itertuples(index=False)):
            score, why = relevance_score(
                title=str(row.title or ""), text=str(row.text or ""),
                resolver=gate_resolver, creator=str(row.creator or ""),
                published_at=row.published_at, now=scoring_now,
            )
            if score >= min_relevance:
                keep.append(pos)
            else:
                gated.append((str(row.item_id), f"relevance:{score:g}",
                              f"below threshold {min_relevance:g}: {why}"))
        queue = queue.iloc[keep].reset_index(drop=True)
        print(f"relevance gate:       {len(gated)} below {min_relevance:g}, "
              f"{len(queue)} pass (deterministic; scores recorded)")

    if args.limit:
        queue = queue.head(args.limit)
        print(f"limited to:           {len(queue)}")
    if queue.empty:
        if gated and not args.dry_run:
            # The gate's verdicts are still worth their ledger rows: without
            # them every below-threshold item would be re-scored forever.
            now = _now()
            rows = [(i, r, d, now) for i, r, d in gated]

            def _write_gated(wh):
                wh.sql(_TRANSCRIBE_SKIP_DDL)
                for r in rows:
                    wh.sql("INSERT OR REPLACE INTO content_transcribe_skip "
                           "VALUES (?, ?, ?, ?)", list(r))

            _write_with_retry(args.db, _write_gated)
            print(f"recorded {len(gated)} relevance skips")
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
    stale_cleanup_failed = 0
    refused: str | None = None
    # The gate's below-threshold verdicts ride the same skip-ledger write as
    # the loop's own skips (the finally block below).
    skips: list[tuple[str, str, str]] = list(gated)

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
                    from fpl_edge.ingest.content.urls import youtube_id

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
                nonlocal segments_written
                segments_written += asr.store_transcription(
                    wh, _id, _res, derivation=_der)

            _write_with_retry(args.db, _write)

            # Deliberately a SECOND lease, not part of the write above. The
            # transcript is the expensive thing -- minutes of GPU per episode
            # -- and dropping the now-stale show-notes analysis is a tidy-up
            # that costs nothing to defer. Sharing one transaction meant a
            # failure inside content_analysis rolled the transcript back and
            # exited the whole run: that is what the 2026-09-01 and -09-02
            # nightly failures were, both of them ~40 minutes in.
            def _drop_stale(wh, _id=item_id):
                nonlocal stale_dropped
                stale_dropped += asr.stale_analyses(wh, _id)

            try:
                _write_with_retry(args.db, _drop_stale)
            except Exception as exc:  # noqa: BLE001 - tidy-up, never the run
                stale_cleanup_failed += 1
                print(f"  warn  transcript stored; stale-analysis cleanup "
                      f"failed ({type(exc).__name__}: {str(exc)[:120]}). "
                      f"Re-run `analyze --retry-skipped` to refresh it.",
                      flush=True)
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
    if stale_cleanup_failed:
        print(f"cleanup failed:  {stale_cleanup_failed} items kept their "
              f"transcript but not their stale-analysis deletion; those items "
              f"still show a show-notes read until analyze re-reads them")
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


def cmd_retention(args: argparse.Namespace) -> int:
    """Sweep the ASR audio cache. Deletes ONLY files whose item has a stored
    transcript AND a transcript_provenance row carrying audio_sha256 -- the
    hash outlives the file. Everything else is kept, always."""
    from fpl_edge.ingest.content import asr

    with Warehouse(args.db, read_only=True) as wh:
        sweep = asr.sweep_audio_cache(wh, dry_run=args.dry_run)
    print(sweep.render())
    if args.dry_run:
        print("\n--dry-run: nothing deleted")
    return 0
