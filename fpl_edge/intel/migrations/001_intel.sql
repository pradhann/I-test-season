-- Intel: news, press-conference coverage, set-piece duty, tactical signals.
--
-- This migration lives under fpl_edge/intel/ and is applied by
-- fpl_edge/intel/store.py, for the same reason the idea registry lives under
-- fpl_edge/interfaces/: fpl_edge/store/schema.sql is the shared contract the
-- ingest, model and optimiser teams read, and adding intel tables there would
-- make every one of those teams' migrations conflict with ours. There is one
-- warehouse and one connection -- three owners of DDL, none of whom edit each
-- other's files.
--
-- POINT-IN-TIME CONTRACT, and the one thing to get right here.
--
-- Every table below carries TWO timestamps and they mean different things:
--
--   published_at / as_of   the instant the fact became PUBLICLY OBSERVABLE.
--                          A manager sitting at their keyboard could have known
--                          it then. This is what reads filter on.
--   observed_at            the instant WE fetched it. Strictly >= published_at.
--                          Never used for filtering, only for auditing how late
--                          our pipeline was.
--
-- Filtering on observed_at would be the wrong kind of safe: it would hide facts
-- the user genuinely knew at the deadline just because our poller was slow.
-- Filtering on published_at is what makes a backtest honest. The gap between
-- the two is itself a measurement, which is why both are stored.
--
-- For availability, published_at comes from FPL's own `news_added`, which is
-- the single reason this engine uses the FPL API as its injury feed rather than
-- a scraped injury table: a scraped table carries no publication timestamp, so
-- a backtest built on one silently applies today's injury list to a deadline
-- three weeks ago. See fpl_edge/ingest/injuries.py for the survey of what else
-- was tried and why each alternative was rejected.

CREATE TABLE IF NOT EXISTS schema_migration (
    version     VARCHAR PRIMARY KEY,
    applied_utc TIMESTAMPTZ NOT NULL,
    sha256      VARCHAR NOT NULL
);

-- One row per discrete piece of news. `item_id` is a content hash, so replaying
-- the same archived bootstrap produces the same id and the insert is a no-op:
-- ingestion is idempotent and the archive can be replayed from scratch.
CREATE TABLE IF NOT EXISTS intel_item (
    item_id       VARCHAR PRIMARY KEY,
    published_at  TIMESTAMPTZ NOT NULL,   -- world could know. Reads filter on this.
    observed_at   TIMESTAMPTZ NOT NULL,   -- we fetched it. Audit only.
    season        VARCHAR,
    kind          VARCHAR NOT NULL,       -- availability | press_conference |
                                          -- set_piece | out_of_position |
                                          -- formation | source_probe
    player_code   INTEGER,                -- stable PlayerCode, never element_id
    team_code     INTEGER,
    headline      VARCHAR NOT NULL,
    body          VARCHAR,
    source        VARCHAR NOT NULL,
    source_url    VARCHAR,
    http_status   INTEGER,
    -- How much to trust it. 1.0 for a first-party FPL field, lower for anything
    -- inferred. Stored rather than assumed so a consumer can filter.
    confidence    DOUBLE
);

-- Set-piece and penalty duty, as FPL itself states it. `ord` = 1 is the first
-- taker. A row with ord IS NULL records that a player who previously HELD a duty
-- no longer appears on the list, which is the change that matters most and which
-- an "only store what is listed" table cannot express.
CREATE TABLE IF NOT EXISTS set_piece_duty (
    season     VARCHAR NOT NULL,
    code       INTEGER NOT NULL,
    duty       VARCHAR NOT NULL,     -- penalties | direct_freekicks | corners_indirect
    ord        INTEGER,              -- 1 = first taker; NULL = dropped off the list
    note       VARCHAR,              -- FPL's own *_text field, verbatim
    team_code  INTEGER,
    source     VARCHAR NOT NULL,
    as_of      TIMESTAMPTZ NOT NULL, -- published_at semantics
    PRIMARY KEY (season, code, duty, as_of)
);

-- A detected change in duty. Written by the detector rather than by ingestion,
-- because "X is now on penalties" is a derived fact about two observations and
-- needs its own provenance: which two.
CREATE TABLE IF NOT EXISTS set_piece_change (
    change_id         VARCHAR PRIMARY KEY,
    season            VARCHAR NOT NULL,
    code              INTEGER NOT NULL,
    team_code         INTEGER,
    duty              VARCHAR NOT NULL,
    ord_before        INTEGER,
    ord_after         INTEGER,
    prior_as_of       TIMESTAMPTZ NOT NULL,  -- the earlier observation
    detected_at       TIMESTAMPTZ NOT NULL,  -- the later one. Reads filter on this.
    -- Valuation of the change in the only unit that matters for points. Roughly
    -- 0.1 goals per game to a first-choice penalty taker; scaled by duty and by
    -- how far up or down the order the player moved.
    delta_goals_per_game DOUBLE,
    headline          VARCHAR NOT NULL
);

-- "FPL says DEF, the player performs like a MID." One row per player per
-- evaluation instant. Scored, not asserted: `score` is the margin by which the
-- observed per-90 profile favours `plays_like` over `fpl_position`, so a
-- consumer can set its own bar rather than inherit ours.
CREATE TABLE IF NOT EXISTS oop_signal (
    season       VARCHAR NOT NULL,
    code         INTEGER NOT NULL,
    fpl_position INTEGER NOT NULL,    -- 1..4, FPL's element_type
    plays_like   INTEGER NOT NULL,    -- 1..4, what the rates say
    score        DOUBLE NOT NULL,
    evidence     VARCHAR NOT NULL,
    as_of        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, code, as_of)
);

-- Formation, counted from who actually started, IN FPL'S OWN CLASSIFICATION.
-- Deliberately not called "the real formation": a back three with wing-backs
-- that FPL lists as midfielders shows up here as 3-5-2 whether the manager
-- calls it that or not. That mismatch is the signal, not a defect -- it is
-- exactly what makes a wing-back cheap in the wrong bucket.
CREATE TABLE IF NOT EXISTS formation_observation (
    season     VARCHAR NOT NULL,
    team_code  INTEGER NOT NULL,
    fixture_id INTEGER NOT NULL,
    gw         INTEGER,
    shape      VARCHAR NOT NULL,      -- 'D-M-F', e.g. '3-5-2'
    n_def      INTEGER NOT NULL,
    n_mid      INTEGER NOT NULL,
    n_fwd      INTEGER NOT NULL,
    as_of      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, team_code, fixture_id, as_of)
);

-- What we asked for, what we got back, and whether we were allowed to ask.
-- Kept in the warehouse rather than in a comment so that "blocked" is a dated
-- measurement someone can re-run, not a claim in a docstring that rots.
CREATE TABLE IF NOT EXISTS source_probe (
    probe_id      VARCHAR PRIMARY KEY,
    probed_at     TIMESTAMPTZ NOT NULL,
    source        VARCHAR NOT NULL,
    url           VARCHAR NOT NULL,
    http_status   INTEGER,            -- NULL when the request never completed
    robots_status INTEGER,
    robots_allows BOOLEAN,            -- NULL when robots.txt was unreadable
    bytes         BIGINT,
    verdict       VARCHAR NOT NULL,   -- usable | blocked | disallowed | error
    note          VARCHAR
);
