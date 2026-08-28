-- content_item.url must be a link, and the audio must be addressable.
--
-- 353 of the 372 stored podcast items had a bare RSS GUID --
-- `74b8ffec-a205-11f1-9f9e-87719b00dbeb` -- sitting in a column named `url`.
-- Blogs (110) and YouTube (107) were unaffected.
--
-- WHY THE URL COLUMN BECOMES NULLABLE
--
-- The old shape (`url VARCHAR NOT NULL`) is what forced the bug. The loader had
-- to put SOMETHING in the column, the feed did not always offer a link, so it
-- fell back to `entry.guid` and produced a string that satisfies the constraint
-- and satisfies nothing else. NOT NULL on a column that cannot always be known
-- does not make the data complete; it makes the data lie, and it moves the lie
-- somewhere no constraint can catch it.
--
-- Measured across the 22 archived podcast feeds in data/raw/content/: 10 of 22
-- emit no <link> element on any item, and 22 of 22 emit <enclosure> on every
-- item. With <enclosure> added to the resolution order (see feeds.resolve_link)
-- all 12,163 archived entries resolve to a real http(s) URL, so in practice
-- `url IS NULL` should be rare. It is still permitted, and it is permitted WITH
-- A REASON, because the alternative is the thing this migration exists to undo.
--
-- The invariant the code upholds: `url` is an absolute http(s) URL, or it is
-- NULL and content_item_asset.url_reason says why. There is no third state. In
-- particular there is no state in which a GUID, a relative path or a `spotify:`
-- URI is stored as though it were a link.
--
-- WHY THE NEW COLUMNS ARE A SIDE TABLE AND NOT COLUMNS ON content_item
--
-- Because content_item is written positionally by code this package does not
-- own. `INSERT INTO content_item VALUES (?, ?, ... )` with no column list
-- appears in at least one other team's test fixture, and
-- `store.py::_ITEM_COLS` is a fixed tuple in a file owned by another agent.
-- Widening content_item breaks every one of those writers with a column-count
-- error, in a package four teams share, at a deadline.
--
-- `ALTER COLUMN ... DROP NOT NULL` changes no column count, so the one change
-- that MUST be on content_item -- the url column itself, which is what the UI
-- renders -- is made there, and everything additive goes next door keyed 1:1 on
-- item_id. A reader wanting the audio joins:
--
--     SELECT i.item_id, i.title, a.enclosure_url, a.enclosure_type
--     FROM content_item i JOIN content_item_asset a USING (item_id)
--     WHERE i.kind = 'podcast' AND a.enclosure_url IS NOT NULL
--
-- POINT-IN-TIME
--
-- Nothing here is PIT-keyed and none of it belongs in store.PIT_KEYS, for the
-- reason content_001_claims.sql gives: these are properties of an immutable
-- published artifact, not (entity, as_of) facts about the world. `published_at`
-- remains the only column a reader filters on, and none of these columns are
-- readable in a way that bypasses ContentStore.claims_visible_at -- they hang
-- off content_item, which is reached through items_visible_at or through a join
-- from an already-filtered claim.
--
-- `checked_utc` is the exception that proves it: it is a FETCH clock, like
-- content_item.fetched_at, recording when loaders.sync_feed_assets last read
-- the feed for this item. It must never be mistaken for a publication instant
-- and nothing filters on it.

ALTER TABLE content_item ALTER COLUMN url DROP NOT NULL;

CREATE TABLE IF NOT EXISTS content_item_asset (
    item_id     VARCHAR PRIMARY KEY,
    -- Which element content_item.url came from: link | atom_alternate |
    -- guid_permalink | enclosure. Kept because the four are not equally good --
    -- an `enclosure` basis means the "link" is an mp3, which a UI may want to
    -- render as a play button rather than a page link -- and because it makes
    -- the repair auditable after the fact. NULL when the url is NULL.
    url_basis   VARCHAR,
    -- Why content_item.url is NULL. Set together with the NULL, never
    -- independently. Written by loaders.sync_feed_assets:
    --   feed_item_has_no_link_alternate_permalink_or_enclosure
    --       the feed offered no <link>, no atom rel=alternate, no isPermaLink
    --       GUID and no <enclosure>.
    --   item_not_in_current_feed_window
    --       the stored row predates the window the feed now serves, so the
    --       repair pass could not see the item and will not invent a link.
    url_reason  VARCHAR,
    -- THE column that turns show notes into content: everything downstream of
    -- this package has been reading a sponsor read with a sentence of football
    -- in it, because the episode itself was never addressable. Local ASR needs
    -- this to exist before it can turn one into the other.
    enclosure_url          VARCHAR,
    -- The feed's own `length` attribute, kept only when positive. Megaphone
    -- stamps length="0" on every episode; a zero-byte audio file is not a fact
    -- about the audio, it is a placeholder, and storing the 0 would let a
    -- caller compute a bitrate from it.
    enclosure_length_bytes BIGINT,
    enclosure_type         VARCHAR,   -- e.g. audio/mpeg, as stated by the feed
    -- When the feed was last consulted for this item. A FETCH clock, not a
    -- publication instant. Nothing filters on it.
    checked_utc TIMESTAMPTZ NOT NULL
);
