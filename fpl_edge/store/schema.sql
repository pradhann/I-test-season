-- fpl-edge warehouse schema.
--
-- Point-in-time discipline: every mutable fact table carries `as_of`, the
-- instant the fact first became PUBLICLY OBSERVABLE. Not the instant the match
-- happened, not the instant we backfilled it -- the instant a manager sitting at
-- their keyboard could have known it. All model input is read through
-- snapshot_at(deadline), which filters as_of <= deadline.
--
-- Cross-season joins key on `code` (stable). `element_id` is per-season only.

CREATE TABLE IF NOT EXISTS raw_fetch (
    fetch_id     BIGINT PRIMARY KEY,
    source       VARCHAR NOT NULL,      -- 'fpl_api' | 'understat' | 'fbref' | 'odds' | ...
    endpoint     VARCHAR NOT NULL,
    params       VARCHAR,
    fetched_at   TIMESTAMPTZ NOT NULL,
    sha256       VARCHAR NOT NULL,
    body_path    VARCHAR NOT NULL,      -- relative path under data/raw
    http_status  INTEGER
);

CREATE TABLE IF NOT EXISTS dim_event (
    season       VARCHAR NOT NULL,
    gw           INTEGER NOT NULL,
    deadline_utc TIMESTAMPTZ NOT NULL,
    is_finished  BOOLEAN,
    as_of        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, gw, as_of)
);

CREATE TABLE IF NOT EXISTS dim_team (
    season       VARCHAR NOT NULL,
    team_code    INTEGER NOT NULL,      -- stable across seasons
    team_id      INTEGER NOT NULL,      -- per-season 1..20
    name         VARCHAR NOT NULL,
    short_name   VARCHAR NOT NULL,
    as_of        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, team_code, as_of)
);

CREATE TABLE IF NOT EXISTS dim_player (
    season       VARCHAR NOT NULL,
    code         INTEGER NOT NULL,      -- stable across seasons
    element_id   INTEGER NOT NULL,      -- per-season
    web_name     VARCHAR NOT NULL,
    first_name   VARCHAR,
    second_name  VARCHAR,
    position     INTEGER NOT NULL,      -- 1..4; element_type 5 (Manager, 25/26) is dropped upstream
    team_code    INTEGER NOT NULL,
    as_of        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, code, as_of)
);

-- Everything that moves between deadlines: price, ownership, availability, news.
CREATE TABLE IF NOT EXISTS fact_player_state (
    season                        VARCHAR NOT NULL,
    code                          INTEGER NOT NULL,
    element_id                    INTEGER NOT NULL,
    price_tenths                  INTEGER NOT NULL,
    selected_by_pct               DOUBLE,
    status                        VARCHAR,
    chance_of_playing_next_round  INTEGER,
    news                          VARCHAR,
    news_added                    TIMESTAMPTZ,
    transfers_in_event            BIGINT,
    transfers_out_event           BIGINT,
    cost_change_start             INTEGER,
    -- FPL's own authority on whether a player may be picked at all. Filtering
    -- on `status` alone is wrong: the two can diverge, and can_select is the
    -- field the game actually enforces.
    can_select                    BOOLEAN,
    can_transact                  BOOLEAN,
    removed                       BOOLEAN,
    as_of                         TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, code, as_of)
);

CREATE TABLE IF NOT EXISTS fact_fixture (
    season          VARCHAR NOT NULL,
    fixture_id      INTEGER NOT NULL,
    gw              INTEGER,            -- NULL for unscheduled/postponed
    kickoff_utc     TIMESTAMPTZ,
    home_team_code  INTEGER NOT NULL,
    away_team_code  INTEGER NOT NULL,
    finished        BOOLEAN,
    home_score      INTEGER,
    away_score      INTEGER,
    as_of           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, fixture_id, as_of)
);

-- Realised per-player per-fixture returns. as_of = points finalisation time,
-- NOT kickoff. Provisional bonus during a gameweek is a different fact.
CREATE TABLE IF NOT EXISTS fact_player_fixture (
    season                          VARCHAR NOT NULL,
    code                            INTEGER NOT NULL,
    fixture_id                      INTEGER NOT NULL,
    gw                              INTEGER NOT NULL,
    minutes                         INTEGER,
    goals_scored                    INTEGER,
    assists                         INTEGER,
    clean_sheets                    INTEGER,
    goals_conceded                  INTEGER,
    own_goals                       INTEGER,
    penalties_saved                 INTEGER,
    penalties_missed                INTEGER,
    yellow_cards                    INTEGER,
    red_cards                       INTEGER,
    saves                           INTEGER,
    bonus                           INTEGER,
    bps                             INTEGER,
    starts                          INTEGER,
    tackles                         INTEGER,
    clearances_blocks_interceptions INTEGER,
    recoveries                      INTEGER,
    defensive_contribution          INTEGER,
    expected_goals                  DOUBLE,
    expected_assists                DOUBLE,
    expected_goals_conceded         DOUBLE,
    total_points                    INTEGER,
    was_home                        BOOLEAN,
    as_of                           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, code, fixture_id, as_of)
);

CREATE TABLE IF NOT EXISTS fact_odds (
    fixture_key  VARCHAR NOT NULL,      -- season:fixture_id once matched, else a natural key
    bookmaker    VARCHAR NOT NULL,
    market       VARCHAR NOT NULL,      -- 'h2h' | 'totals' | 'anytime_scorer' | 'clean_sheet'
    selection    VARCHAR NOT NULL,
    price_decimal DOUBLE NOT NULL,
    as_of        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (fixture_key, bookmaker, market, selection, as_of)
);

CREATE SEQUENCE IF NOT EXISTS seq_fetch_id START 1;

-- Derived odds: clean-sheet, team-lambda and scorer priors computed from
-- stored fact_odds rows (see fpl_edge/ingest/odds_derived.py for the maths).
-- Lives in the core schema, not module-created DDL: a table that only exists
-- after a particular module runs cannot be read by a fresh warehouse, which is
-- how this data spent its first days with no reader.
CREATE TABLE IF NOT EXISTS fact_odds_derived (
    fixture_key  VARCHAR NOT NULL,   -- same convention as fact_odds
    season       VARCHAR NOT NULL,
    entity_type  VARCHAR NOT NULL,   -- 'team' | 'player'
    entity_code  INTEGER NOT NULL,   -- FPL team_code or player code
    market       VARCHAR NOT NULL,   -- clean_sheet_prob | team_lambda | anytime_prob | xg_share
    method       VARCHAR NOT NULL,   -- how the number was derived
    value        DOUBLE  NOT NULL,   -- probability for *_prob markets, a rate for team_lambda
    as_of        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (fixture_key, entity_type, entity_code, market, method, as_of)
);
