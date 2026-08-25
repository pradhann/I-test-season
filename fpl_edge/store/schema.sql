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

-- ---------------------------------------------------------------------------
-- Projection tables. Historically created only by the projections package's
-- migrations, which meant a fresh warehouse did not have them and the
-- semantic layer (views.sql) could not even be created. The migrations remain
-- (IF NOT EXISTS keeps them idempotent) and stay authoritative for
-- *evolution*; this is the base shape so every warehouse is complete at birth.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_projection (
    provider       VARCHAR NOT NULL,   -- 'fplform' | 'internal' | 'market' | 'ppg' | ...
    season         VARCHAR NOT NULL,
    gw             INTEGER NOT NULL,   -- the gameweek being projected
    code           INTEGER NOT NULL,   -- stable FPL player code
    xp             DOUBLE,             -- expected points, appearance-weighted
    xp_if_appears  DOUBLE,             -- expected points conditional on appearing
    p_appear       DOUBLE,             -- provider's own P(any minutes)
    as_of          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider, season, gw, code, as_of)
);

ALTER TABLE fact_projection ADD COLUMN IF NOT EXISTS xmins DOUBLE;

CREATE TABLE IF NOT EXISTS fact_external_ownership (
    provider   VARCHAR NOT NULL,
    season     VARCHAR NOT NULL,
    gw         INTEGER NOT NULL,
    code       INTEGER NOT NULL,
    metric     VARCHAR NOT NULL,
    value      DOUBLE NOT NULL,
    as_of      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider, season, gw, code, metric, as_of)
);

CREATE TABLE IF NOT EXISTS projection_weight (
    fit_id         VARCHAR NOT NULL,   -- one id per fit run
    provider       VARCHAR NOT NULL,
    weight         DOUBLE NOT NULL,
    loss           DOUBLE,             -- provider's own held-out loss
    loss_metric    VARCHAR,            -- 'mae' | 'crps' | 'brier' | 'log_loss'
    baseline_loss  DOUBLE,             -- the loss the provider had to beat
    n_obs          INTEGER,            -- held-out observations behind the number
    earned         BOOLEAN NOT NULL,   -- FALSE => provisional, never measured
    holdout        VARCHAR,            -- human description of the held-out set
    as_of          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (fit_id, provider)
);

-- The accumulated per-(provider, gameweek) accuracy record that EARNS the
-- rows in projection_weight. Base shape here so a fresh warehouse is complete
-- at birth (the sem_projection_weights macro in views.sql binds against it at
-- CREATE time); the projections package's migration 004 remains authoritative
-- for evolution and is idempotent over this. Column semantics are documented
-- in fpl_edge/ingest/projections/migrations/004_projection_scores.sql.
CREATE TABLE IF NOT EXISTS fact_projection_score (
    provider      VARCHAR NOT NULL,
    season        VARCHAR NOT NULL,
    gw            INTEGER NOT NULL,
    scope         VARCHAR NOT NULL,      -- 'overall' | 'pos:GKP'..'pos:FWD' | 'p_appear'
    metric        VARCHAR NOT NULL,      -- 'mae' | 'rmse' | 'brier'
    value         DOUBLE NOT NULL,
    baseline      DOUBLE,                -- same metric, all-provider mean, same obs
    n_obs         INTEGER NOT NULL,
    deadline_utc  TIMESTAMPTZ NOT NULL,  -- projections were read as-of this instant
    as_of         TIMESTAMPTZ NOT NULL,  -- the scoring instant
    PRIMARY KEY (provider, season, gw, scope, metric, as_of)
);

CREATE TABLE IF NOT EXISTS fact_predicted_lineup (
    provider        VARCHAR NOT NULL,   -- 'rotowire' | ...
    season          VARCHAR NOT NULL,
    gw              INTEGER NOT NULL,
    code            INTEGER NOT NULL,   -- stable FPL player code
    team_code       INTEGER NOT NULL,
    predicted_start BOOLEAN NOT NULL,
    certainty       VARCHAR NOT NULL,
    as_of           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider, season, gw, code, as_of)
);

-- CONFIRMED starting lineups, copied from an official teamsheet feed
-- (currently the Premier League's own Pulselive API) the moment they are
-- published, roughly an hour before kickoff. This is a different fact from
-- fact_predicted_lineup: a prediction is somebody's opinion before the
-- teamsheet exists, a confirmed lineup is the club's own declaration. as_of is
-- the FETCH instant -- lineups drop ~T-60m, so a snapshot_at(deadline) read
-- correctly cannot see a lineup that was not yet public. Append-only; a late
-- change (warm-up injury) arrives as new rows at a later as_of.
CREATE TABLE IF NOT EXISTS fact_confirmed_lineup (
    source          VARCHAR NOT NULL,   -- 'pulselive' | ...
    season          VARCHAR NOT NULL,
    fixture_id      INTEGER NOT NULL,   -- OUR fixture id (fact_fixture)
    code            INTEGER NOT NULL,   -- stable FPL player code
    started         BOOLEAN NOT NULL,   -- TRUE = in the XI, FALSE = on the bench
    shirt           INTEGER,
    position_label  VARCHAR,            -- publisher's label ('G','D','M','F')
    formation       VARCHAR,            -- the side's formation label ('4-2-3-1')
    as_of           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, season, fixture_id, code, as_of)
);

-- Identity bridges for the Pulselive feed. Each player is name-matched ONCE,
-- and the successful (pl_player_id -> code) result is persisted here so every
-- later ingest joins by id; a name that could not be matched unambiguously is
-- NEVER written (drop-and-count lives in the ingest). Same for fixtures:
-- (pl_fixture_id -> our fixture_id), matched by kickoff instant + team pair.
CREATE TABLE IF NOT EXISTS bridge_pl_player (
    season        VARCHAR NOT NULL,
    pl_player_id  BIGINT  NOT NULL,   -- Pulselive's nested player `id`
    code          INTEGER NOT NULL,   -- stable FPL player code
    opta_id       VARCHAR,            -- altIds.opta, e.g. 'p231416'
    matched_by    VARCHAR NOT NULL,   -- 'name' | 'last_name' | 'manual'
    as_of         TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, pl_player_id, as_of)
);

CREATE TABLE IF NOT EXISTS bridge_pl_fixture (
    season         VARCHAR NOT NULL,
    pl_fixture_id  BIGINT  NOT NULL,
    fixture_id     INTEGER NOT NULL,   -- OUR fixture id (fact_fixture)
    as_of          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, pl_fixture_id, as_of)
);

-- Third-party per-player per-match statistics -- xG, xA, shots, defensive
-- actions -- COPIED from a publisher, never computed here. This is a different
-- kind of fact from fact_player_fixture (the official FPL scoring return):
-- it is another party's Opta-like read of the same match, carried under its
-- own `source` so its numbers can never be mistaken for the official ones.
--
-- `match_id` is the PUBLISHER'S OWN match key (e.g. FPL-Core-Insights'
-- '26-27-prem-arsenal-vs-coventry-city'), kept verbatim rather than remapped
-- onto fact_fixture.fixture_id at write time: the mapping is an opinion that
-- can be recomputed, the publisher's key is a fact that cannot.
--
-- as_of is the fetch instant, and rows describe finished matches only, so a
-- snapshot_at(deadline) read can never see a stat before we could have.
-- Lives in the core schema for the same reason fact_odds_derived does: a
-- table that only exists after a particular module runs cannot be read by a
-- fresh warehouse.
CREATE TABLE IF NOT EXISTS fact_player_match_stats (
    source                  VARCHAR NOT NULL,   -- 'fpl_core_insights' | ...
    season                  VARCHAR NOT NULL,
    code                    INTEGER NOT NULL,   -- stable FPL player code
    match_id                VARCHAR NOT NULL,   -- the publisher's match key
    tournament              VARCHAR NOT NULL,   -- 'Premier League' | 'EFL Cup' | ...
    gw                      INTEGER,            -- publisher's gameweek label (0 = pre-season)
    minutes_played          DOUBLE,
    start_min               DOUBLE,
    finish_min              DOUBLE,
    goals                   DOUBLE,
    assists                 DOUBLE,
    penalties_scored        DOUBLE,
    penalties_missed        DOUBLE,
    total_shots             DOUBLE,
    shots_on_target         DOUBLE,
    xg                      DOUBLE,             -- publisher's expected goals
    xa                      DOUBLE,             -- publisher's expected assists
    xgot                    DOUBLE,             -- xG on target
    chances_created         DOUBLE,
    touches_opposition_box  DOUBLE,
    tackles                 DOUBLE,
    tackles_won             DOUBLE,
    interceptions           DOUBLE,
    recoveries              DOUBLE,
    blocks                  DOUBLE,
    clearances              DOUBLE,
    defensive_contributions DOUBLE,
    saves                   DOUBLE,
    goals_conceded          DOUBLE,
    xgot_faced              DOUBLE,             -- keeper: xG on target faced
    goals_prevented         DOUBLE,             -- keeper: publisher's shot-stopping delta
    as_of                   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, season, code, match_id, as_of)
);

CREATE OR REPLACE VIEW projection_normalized AS
SELECT
    provider AS source,
    code     AS player_code,
    season,
    gw,
    xmins,
    xp       AS xpts,
    xp_if_appears,
    p_appear,
    as_of    AS fetched_at
FROM fact_projection;

-- ---------------------------------------------------------------------------
-- Manager (rival) tables. Historically created by the additive migration in
-- fpl_edge/ingest/rivals/schema.py; they are ALSO here because DuckDB binds a
-- table macro's body at CREATE time, and the sem_manager_* macros in views.sql
-- (executed on every writable open) reference these tables -- a fresh
-- warehouse must therefore be complete at birth, exactly as was done for the
-- projection tables. The rivals module's migrate() remains authoritative for
-- evolution and is idempotent over this base. Column semantics are documented
-- in fpl_edge/ingest/rivals/schema.py.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_manager (
    entry_id            BIGINT   NOT NULL,
    player_name         VARCHAR,
    entry_name          VARCHAR,
    region              VARCHAR,
    years_active        INTEGER,
    favourite_team_id   INTEGER,
    started_event       INTEGER,
    source              VARCHAR  NOT NULL,
    as_of               TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, as_of)
);

CREATE TABLE IF NOT EXISTS dim_manager_league (
    entry_id     BIGINT  NOT NULL,
    league_id    BIGINT  NOT NULL,
    league_name  VARCHAR,
    league_type  VARCHAR,
    scoring      VARCHAR,
    as_of        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, league_id, as_of)
);

CREATE TABLE IF NOT EXISTS fact_manager_season (
    entry_id        BIGINT  NOT NULL,
    season          VARCHAR NOT NULL,
    total_points    INTEGER,
    overall_rank    BIGINT,
    rank_percentage      DOUBLE,
    rank_percentage_text VARCHAR,
    as_of           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, season, as_of)
);

CREATE TABLE IF NOT EXISTS fact_manager_gw (
    entry_id              BIGINT  NOT NULL,
    season                VARCHAR NOT NULL,
    gw                    INTEGER NOT NULL,
    points                INTEGER,
    total_points          INTEGER,
    overall_rank          BIGINT,
    bank_tenths           INTEGER,
    value_tenths          INTEGER,
    event_transfers       INTEGER,
    event_transfers_cost  INTEGER,
    points_on_bench       INTEGER,
    as_of                 TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, season, gw, as_of)
);

CREATE TABLE IF NOT EXISTS fact_manager_pick (
    entry_id       BIGINT  NOT NULL,
    season         VARCHAR NOT NULL,
    gw             INTEGER NOT NULL,
    element_id     INTEGER NOT NULL,
    slot           INTEGER,
    multiplier     INTEGER,
    is_captain     BOOLEAN,
    is_vice_captain BOOLEAN,
    as_of          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, season, gw, element_id, as_of)
);

CREATE TABLE IF NOT EXISTS fact_manager_transfer (
    entry_id          BIGINT  NOT NULL,
    season            VARCHAR NOT NULL,
    gw                INTEGER NOT NULL,
    element_in        INTEGER NOT NULL,
    element_in_cost   INTEGER,
    element_out       INTEGER NOT NULL,
    element_out_cost  INTEGER,
    time_utc          TIMESTAMPTZ,
    as_of             TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, season, gw, element_in, element_out, as_of)
);

CREATE TABLE IF NOT EXISTS fact_manager_chip (
    entry_id  BIGINT  NOT NULL,
    season    VARCHAR NOT NULL,
    gw        INTEGER NOT NULL,
    chip      VARCHAR NOT NULL,
    as_of     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, season, gw, as_of)
);
