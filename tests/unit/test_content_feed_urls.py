"""The url column must contain a url, and the audio must be addressable.

353 of the 372 stored podcast items held a bare RSS GUID --
``74b8ffec-a205-11f1-9f9e-87719b00dbeb`` -- in a column named ``url``. Every one
of them rendered as a dead link and every deep link built from one degraded.

The reported diagnosis was that the parser preferred ``<guid>`` over ``<link>``.
It did not, and had never: ``feeds.parse_feed`` read ``<link>`` first. The real
shape is uglier and is what the fixture below encodes -- **most podcast items
have no ``<link>`` element at all**. Measured over the 22 archived podcast feeds
in ``data/raw/content/``: 10 of 22 emit ``<link>`` on zero or one item, and 22 of
22 emit ``<enclosure>`` on every item. The loader's ``url = entry.link or
entry.guid`` then did the rest.

So the fixture is Megaphone-shaped, not textbook-shaped. It carries, in order:

* an item with BOTH a guid and a link -- the case the brief asked to be
  regression-tested, and the one that already worked;
* an item with a ``isPermaLink="false"`` UUID guid, no link, and an enclosure --
  the shape of all 353 broken rows;
* an Atom-style item whose only link is ``rel="alternate"``, alongside a
  ``rel="self"`` that must NOT be mistaken for it;
* an item whose guid IS a permalink, which RSS 2.0 explicitly allows;
* an item with a guid, no link, no enclosure and nothing else -- the only case
  in which a null url is correct, and it must arrive with a reason.

Hermetic: no network, no live warehouse. The store tests build their own DuckDB
file in tmp_path.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.ingest.content.feeds import NO_LINK_REASON, http_url, parse_feed
from fpl_edge.ingest.content.fetch import Response
from fpl_edge.ingest.content.loaders import (
    UNSEEN_REASON,
    load_feed_source,
    sync_feed_assets,
)
from fpl_edge.ingest.content.models import ContentItem
from fpl_edge.ingest.content.sources import AccessPolicy, Source, SourceKind
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse

UTC = dt.UTC
FETCHED = dt.datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

GUID_LINKED = "ep-with-both"
GUID_MEGAPHONE = "74b8ffec-a205-11f1-9f9e-87719b00dbeb"
GUID_ATOM = "ep-atom"
GUID_PERMALINK = "https://example.invalid/ep/permalink"
GUID_BARE = "ep-nothing-at-all"

MP3 = "https://traffic.megaphone.fm/COMG9332837514.mp3"

FEED = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>The Test Wire</title>
    <link>https://example.invalid/show</link>
    <item>
      <title>GW2 Captaincy</title>
      <link>https://example.invalid/ep/1</link>
      <guid isPermaLink="false"><![CDATA[{GUID_LINKED}]]></guid>
      <pubDate>Tue, 18 Aug 2026 08:20:00 -0000</pubDate>
      <enclosure url="{MP3}" length="0" type="audio/mpeg"/>
      <description><![CDATA[<p>Captaining Haaland</p>]]></description>
    </item>
    <item>
      <title>Free Hit or Wildcard? - Zophar Gameweek 2 Team</title>
      <guid isPermaLink="false"><![CDATA[{GUID_MEGAPHONE}]]></guid>
      <pubDate>Thu, 27 Aug 2026 10:52:00 -0000</pubDate>
      <enclosure url="{MP3}" length="48293102" type="audio/mpeg"/>
      <description><![CDATA[<p>Zophar discusses his transfer plans</p>]]></description>
    </item>
    <item>
      <title>Atom shaped</title>
      <atom:link rel="self" href="https://example.invalid/feed.xml"/>
      <atom:link rel="alternate" href="https://example.invalid/ep/atom"/>
      <guid isPermaLink="false">{GUID_ATOM}</guid>
      <pubDate>Wed, 26 Aug 2026 07:00:00 +0000</pubDate>
      <description><![CDATA[<p>Selling Salah</p>]]></description>
    </item>
    <item>
      <title>Guid is the permalink</title>
      <guid>{GUID_PERMALINK}</guid>
      <pubDate>Mon, 24 Aug 2026 09:00:00 +0000</pubDate>
      <description><![CDATA[<p>Holding Watkins</p>]]></description>
    </item>
    <item>
      <title>Nothing resolvable at all</title>
      <guid isPermaLink="false">{GUID_BARE}</guid>
      <pubDate>Sun, 23 Aug 2026 09:00:00 +0000</pubDate>
      <description><![CDATA[<p>Benching Mbeumo</p>]]></description>
    </item>
  </channel>
</rss>
""".encode()

def _without_item(feed: bytes, guid: str) -> bytes:
    """Drop one whole <item> block, the way a feed that ages out episodes does."""
    head, _, rest = feed.partition(b"<item>")
    blocks = rest.split(b"<item>")
    kept = [b for b in blocks if guid.encode() not in b]
    return head + b"<item>" + b"<item>".join(kept)


#: The same feed after the show pulled its back catalogue: the Megaphone item
#: the warehouse already holds is no longer served at all.
FEED_SHRUNK = _without_item(FEED, GUID_MEGAPHONE)

SOURCE = Source(
    "pod_test", "The Test Wire", SourceKind.PODCAST,
    "https://example.invalid/feed", policy=AccessPolicy.OPEN,
)

class _CannedFetcher:
    """A ContentFetcher stand-in that answers one body per URL."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def get(self, url: str) -> Response:
        return Response(
            url=url, status=200, body=self._body,
            fetched_at=FETCHED, sha256="", body_path=None,
        )


class _DeadFetcher:
    def get(self, url: str) -> Response:
        return Response(
            url=url, status=500, body=b"", fetched_at=FETCHED,
            sha256="", body_path=None, error="server_error",
        )


def _by_title(entries):
    return {e.title: e for e in entries}


class TestLinkResolution:
    def test_link_wins_over_guid_when_the_feed_offers_both(self) -> None:
        """The case the brief named. It already worked; now it is guarded."""
        entries, _ = parse_feed(FEED)
        entry = _by_title(entries)["GW2 Captaincy"]
        assert entry.link == "https://example.invalid/ep/1"
        assert entry.link_basis == "link"
        assert entry.guid == GUID_LINKED
        assert entry.link != entry.guid

    def test_a_guid_only_item_resolves_to_its_enclosure_not_to_the_guid(self) -> None:
        """The 353. This item is byte-shaped like the live Megaphone feed."""
        entry = _by_title(parse_feed(FEED)[0])[
            "Free Hit or Wildcard? - Zophar Gameweek 2 Team"
        ]
        assert entry.link == MP3
        assert entry.link_basis == "enclosure"
        assert entry.guid == GUID_MEGAPHONE
        assert GUID_MEGAPHONE not in entry.link

    def test_atom_alternate_is_used_and_atom_self_is_not(self) -> None:
        """rel="self" is the FEED's url, identical on every item of it.

        Taking the first <atom:link> regardless of rel is the same
        collapse-every-item-onto-one-url failure the GUID identity rule exists
        to survive, arriving through a different door.
        """
        entry = _by_title(parse_feed(FEED)[0])["Atom shaped"]
        assert entry.link == "https://example.invalid/ep/atom"
        assert entry.link_basis == "atom_alternate"

    def test_a_permalink_guid_is_a_link_because_rss_says_it_is(self) -> None:
        entry = _by_title(parse_feed(FEED)[0])["Guid is the permalink"]
        assert entry.link == GUID_PERMALINK
        assert entry.link_basis == "guid_permalink"

    def test_nothing_resolvable_yields_an_empty_link_and_a_reason(self) -> None:
        """Never the GUID. An unlinkable item is unlinkable, and says so."""
        entry = _by_title(parse_feed(FEED)[0])["Nothing resolvable at all"]
        assert entry.link == ""
        assert entry.link_basis is None
        assert entry.link_reason == NO_LINK_REASON

    def test_no_entry_anywhere_stores_a_guid_as_a_link(self) -> None:
        for entry in parse_feed(FEED)[0]:
            assert entry.link == "" or entry.link.startswith("https://")
            if entry.link and entry.link_basis != "guid_permalink":
                assert entry.link != entry.guid

    @pytest.mark.parametrize(
        "value",
        ["74b8ffec-a205-11f1-9f9e-87719b00dbeb", "spotify:episode:xyz",
         "//example.invalid/ep", "/ep/1", "", None, "https://"],
    )
    def test_http_url_refuses_everything_that_is_not_an_absolute_link(self, value):
        assert http_url(value) is None


class TestEnclosureCapture:
    """Bug 2: it was not captured at all. FeedEntry had no field for it."""

    def test_enclosure_url_length_and_type_are_all_captured(self) -> None:
        entry = _by_title(parse_feed(FEED)[0])[
            "Free Hit or Wildcard? - Zophar Gameweek 2 Team"
        ]
        assert entry.enclosure is not None
        assert entry.enclosure.url == MP3
        assert entry.enclosure.length == 48293102
        assert entry.enclosure.mime_type == "audio/mpeg"

    def test_a_zero_length_enclosure_is_null_not_zero(self) -> None:
        """Megaphone stamps length="0" on every episode.

        A zero-byte audio file is not a fact about the audio; storing the 0
        would let a caller compute a bitrate from a placeholder.
        """
        entry = _by_title(parse_feed(FEED)[0])["GW2 Captaincy"]
        assert entry.enclosure is not None
        assert entry.enclosure.length is None


class TestLoader:
    def test_the_stored_url_is_never_the_guid(self) -> None:
        items, probe = load_feed_source(_CannedFetcher(FEED), SOURCE)
        assert len(items) == 5
        for item in items:
            assert item.url == "" or item.url.startswith("https://")
        assert not any(item.url == GUID_MEGAPHONE for item in items)
        assert probe.no_url == 1, "the unlinkable item must reach the receipt"
        assert probe.no_identity == 0

    def test_item_id_still_comes_from_the_guid(self) -> None:
        """The identity rule MUST NOT move while the url rule is fixed.

        item_id is sha256(source_key|guid) and all 372 stored rows were keyed
        that way. Had the fix changed identity, the repair pass would match
        nothing and the whole archive would re-insert as duplicates.
        """
        items, _ = load_feed_source(_CannedFetcher(FEED), SOURCE)
        expected = ContentItem.make_id(SOURCE.key, GUID_MEGAPHONE)
        assert any(item.item_id == expected for item in items)


@pytest.fixture
def warehouse(tmp_path):
    with Warehouse(tmp_path / "content.duckdb") as wh:
        ContentStore(wh)  # applies the content migrations, 003 included
        yield wh


def _seed(wh, *, item_id: str, url: str | None, source_key: str = "pod_test") -> None:
    wh.sql(
        "INSERT INTO content_item (item_id, source_key, creator, kind, title, url, "
        "published_at, fetched_at, text_source, text, text_sha256) "
        "VALUES (?, ?, 'The Test Wire', 'podcast', 'seeded', ?, ?, ?, "
        "'description', 'body', 'sha')",
        [item_id, source_key, url, FETCHED, FETCHED],
    )


class TestBackfill:
    """The 353 rows that are already in the warehouse.

    Run by hand, never by the pipeline::

        uv run python -m fpl_edge.ingest.content.loaders sync-assets --dry-run
    """

    def test_a_stored_guid_url_is_repaired_and_the_enclosure_filled_in(self, warehouse):
        item_id = ContentItem.make_id(SOURCE.key, GUID_MEGAPHONE)
        _seed(warehouse, item_id=item_id, url=GUID_MEGAPHONE)

        [result] = sync_feed_assets(warehouse, _CannedFetcher(FEED), (SOURCE,))

        assert result.url_repaired == 1
        row = warehouse.sql(
            "SELECT i.url, a.url_basis, a.url_reason, a.enclosure_url, "
            "a.enclosure_length_bytes, a.enclosure_type "
            "FROM content_item i JOIN content_item_asset a USING (item_id) "
            "WHERE i.item_id = ?",
            [item_id],
        ).iloc[0]
        assert row["url"] == MP3
        assert row["url_basis"] == "enclosure"
        assert row["url_reason"] is None
        assert row["enclosure_url"] == MP3
        assert int(row["enclosure_length_bytes"]) == 48293102
        assert row["enclosure_type"] == "audio/mpeg"

    def test_a_working_url_is_never_overwritten(self, warehouse):
        """110 blog rows and 107 YouTube rows are correct. Leave them alone."""
        item_id = ContentItem.make_id(SOURCE.key, GUID_LINKED)
        _seed(warehouse, item_id=item_id, url="https://example.invalid/ep/1-canonical")

        [result] = sync_feed_assets(warehouse, _CannedFetcher(FEED), (SOURCE,))

        assert result.url_already_ok == 1
        assert result.url_repaired == 0
        url = warehouse.sql(
            "SELECT url FROM content_item WHERE item_id = ?", [item_id]
        ).iloc[0]["url"]
        assert url == "https://example.invalid/ep/1-canonical"

    def test_an_unresolvable_item_gets_null_and_a_reason(self, warehouse):
        item_id = ContentItem.make_id(SOURCE.key, GUID_BARE)
        _seed(warehouse, item_id=item_id, url=GUID_BARE)

        sync_feed_assets(warehouse, _CannedFetcher(FEED), (SOURCE,))

        row = warehouse.sql(
            "SELECT i.url, a.url_reason FROM content_item i "
            "LEFT JOIN content_item_asset a USING (item_id) WHERE i.item_id = ?",
            [item_id],
        ).iloc[0]
        assert row["url"] is None
        assert row["url_reason"] == NO_LINK_REASON

    def test_an_item_the_feed_no_longer_carries_gets_null_and_a_reason(self, warehouse):
        """"We looked and it is gone" is a fact. A GUID standing in is not."""
        item_id = ContentItem.make_id(SOURCE.key, GUID_MEGAPHONE)
        _seed(warehouse, item_id=item_id, url=GUID_MEGAPHONE)

        sync_feed_assets(warehouse, _CannedFetcher(FEED_SHRUNK), (SOURCE,))

        row = warehouse.sql(
            "SELECT i.url, a.url_reason FROM content_item i "
            "LEFT JOIN content_item_asset a USING (item_id) WHERE i.item_id = ?",
            [item_id],
        ).iloc[0]
        assert row["url"] is None
        assert row["url_reason"] == UNSEEN_REASON

    def test_a_failed_fetch_blanks_nothing(self, warehouse):
        """A 500 must not be allowed to erase a column. Skip, and say why."""
        item_id = ContentItem.make_id(SOURCE.key, GUID_MEGAPHONE)
        _seed(warehouse, item_id=item_id, url=GUID_MEGAPHONE)

        [result] = sync_feed_assets(warehouse, _DeadFetcher(), (SOURCE,))

        assert result.error == "server_error"
        url = warehouse.sql(
            "SELECT url FROM content_item WHERE item_id = ?", [item_id]
        ).iloc[0]["url"]
        assert url == GUID_MEGAPHONE, "a dead feed rewrote a row it never read"

    def test_the_repair_is_idempotent(self, warehouse):
        item_id = ContentItem.make_id(SOURCE.key, GUID_MEGAPHONE)
        _seed(warehouse, item_id=item_id, url=GUID_MEGAPHONE)

        first = sync_feed_assets(warehouse, _CannedFetcher(FEED), (SOURCE,))[0]
        second = sync_feed_assets(warehouse, _CannedFetcher(FEED), (SOURCE,))[0]

        assert first.url_repaired == 1
        assert second.url_repaired == 0, "the second run rewrote a row it had fixed"
        assert second.url_already_ok == 1

    def test_dry_run_writes_nothing_and_still_counts(self, warehouse):
        item_id = ContentItem.make_id(SOURCE.key, GUID_MEGAPHONE)
        _seed(warehouse, item_id=item_id, url=GUID_MEGAPHONE)

        result = sync_feed_assets(
            warehouse, _CannedFetcher(FEED), (SOURCE,), dry_run=True
        )[0]

        assert result.url_repaired == 1
        url = warehouse.sql(
            "SELECT url FROM content_item WHERE item_id = ?", [item_id]
        ).iloc[0]["url"]
        assert url == GUID_MEGAPHONE
