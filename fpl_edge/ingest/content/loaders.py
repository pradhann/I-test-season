"""Source kind -> :class:`ContentItem` list, with every failure reported.

Nothing in here raises on a bad source. A source that 404s, times out, serves
malformed XML or is refused on policy grounds produces zero items and a
:class:`~fpl_edge.ingest.content.sources.ProbeResult` saying why, and the run
continues. The alternative -- an exception that aborts the pipeline -- means one
dead podcast feed on a Thursday night costs the whole corpus before a deadline.

This module also owns the repair pass for what is ALREADY stored::

    uv run python -m fpl_edge.ingest.content.loaders sync-assets --dry-run
    uv run python -m fpl_edge.ingest.content.loaders sync-assets

which re-reads each feed and fixes ``content_item.url`` where a GUID was stored
in place of a link, and fills in ``enclosure_url`` for the audio. It is separate
from ``pipeline.py`` on purpose: it is a one-off backfill that takes the single
DuckDB write lock, and it is the owner's to run, not the pipeline's to trigger.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass, replace

import pandas as pd

from fpl_edge.ingest.content.feeds import parse_feed, strip_html
from fpl_edge.ingest.content.fetch import ContentFetcher
from fpl_edge.ingest.content.models import ContentItem
from fpl_edge.ingest.content.sources import (
    AccessPolicy,
    ProbeResult,
    Scope,
    Source,
    SourceKind,
)
from fpl_edge.ingest.content.youtube import videos_from_channel_page

UTC = dt.UTC

_ARTICLE_RE = re.compile(
    r"(?is)<(?:article|main)\b[^>]*>(.*?)</(?:article|main)>"
)


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def load_feed_source(
    fetcher: ContentFetcher,
    source: Source,
    *,
    max_items: int | None = None,
    since: dt.datetime | None = None,
) -> tuple[list[ContentItem], ProbeResult]:
    """Podcast or blog RSS. The body is the show notes / excerpt."""
    if source.policy is not AccessPolicy.OPEN:
        return [], ProbeResult(
            source.key, source.url, None, 0, 0,
            skipped_reason="policy", error=None,
        )
    resp = fetcher.get(source.url)
    if not resp.ok:
        return [], ProbeResult(
            source.key, source.url, resp.status, len(resp.body), 0,
            error="robots_disallow" if resp.robots_blocked else resp.error,
        )
    entries, bad_dates = parse_feed(resp.body)
    fetched = _now()
    items: list[ContentItem] = []
    no_url = 0
    no_identity = 0
    for entry in entries:
        if since is not None and entry.published_at < since:
            continue
        # `entry.link` is an absolute http(s) URL or "". It is NEVER the GUID:
        # `url = entry.link or entry.guid` used to be this line, and because 10
        # of the 22 registered podcast feeds emit no <link> at all, it wrote a
        # bare Megaphone UUID into `url` for 353 of 372 stored podcast items.
        # Those rows rendered as dead links. There is no fallback here on
        # purpose -- feeds.resolve_link has already tried <link>, the Atom
        # alternate, an isPermaLink GUID and the enclosure, in that order.
        url = entry.link
        if not url:
            no_url += 1
        # Identity comes from the GUID, not the link, and that is unchanged by
        # the fix above -- deliberately, because item_id is sha256(source_key|
        # identity) and every one of the 372 rows already in the warehouse was
        # keyed this way. Changing the identity rule would re-insert the entire
        # archive as duplicates instead of repairing it.
        #
        # Several feeds -- Sky Sports FPL and FPL JUiCE among them -- put a
        # single constant site URL on every item, so keying on the link
        # collapsed 84 episodes into 1 and 378 into 1. That failure was silent:
        # the feed returned 200, parsed to the right item count, and then
        # quietly lost 99% of the archive at insert time because every row
        # deduplicated onto the same id.
        identity = entry.guid or url
        if not identity:
            # No GUID and no link: there is nothing stable to key this item on,
            # and every such item in a feed would hash to the same item_id and
            # deduplicate onto one row. Dropped and counted, never merged.
            no_identity += 1
            continue
        text = entry.text
        if source.excerpt_only and entry.link:
            article = _fetch_article(fetcher, entry.link)
            if article:
                text = f"{entry.title}.\n{article}"
        items.append(
            ContentItem(
                item_id=ContentItem.make_id(source.key, identity),
                source_key=source.key,
                creator=source.creator,
                kind=str(source.kind),
                title=entry.title,
                url=url,
                published_at=entry.published_at,
                text=text,
                fetched_at=fetched,
                text_source="article" if source.excerpt_only else "description",
            )
        )
        if max_items is not None and len(items) >= max_items:
            break
    return items, ProbeResult(
        source.key, source.url, resp.status, len(resp.body), len(entries),
        # Carried, not discarded. A feed whose dates stop parsing returns 200
        # and zero entries, which is indistinguishable from an empty feed on
        # every other field of this receipt.
        bad_dates=bad_dates,
        no_url=no_url,
        no_identity=no_identity,
    )


def _fetch_article(fetcher: ContentFetcher, url: str) -> str:
    resp = fetcher.get(url)
    if not resp.ok:
        return ""
    match = _ARTICLE_RE.search(resp.text)
    body = match.group(1) if match else resp.text
    text = strip_html(body)
    return text[:20000]


def load_youtube_source(
    fetcher: ContentFetcher,
    source: Source,
    *,
    max_videos: int = 8,
    since: dt.datetime | None = None,
) -> tuple[list[ContentItem], ProbeResult]:
    """A channel's recent uploads: title plus description, from permitted pages.

    Not the Atom feed and not transcripts. Both are Disallowed in
    ``youtube.com/robots.txt`` -- see :mod:`fpl_edge.ingest.content.youtube` for
    the measurement showing the transcript route works and the reason it is
    unused anyway.
    """
    if source.policy is not AccessPolicy.OPEN:
        return [], ProbeResult(source.key, source.url, None, 0, 0, skipped_reason="policy")

    handle = source.handle or source.key.removeprefix("yt_")
    videos, resp = videos_from_channel_page(fetcher, handle, limit=max_videos)
    if not videos:
        return [], ProbeResult(
            source.key, source.url, resp.status, len(resp.body), 0,
            error="robots_disallow" if resp.robots_blocked else (resp.error or "no_videos"),
        )

    if since is not None:
        videos = [v for v in videos if v.published_at >= since]

    fetched = _now()
    items = [
        ContentItem(
            item_id=ContentItem.make_id(source.key, video.url),
            source_key=source.key,
            creator=source.creator,
            kind=str(source.kind),
            title=video.title,
            url=video.url,
            published_at=video.published_at,
            text=video.text,
            fetched_at=fetched,
            text_source="description",
        )
        for video in videos
    ]
    return items, ProbeResult(
        source.key, source.url, resp.status, len(resp.body), len(items)
    )


def load_source(
    fetcher: ContentFetcher,
    source: Source,
    *,
    max_items: int | None = None,
    max_videos: int = 8,
    since: dt.datetime | None = None,
) -> tuple[list[ContentItem], ProbeResult]:
    """Dispatch on source kind.

    The parameters are spelled out rather than taken as ``**kwargs`` on
    purpose. The previous signature accepted anything and forwarded only the
    names it recognised, so ``transcripts=False`` -- passed by both callers in
    pipeline.py and advertised as ``--no-transcripts`` in ``--help`` -- was
    accepted, silently dropped, and did nothing for as long as it existed. A
    keyword this function cannot honour is now a TypeError at the call site.

    That fix was half-done, and an adversarial audit caught it: spelling the
    parameters out stopped an *unknown* keyword being swallowed, but a known
    one was still being dropped. ``cmd_ingest`` passes both caps to every
    source, and ``max_items`` reached only the feed branch -- so a YouTube
    source ignored the item cap entirely and said nothing.

    The two caps are not synonyms, which is why both survive: ``max_videos``
    is a FETCH budget (how many video pages this source may request), while
    ``max_items`` is a RESULT cap (how many items any source may return).
    YouTube is subject to both.
    """
    if source.kind is SourceKind.YOUTUBE:
        items, probe = load_youtube_source(
            fetcher, source, max_videos=max_videos, since=since,
        )
        if max_items is not None and len(items) > max_items:
            items = items[:max_items]
            probe = replace(probe, items=len(items))
        return items, probe
    if source.kind in (SourceKind.PODCAST, SourceKind.BLOG):
        return load_feed_source(
            fetcher, source, max_items=max_items, since=since,
        )
    return [], ProbeResult(
        source.key, source.url, None, 0, 0,
        skipped_reason="policy" if source.policy is not AccessPolicy.OPEN else "unsupported",
    )


# ---------------------------------------------------------------------------
# Repairing what is already stored
# ---------------------------------------------------------------------------
#
# The parser fix above only helps items ingested from now on. 353 of the 372
# podcast rows already in the warehouse hold a GUID in their `url` column and
# none of them hold an enclosure, because the column did not exist. Both are
# repaired by the same pass, from the same feeds, keyed on the same item_id --
# so it is one function and one command rather than two that can disagree.
#
# It is idempotent by construction: it only ever writes a value it just derived
# from the feed, and the second run derives the same value. Running it twice
# changes nothing the first run did not.

#: A stored url that is already a real link. Repair never touches these: a blog
#: permalink that works must not be replaced by whatever the feed happens to
#: serve today, and re-running this pass must not rewrite rows it has fixed.
_STORED_URL_IS_HTTP = (
    "(t.url IS NOT NULL AND (starts_with(lower(t.url), 'http://') "
    "OR starts_with(lower(t.url), 'https://')))"
)

#: Set on a row whose url is not a link and whose item is no longer in the
#: window the feed serves, so nothing can be derived for it. Recorded rather
#: than left holding a GUID -- the GUID is the bug, and "we looked and the feed
#: no longer carries this episode" is a fact worth keeping.
UNSEEN_REASON = "item_not_in_current_feed_window"


@dataclass(frozen=True, slots=True)
class AssetSync:
    """What one source's repair pass actually did. Every field is a count."""

    source_key: str
    #: Stored rows for this source, whatever their state.
    stored: int = 0
    #: Stored rows the current feed still carries.
    matched: int = 0
    #: Rows whose url was a GUID (or otherwise not http) and now is a link.
    url_repaired: int = 0
    #: Rows whose url was not a link and for which the feed offers none, so the
    #: url is now NULL with a reason.
    url_nulled: int = 0
    #: Rows that gained (or kept) an enclosure_url.
    enclosures_written: int = 0
    #: Matched rows whose url was already a working link and was left alone.
    url_already_ok: int = 0
    error: str | None = None

    def render(self) -> str:
        if self.error:
            return f"{self.source_key:>22}  SKIPPED  {self.error}"
        return (
            f"{self.source_key:>22}  stored {self.stored:>5}  matched {self.matched:>5}  "
            f"url_fixed {self.url_repaired:>5}  url_null {self.url_nulled:>4}  "
            f"enclosure {self.enclosures_written:>5}  ok_already {self.url_already_ok:>5}"
        )


def feed_assets(fetcher: ContentFetcher, source: Source) -> tuple[pd.DataFrame, str | None]:
    """The url/enclosure this feed currently offers for each item, by item_id.

    ``item_id`` is recomputed with exactly the rule the loader used when the row
    was written -- ``sha256(source_key|guid)`` -- which is why the identity rule
    in :func:`load_feed_source` had to stay untouched while the url rule changed.
    Had identity moved, this pass would match nothing and the repair would look
    like a feed that had lost its entire archive.
    """
    if source.policy is not AccessPolicy.OPEN:
        return pd.DataFrame(), "policy"
    if source.kind not in (SourceKind.PODCAST, SourceKind.BLOG):
        return pd.DataFrame(), "not_a_feed"
    resp = fetcher.get(source.url)
    if not resp.ok:
        return pd.DataFrame(), (
            "robots_disallow" if resp.robots_blocked else (resp.error or f"http_{resp.status}")
        )
    entries, _ = parse_feed(resp.body)
    rows = []
    for entry in entries:
        identity = entry.guid or entry.link
        if not identity:
            continue
        enclosure = entry.enclosure
        rows.append(
            {
                "item_id": ContentItem.make_id(source.key, identity),
                "new_url": entry.link or None,
                "new_url_basis": entry.link_basis,
                "new_url_reason": entry.link_reason,
                "new_enclosure_url": enclosure.url if enclosure else None,
                "new_enclosure_length_bytes": (
                    enclosure.length if enclosure else None
                ),
                "new_enclosure_type": enclosure.mime_type if enclosure else None,
            }
        )
    if not rows:
        return pd.DataFrame(), "no_entries"
    frame = pd.DataFrame(rows).drop_duplicates(subset=["item_id"])
    return frame, None


def sync_feed_assets(
    warehouse,
    fetcher: ContentFetcher,
    sources: tuple[Source, ...],
    *,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> list[AssetSync]:
    """Repair stored urls and fill in enclosures, one feed at a time.

    Three rules, all of them about not making things worse:

    * A stored url that is already an http(s) link is NEVER overwritten. The
      110 blog rows and the 107 YouTube rows are correct and this pass must be
      a no-op for them.
    * A url is set to NULL only when the feed was actually reached and actually
      offered nothing. A 500 or a robots block skips the source entirely; it
      does not get to blank a column.
    * The reason is written in the same pass as the NULL. There is no state in
      which a row has an empty url and no explanation for it.

    The plan is materialised BEFORE anything is written, because every decision
    here depends on what the url was when the pass started. Reading
    ``content_item.url`` again after the UPDATE would see the value this pass
    had just written and conclude the row had always been fine.
    """
    stamp = now or _now()
    out: list[AssetSync] = []
    for source in sources:
        frame, error = feed_assets(fetcher, source)
        stored = int(warehouse.sql(
            "SELECT count(*) c FROM content_item WHERE source_key = ?", [source.key]
        ).iloc[0]["c"])
        if error is not None:
            out.append(AssetSync(source.key, stored=stored, error=error))
            continue
        frame = frame.copy()
        frame["checked_utc"] = stamp
        warehouse._con.register("_incoming_assets", frame)
        try:
            warehouse.sql(
                f"""
                CREATE OR REPLACE TEMP TABLE _asset_plan AS
                SELECT i.item_id,
                       {_STORED_URL_IS_HTTP} AS keep_url,
                       i.new_url, i.new_url_basis, i.new_url_reason,
                       i.new_enclosure_url, i.new_enclosure_length_bytes,
                       i.new_enclosure_type, i.checked_utc
                FROM _incoming_assets i JOIN content_item t USING (item_id)
                WHERE t.source_key = ?
                """,
                [source.key],
            )
            counts = warehouse.sql(
                """
                SELECT count(*) AS matched,
                       count(*) FILTER (WHERE NOT keep_url AND new_url IS NOT NULL)
                           AS url_repaired,
                       count(*) FILTER (WHERE NOT keep_url AND new_url IS NULL)
                           AS url_nulled,
                       count(*) FILTER (WHERE keep_url) AS url_already_ok,
                       count(*) FILTER (WHERE new_enclosure_url IS NOT NULL)
                           AS enclosures_written
                FROM _asset_plan
                """
            ).iloc[0]
            unseen = int(warehouse.sql(
                f"""
                SELECT count(*) c FROM content_item t
                WHERE t.source_key = ? AND NOT {_STORED_URL_IS_HTTP}
                  AND t.item_id NOT IN (SELECT item_id FROM _asset_plan)
                """,
                [source.key],
            ).iloc[0]["c"])
            if not dry_run:
                warehouse.sql("BEGIN TRANSACTION")
                try:
                    # Rows the feed no longer carries, FIRST: this is the only
                    # statement that needs to know which stored urls were
                    # broken, and the UPDATE below is about to change some of
                    # them. A GUID in a url column is the bug being fixed, so
                    # it does not get to survive on the grounds that we could
                    # not find a replacement for it.
                    warehouse.sql(
                        """
                        CREATE OR REPLACE TEMP TABLE _asset_unseen AS
                        SELECT t.item_id FROM content_item t
                        WHERE t.source_key = ?
                          AND t.url IS NOT NULL
                          AND NOT starts_with(lower(t.url), 'http://')
                          AND NOT starts_with(lower(t.url), 'https://')
                          AND t.item_id NOT IN (SELECT item_id FROM _asset_plan)
                        """,
                        [source.key],
                    )
                    warehouse.sql(
                        "UPDATE content_item SET url = NULL WHERE item_id IN "
                        "(SELECT item_id FROM _asset_unseen)"
                    )
                    # content_item carries the url and nothing else new: it is
                    # written positionally elsewhere in this project, so it may
                    # not be widened. See
                    # content_003_item_url_and_enclosure.sql.
                    warehouse.sql(
                        """
                        UPDATE content_item t SET url = p.new_url
                        FROM _asset_plan p
                        WHERE t.item_id = p.item_id AND NOT p.keep_url
                        """
                    )
                    # Basis, reason and audio, keyed 1:1 on item_id.
                    # Delete-then-insert scoped to exactly the items this pass
                    # derived: the feed is authoritative about its own items
                    # and about no others, so a narrowed run cannot erase a row
                    # it never looked at.
                    warehouse.sql(
                        "DELETE FROM content_item_asset WHERE item_id IN "
                        "(SELECT item_id FROM _asset_plan UNION ALL "
                        " SELECT item_id FROM _asset_unseen)"
                    )
                    warehouse.sql(
                        """
                        INSERT INTO content_item_asset (
                          item_id, url_basis, url_reason, enclosure_url,
                          enclosure_length_bytes, enclosure_type, checked_utc)
                        SELECT item_id,
                               -- NULL for a row whose url this pass left
                               -- alone: we did not derive that url and will
                               -- not claim to know where it came from.
                               CASE WHEN keep_url THEN NULL ELSE new_url_basis END,
                               CASE WHEN keep_url THEN NULL ELSE new_url_reason END,
                               new_enclosure_url, new_enclosure_length_bytes,
                               new_enclosure_type, checked_utc
                        FROM _asset_plan
                        """
                    )
                    warehouse.sql(
                        "INSERT INTO content_item_asset "
                        "(item_id, url_reason, checked_utc) "
                        "SELECT item_id, ?, ? FROM _asset_unseen",
                        [UNSEEN_REASON, stamp],
                    )
                    warehouse.sql("COMMIT")
                except Exception:
                    # A half-applied repair leaves urls nulled with no reason
                    # recorded anywhere, which is strictly worse than the GUID.
                    warehouse.sql("ROLLBACK")
                    raise
        finally:
            for scratch in ("_asset_plan", "_asset_unseen"):
                try:
                    warehouse.sql(f"DROP TABLE IF EXISTS {scratch}")
                except Exception:  # noqa: BLE001, S110
                    # Scratch space. Failing to tidy it must not replace the
                    # real exception with a misleading one.
                    pass
            warehouse._con.unregister("_incoming_assets")
        out.append(
            AssetSync(
                source_key=source.key,
                stored=stored,
                matched=int(counts["matched"]),
                url_repaired=int(counts["url_repaired"]),
                url_nulled=int(counts["url_nulled"]) + unseen,
                enclosures_written=int(counts["enclosures_written"]),
                url_already_ok=int(counts["url_already_ok"]),
            )
        )
    return out


def _cmd_sync_assets(args: argparse.Namespace) -> int:
    from fpl_edge.ingest.content.store import ContentStore
    from fpl_edge.store import Warehouse

    scope = (
        Scope.from_keys(args.only.split(","), label="--only")
        if args.only else Scope.everything("all fetchable feed sources")
    )
    sources = tuple(
        s for s in scope.apply()
        if s.kind in (SourceKind.PODCAST, SourceKind.BLOG)
    )
    print(scope.render())
    if scope.unknown_keys:
        print(f"unknown source keys: {', '.join(scope.unknown_keys)}", file=sys.stderr)
        return 2
    with Warehouse(args.db) as warehouse:
        applied = ContentStore(warehouse).migrate()
        if applied:
            print(f"migrations applied: {', '.join(applied)}")
        with ContentFetcher("sync-assets", delay_s=args.delay) as fetcher:
            results = sync_feed_assets(
                warehouse, fetcher, sources, dry_run=args.dry_run
            )
        for result in results:
            print(result.render(), flush=True)
        print()
        print(
            f"-- {'WOULD REPAIR' if args.dry_run else 'repaired'} "
            f"{sum(r.url_repaired for r in results)} urls, "
            f"nulled {sum(r.url_nulled for r in results)} with a reason, "
            f"wrote {sum(r.enclosures_written for r in results)} enclosures, "
            f"left {sum(r.url_already_ok for r in results)} working urls alone"
        )
        broken = warehouse.sql(
            "SELECT count(*) c FROM content_item WHERE url IS NOT NULL "
            "AND NOT starts_with(lower(url), 'http')"
        ).iloc[0]["c"]
        print(f"-- rows still holding a non-link url: {int(broken)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Backfill entry point. Deliberately NOT wired into pipeline.py.

    Run it yourself, against a warehouse nothing else is writing to::

        uv run python -m fpl_edge.ingest.content.loaders sync-assets --dry-run
        uv run python -m fpl_edge.ingest.content.loaders sync-assets

    DuckDB is single-writer: this opens the warehouse for writing, so no
    ingest, analyse or score run may be in flight while it runs.
    """
    parser = argparse.ArgumentParser(
        prog="python -m fpl_edge.ingest.content.loaders",
        description="Repair content_item.url and backfill enclosure_url from the feeds.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sync = sub.add_parser(
        "sync-assets",
        help="re-read each feed and repair stored urls / fill enclosures",
    )
    sync.add_argument("--db", default="data/warehouse/fpl.duckdb")
    sync.add_argument("--only", default=None, help="comma-separated source keys")
    sync.add_argument("--delay", type=float, default=1.0)
    sync.add_argument(
        "--dry-run", action="store_true",
        help="count what would change and write nothing",
    )
    sync.set_defaults(func=_cmd_sync_assets)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
