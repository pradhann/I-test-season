-- The other half of what a creator says: OBSERVATIONS, not recommendations.
--
-- content_claim records one grain and one grain only: a player plus an action
-- from the closed Action set (buy/sell/hold/captain/bench/avoid). That grain
-- captures "buy Haaland" perfectly and captures NOTHING of the sentences an
-- analytical channel actually spends its hour on:
--
--   "Semenyo is playing as a false nine now."
--   "Arsenal's fixtures turn in GW6."
--   "Wirtz has been on set pieces since the international break."
--   "Spurs rotate their keeper in cup weeks."
--
-- None of those is a call. All of them are the reason a call gets made, and a
-- creator whose whole output is that kind of sentence -- SolioAnalytics is the
-- worked example -- currently reads through this pipeline as having said
-- nothing at all. content_insight is that missing grain.
--
-- WHY THIS IS NOT A CLAIM, AND MUST NEVER BECOME ONE
--
-- The separation is the entire point of the table, so it is worth being blunt
-- about it. "Semenyo is on set pieces" is an observation about the present. It
-- may inform a buy. It is not a buy, and nobody predicted anything by saying
-- it. If insights were folded into content_claim they would be swept into
-- claim_outcome and creator_score, and the scoreboard -- which exists to ask
-- "did this creator's PREDICTIONS come true?" -- would start settling opinions
-- that were never predictions. A creator who correctly observed a role change
-- would be marked wrong because the player then blanked. That is not a
-- measurement of anything.
--
-- So: there is no insight_outcome table, there is no insight column in
-- creator_score, and content_insight rows must never be inserted into
-- content_claim. An insight is evidence a human reads. It is not a bet.
--
-- The conviction band in `confidence` carries the same 0.8/0.6/0.4 encoding as
-- content_claim.confidence for consistency of reading, but it is NOT a
-- calibration target here, because nothing settles it. It says how firmly the
-- creator asserted the observation, and that is all it will ever say.
--
-- THE ENTITY MODEL, WHICH IS WHERE THE SHAPE ACTUALLY DIFFERS
--
-- A claim is always about exactly one player, so content_claim can afford
-- `player_code INTEGER NOT NULL`. An insight cannot. "Arsenal's fixtures turn
-- in GW6" is about a TEAM across a RANGE of gameweeks; "the international
-- break resets everyone's minutes" is about no entity at all. Forcing those
-- through a player_code column would mean inventing a player, and inventing a
-- player is exactly the failure mode this package is built to refuse.
--
-- Hence four columns instead of one, and the split between them is deliberate:
--
--   entity_kind   player | team | fixture | gameweek | none. Closed set. Says
--                 what the other three columns mean.
--   player_code   The stable cross-season PlayerCode, never element_id, and
--                 NULLABLE. Set only when entity_kind = 'player' AND the same
--                 strict resolver that guards content_claim actually resolved
--                 the name. A player the resolver refuses to guess keeps
--                 entity_name and gets a NULL code -- the row survives, the UI
--                 can still show the creator's words, and nothing downstream
--                 can join a stranger's history onto it.
--   entity_ref    A NORMALISED STRING for the non-player kinds: the team,
--                 fixture or gameweek the insight is about, lowercased and
--                 accent-folded through the repo's one name normaliser so
--                 "Spurs" and "spurs" group. It is deliberately NOT a foreign
--                 key and must not be read as one: this package has no team-code
--                 resolver, the ones that exist elsewhere are bookmaker-specific
--                 alias tables owned by another team, and building a second name
--                 matcher here is precisely the drift that names.norm exists to
--                 prevent. A grouping key, honestly labelled.
--   entity_name   As SPOKEN, verbatim, always populated. The audit column. When
--                 player_code is NULL this is the only record of who was meant,
--                 and when it is not NULL it is what lets a misresolution be
--                 found later.
--
-- WHY THERE ARE TWO HORIZON COLUMNS
--
-- The brief for this table proposed a single `horizon_gw`. A fixture swing is
-- the single most common insight an FPL analyst voices and it is intrinsically
-- a RANGE -- "Arsenal's fixtures turn in GW6 and stay good through GW12". A
-- lone integer forces that to be stored as GW6, which drops the half of the
-- statement a planner needs. `horizon_gw` is the first gameweek it applies to,
-- `horizon_gw_end` the last; both NULL when the speaker named no window, and
-- horizon_gw_end NULL with horizon_gw set means a single gameweek. Nothing is
-- inferred into either: an unstated horizon stays NULL rather than defaulting
-- to the gameweek the item was published into.
--
-- `gameweek` and `season` are a different fact entirely: they are the gameweek
-- the ITEM was published into, inferred from published_at exactly as
-- content_claim does it. "When was this said" and "when does it apply" are not
-- the same question and are not the same column.
--
-- QUOTES ARE MANDATORY
--
-- `quote NOT NULL` and the writer refuses an empty one. An insight without a
-- verbatim span from the source is an assertion the pipeline made up, and this
-- corpus does not store those. `claim_text` is the model's one-line rendering
-- for display; `quote` is the receipt. `start_s` is the deep-link offset when
-- the quote can be located in transcript_segment, and NULL when it cannot --
-- never a guessed offset, following platform/scripts/creators.deep_link.
--
-- WHAT IS ALLOWED TO PRODUCE A ROW
--
-- Only sources that contain speech or prose: transcripts and articles. Show
-- notes are gated out at the writer (see analyze.insights_permitted). The
-- reasoning is not the same as the one behind is_scoreable -- nothing here
-- feeds a calibration band -- it is the quote requirement. A description is a
-- headline, sponsor copy, affiliate links and a chapter list; a chapter marker
-- reading "12:30 Semenyo's new role" names a TOPIC, and a model asked to quote
-- an observation out of it will produce "Semenyo's new role" as though someone
-- had asserted something. Nobody asserted anything. Empty is the true answer.
--
-- POINT-IN-TIME
--
-- Same as content_001_claims.sql, for the same reason: NOT in store.PIT_KEYS.
-- An insight is an immutable utterance, made once, never superseded; modelling
-- it as (entity, as_of) would imply a newer version can exist. `published_at`
-- is the load-bearing column and the only one a reader may filter on. The
-- sanctioned read is analyze.insights_visible_at, which mirrors
-- ContentStore.claims_visible_at exactly -- strictly `published_at < as_of`,
-- because an insight published at the instant of the deadline could not have
-- been read before it.

CREATE TABLE IF NOT EXISTS content_insight (
    insight_id     VARCHAR PRIMARY KEY,
    item_id        VARCHAR NOT NULL,
    creator        VARCHAR NOT NULL,
    source_key     VARCHAR NOT NULL,

    -- Closed set: role_change | set_pieces | minutes | fixture_swing
    --           | tactical | injury_return | price | chip_strategy | other
    topic          VARCHAR NOT NULL,

    -- Closed set: player | team | fixture | gameweek | none
    entity_kind    VARCHAR NOT NULL,
    -- Stable PlayerCode, never element_id. NULL when the entity is not a
    -- player, or is a player the strict resolver refused to guess.
    player_code    INTEGER,
    -- Normalised grouping string for non-player entities. NOT a foreign key.
    entity_ref     VARCHAR,
    -- As spoken, verbatim. Always present; the audit trail for the above two.
    entity_name    VARCHAR NOT NULL,

    -- The observation in one line, in the model's words. For display.
    claim_text     VARCHAR NOT NULL,
    -- VERBATIM from the source. The receipt. Never empty.
    quote          VARCHAR NOT NULL,
    -- Deep-link offset into the item, or NULL when the quote is unlocatable.
    start_s        DOUBLE,

    -- The window the insight applies to, when the speaker named one. Both NULL
    -- when they did not; end NULL with start set means a single gameweek.
    horizon_gw     INTEGER,
    horizon_gw_end INTEGER,

    -- Conviction band, high 0.8 / medium 0.6 / low 0.4. How firmly it was
    -- asserted. NOT a calibration target: nothing settles an observation.
    confidence     DOUBLE NOT NULL,

    -- When the item was published, and the season/gameweek that lands in.
    published_at   TIMESTAMPTZ NOT NULL,
    season         VARCHAR NOT NULL,
    gameweek       INTEGER NOT NULL,

    -- 'llm:<model>'. There is no cue extractor for insights and there will not
    -- be one: a keyword window cannot tell an observation from a mention.
    extractor      VARCHAR NOT NULL
);
