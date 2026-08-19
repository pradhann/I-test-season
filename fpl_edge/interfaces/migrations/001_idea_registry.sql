-- Idea registry: the user's own hypotheses as first-class, tracked objects.
--
-- This migration lives under fpl_edge/interfaces/ rather than in
-- fpl_edge/store/schema.sql on purpose. schema.sql is the shared contract that
-- the ingest, model and optimiser teams read; adding interface-owned tables
-- there would make every one of those teams' migrations conflict with ours.
-- The runner in fpl_edge/interfaces/registry.py applies this file idempotently
-- against the same DuckDB file, so there is one warehouse and one connection --
-- just two owners of DDL.
--
-- These tables are deliberately NOT registered in store.PIT_KEYS. PIT_KEYS
-- describes *facts about the world* that were observable at an instant, read
-- through Snapshot so a backtest cannot see the future. An idea is not a fact
-- about the world; it is a record of what the user believed and when. It is
-- append-mostly and keyed by a stable id, not by (entity, as_of).
--
-- The point-in-time discipline still applies to the idea's *inputs*: `as_of` on
-- `idea` records the snapshot instant the verdict and context were computed
-- from, and `idea_context` freezes the features as they stood at that instant.
-- Resolution later reads realised results, which is legitimate because the
-- thesis was written down before them.

CREATE TABLE IF NOT EXISTS schema_migration (
    version     VARCHAR PRIMARY KEY,
    applied_utc TIMESTAMPTZ NOT NULL,
    sha256      VARCHAR NOT NULL
);

-- One row per thing the user ever said. Never deleted, never overwritten except
-- by resolution, so an idea the user ignored is tracked exactly as hard as one
-- they acted on -- that is the entire point of the table.
CREATE TABLE IF NOT EXISTS idea (
    idea_id           VARCHAR PRIMARY KEY,
    created_utc       TIMESTAMPTZ NOT NULL,
    as_of             TIMESTAMPTZ NOT NULL,  -- snapshot instant the verdict saw
    source            VARCHAR NOT NULL,      -- 'telegram' | 'cli' | 'mcp' | 'test'
    source_ref        VARCHAR,               -- chat id / message id, for audit only
    raw_text          VARCHAR NOT NULL,      -- UNTRUSTED user input, stored verbatim
    season            VARCHAR NOT NULL,
    kind              VARCHAR NOT NULL,      -- captain|transfer_in|fade|differential|compare|watch
    subject_code      INTEGER,               -- stable PlayerCode, never element_id
    subject_name      VARCHAR,
    comparator        VARCHAR NOT NULL,      -- median_captain|price_peer_median|named_player|field_median
    comparator_code   INTEGER,
    comparator_label  VARCHAR NOT NULL,
    gw                INTEGER NOT NULL,      -- first gameweek the thesis covers
    horizon_gws       INTEGER NOT NULL,
    thesis            VARCHAR NOT NULL,      -- the falsifiable claim, in English
    resolution_rule   VARCHAR NOT NULL,      -- exactly what would falsify it
    parse_confidence  DOUBLE NOT NULL,
    status            VARCHAR NOT NULL,      -- 'open' | 'resolved' | 'void'
    acted             BOOLEAN NOT NULL,      -- did the user actually do it
    acted_utc         TIMESTAMPTZ,
    resolved_utc      TIMESTAMPTZ,
    outcome           VARCHAR,               -- 'correct' | 'incorrect' | 'push'
    subject_points    DOUBLE,
    comparator_points DOUBLE
);

-- The model's answer at the moment the idea was submitted. Separate table
-- because a verdict is a model output with a provider and a version: when the
-- simulation lands and re-scores history, the old verdicts stay on the record.
CREATE TABLE IF NOT EXISTS idea_verdict (
    verdict_id       VARCHAR PRIMARY KEY,
    idea_id          VARCHAR NOT NULL,
    issued_utc       TIMESTAMPTZ NOT NULL,
    provider         VARCHAR NOT NULL,
    provider_version VARCHAR NOT NULL,
    stance           VARCHAR NOT NULL,      -- 'agree' | 'disagree' | 'neutral'
    p_thesis_true    DOUBLE NOT NULL,       -- P(the written thesis resolves correct)
    confidence       VARCHAR NOT NULL,      -- 'low' | 'medium' | 'high'
    rationale        VARCHAR NOT NULL,
    degraded         BOOLEAN NOT NULL,      -- fell back from the primary provider
    latency_ms       DOUBLE NOT NULL
);

-- Features frozen at submission time. This table exists so that `fpl idea
-- review` can COMPUTE the user's biases rather than assert them: each probe
-- needs the subject's state and the population base rate as they both stood at
-- the instant the idea was had, which is unrecoverable afterwards.
CREATE TABLE IF NOT EXISTS idea_context (
    idea_id             VARCHAR PRIMARY KEY,
    captured_utc        TIMESTAMPTZ NOT NULL,
    position            INTEGER,
    team_code           INTEGER,
    price_tenths        INTEGER,
    selected_by_pct     DOUBLE,
    availability        VARCHAR,
    form_points_last3   DOUBLE,
    form_percentile     DOUBLE,   -- vs same-position peers with minutes, at as_of
    minutes_last3       INTEGER,
    last_match_gw       INTEGER,
    last_match_points   INTEGER,
    last_match_was_home BOOLEAN,  -- the home-bias probe
    gws_since_haul      INTEGER,  -- the recency probe; NULL if never hauled
    season_ppg          DOUBLE,
    -- Population base rates measured at the same instant, so every bias test has
    -- a null hypothesis drawn from the same world the user was looking at.
    pop_home_rate       DOUBLE,
    pop_mean_gws_since_haul DOUBLE,
    pop_recent_haul_rate DOUBLE,  -- fraction of the universe that hauled recently
    pop_mean_form_pct   DOUBLE,   -- sanity check: must sit at ~0.5 by construction
    pop_n               INTEGER,
    -- Club affinity. The user supports Man Utd, and "he plays for my club" is a
    -- selection pressure with no predictive content. Stored as the subject's
    -- club vs the share of the SELECTABLE universe at that club, because unlike
    -- the form/venue/haul probes this one is measurable from GW1 -- it needs no
    -- results, only a squad list.
    supported_team_code INTEGER,
    is_supported_club   BOOLEAN,
    pop_supported_club_rate DOUBLE,
    pop_universe_n      INTEGER
);

-- The tracking trail. One row per (idea, gameweek) once results finalise,
-- written for every open idea whether or not the user acted on it.
CREATE TABLE IF NOT EXISTS idea_observation (
    idea_id           VARCHAR NOT NULL,
    gw                INTEGER NOT NULL,
    observed_utc      TIMESTAMPTZ NOT NULL,
    subject_points    DOUBLE,
    comparator_points DOUBLE,
    note              VARCHAR,
    PRIMARY KEY (idea_id, gw)
);

-- A message we could not resolve without guessing. Held so the next reply from
-- the same chat can complete it; this is what makes "ask, don't guess" work as a
-- conversation rather than a dead end.
CREATE TABLE IF NOT EXISTS idea_pending (
    pending_id   VARCHAR PRIMARY KEY,
    created_utc  TIMESTAMPTZ NOT NULL,
    source       VARCHAR NOT NULL,
    source_ref   VARCHAR NOT NULL,
    raw_text     VARCHAR NOT NULL,   -- UNTRUSTED, verbatim
    question     VARCHAR NOT NULL,
    candidates   VARCHAR NOT NULL,   -- JSON array of {code, label, hint}
    resolved     BOOLEAN NOT NULL
);
