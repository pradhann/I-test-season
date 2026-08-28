-- People, not shows: the panel, who is on which show, and which item is whose.
--
-- The corpus is keyed by `content_item.creator`, and creator is a SHOW name.
-- "The FPL Wire" is not a person; it is Lateriser, Zophar and Pras, and the
-- latest episode in the warehouse is titled "Free Hit or Wildcard? - Zophar
-- Gameweek 2 Team". That is one host's team, filed under a three-host label.
-- Every consensus, every track record and every "tie the suggestion to an FPL
-- team" is wrong at the show grain, because the show does not have an FPL team
-- and the show never made a pick.
--
-- Three tables, and the important design decision is in the third.
--
-- 1. panel_person   -- the person, and their FPL entry if it is KNOWN.
-- 2. panel_person_show -- N people on N shows, in both directions.
-- 3. item_person    -- which person an item is attributable to, AND HOW.
--
-- ON NOT FABRICATING AN ENTRY ID
--
-- `entry_id` is nullable and every null carries `entry_reason`. An FPL entry id
-- is a claim about a real account belonging to a real person, it is the join
-- key to that person's actual team, and a wrong one silently attributes a
-- stranger's squad to a creator for a whole season. So: `entry_verified` says
-- whether the id was confirmed against something, `entry_source_url` is where
-- it was confirmed (the creator stating it themselves, a pinned tweet, a video
-- frame), `entry_api_name` is the name the FPL API returns for that entry --
-- kept because it is the one field that can later CONTRADICT the mapping --
-- and `entry_checked_utc` is when that check happened. An id with
-- entry_verified = false is a lead, not a fact, and readers must treat it as
-- one.
--
-- ON ATTRIBUTION, WHICH IS THE PART THAT CAN GO WRONG QUIETLY
--
-- item_person exists so that "Zophar's GW2 team" is Zophar's and not The FPL
-- Wire's. It records `basis`: HOW the attribution was made, from a closed set,
-- never a guess:
--
--   sole_host  The show has exactly one active person on the panel. Structural
--              and deterministic; there is no one else it could be.
--   title      The episode title names them, matched whole-word against an
--              alias the panel file states explicitly, and ONLY against
--              aliases of people who are on that show. "Harry" in an FPL Focal
--              title does not become FPL Harry.
--   stated     The transcript or a structured analysis of it says whose picks
--              these are. Written by the transcript/ASR side, not by this
--              module, and `evidence` carries the verbatim span.
--   manual     A human wrote it down.
--
-- `evidence` is the verbatim text that justified the attribution, so a wrong
-- one can be found and deleted rather than argued about.
--
-- THE ABSENT ROW IS A LEGITIMATE STATE. An item with no item_person row is
-- attributed to the show and to nobody in particular, which for a three-host
-- round-table episode is the TRUE answer. There is no "unknown person"
-- placeholder row and there is no default-to-the-first-host rule, because both
-- would turn "we do not know" into a name, and a name is what downstream code
-- would then trust. Readers must LEFT JOIN and cope with NULL.
--
-- ON POINT-IN-TIME, AND WHY THESE ARE NOT IN store.PIT_KEYS
--
-- Same reasoning as content_001_claims.sql. PIT_KEYS is for facts about the
-- world read through Snapshot, keyed by (entity, as_of). None of these are that:
--
-- * panel_person and panel_person_show are a ROSTER -- editorial identity
--   metadata, re-upserted whole from a curated YAML file. `as_of` here is the
--   file's own stamp, recording when the roster was last curated. It is not a
--   PIT axis and must not be read as one; there is exactly one current row per
--   person and per (person, show).
-- * item_person is a statement about an immutable item. The attribution is not
--   a claim, carries no publication instant of its own, and must never be used
--   as one.
--
-- The point-in-time discipline is unchanged and is enforced where it always
-- was: `content_item.published_at` and `content_claim.published_at`, read
-- through ContentStore.claims_visible_at. Person-level reads join item_person
-- onto the ALREADY-FILTERED result of that call (see
-- panel.person_claims_visible_at); they do not open a second read path, because
-- a second read path is how the first one stops being checked.

CREATE TABLE IF NOT EXISTS panel_person (
    person_key       VARCHAR PRIMARY KEY,   -- stable slug, e.g. 'zophar'
    display_name     VARCHAR NOT NULL,
    -- {"twitter": "zopharfpl", "youtube": "@...", ...}. JSON rather than
    -- columns because the set of platforms is not ours to fix.
    handles_json     VARCHAR,
    -- Names this person is called ON AIR, as JSON array. Used for title-basis
    -- attribution and for nothing else. Curated by a human in the panel file:
    -- an alias here is a licence to attribute an episode to someone, so it is
    -- never derived from the corpus.
    aliases_json     VARCHAR,
    -- The FPL entry. NULL is normal and NULL is honest; see the header.
    entry_id         BIGINT,
    entry_verified   BOOLEAN NOT NULL DEFAULT FALSE,
    entry_source_url VARCHAR,
    entry_api_name   VARCHAR,   -- what /api/entry/<id>/ calls it, if checked
    entry_checked_utc TIMESTAMPTZ,
    -- Required when entry_id IS NULL. Enforced by panel.load_panel, which
    -- refuses a person who has neither an id nor a reason rather than letting
    -- an unexplained blank into the table.
    entry_reason     VARCHAR,
    edge_note        VARCHAR,   -- free text: what this person is good at
    top10k_finishes  INTEGER,   -- as stated by the source in entry_source_url
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    as_of            TIMESTAMPTZ NOT NULL
);

-- A person appears on N shows; a show has N people.
--
-- Keyed on `show_creator` rather than `source_key` because that is the grain
-- the corpus is stored at: content_item.creator is the show name, and one show
-- publishes through several sources (a YouTube channel AND a podcast feed).
-- `source_key` narrows the mapping to one of those feeds when a person really
-- is on only one of them; NULL means all of the show's sources.
CREATE TABLE IF NOT EXISTS panel_person_show (
    person_key   VARCHAR NOT NULL,
    show_creator VARCHAR NOT NULL,   -- joins content_item.creator
    source_key   VARCHAR,            -- optional narrowing; NULL = every source
    role         VARCHAR,            -- host | co_host | guest | producer
    as_of        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (person_key, show_creator)
);

-- Which person an item is attributable to. NO ROW = attributed to the show,
-- which is a real answer and the correct one for a round-table episode.
CREATE TABLE IF NOT EXISTS item_person (
    item_id     VARCHAR NOT NULL,
    person_key  VARCHAR NOT NULL,
    -- sole_host | title | stated | manual. Closed set; see the header.
    basis       VARCHAR NOT NULL,
    -- How firmly the ATTRIBUTION is established -- not how right the pick is,
    -- and not how firmly the pick was stated. Mirrors content_claim.confidence
    -- in spirit and is a different quantity.
    confidence  DOUBLE NOT NULL,
    -- The verbatim text that justified it: the matched span of the title, the
    -- transcript sentence. NULL for sole_host, which is structural and has no
    -- quotable evidence.
    evidence    VARCHAR,
    attributed_utc TIMESTAMPTZ NOT NULL,
    -- Two bases may both hold for one item (the title names them AND they say
    -- so on air). Both are kept: they are separate evidence, and collapsing
    -- them would discard the corroboration.
    PRIMARY KEY (item_id, person_key, basis)
);
