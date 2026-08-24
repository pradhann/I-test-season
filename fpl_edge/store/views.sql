-- The semantic layer: the stable query surface for chat, the UI and the MCP
-- server. One guarded vocabulary instead of every consumer reaching into raw
-- tables (the Argus single-query-endpoint pattern).
--
-- CONTRACT. Each macro below is an API with a compatibility promise: columns
-- may be ADDED, never renamed or removed; behaviour changes get a new name.
-- Documented in docs/platform/semantic_layer.md. Consumers: the platform's
-- guarded /api/query, the FPL-MCP server, panel scripts.
--
-- POINT IN TIME. Every macro takes p_as_of (TIMESTAMPTZ) and answers with the
-- newest row per entity OBSERVED AT OR BEFORE that instant -- the same
-- semantics as Snapshot.table(). Pass the current time for "now"; pass a
-- deadline to see exactly what was knowable then. This is enforced by tests
-- (tests/unit/test_semantic_layer.py): a fact recorded after p_as_of is
-- invisible.
--
-- These are macros, not views, because a view cannot take the as_of
-- parameter; and they live in the database file itself, so every read copy
-- carries them.

-- ---------------------------------------------------------------------------
-- sem_players(p_as_of): identity + market state, one row per (season, code).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO sem_players(p_as_of) AS TABLE (
    WITH p AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY season, code ORDER BY as_of DESC) rn
            FROM dim_player WHERE as_of <= p_as_of) WHERE rn = 1
    ), s AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY season, code ORDER BY as_of DESC) rn
            FROM fact_player_state WHERE as_of <= p_as_of) WHERE rn = 1
    ), t AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY season, team_code ORDER BY as_of DESC) rn
            FROM dim_team WHERE as_of <= p_as_of) WHERE rn = 1
    )
    SELECT p.season, p.code, p.web_name, p.position, p.team_code,
           t.short_name AS team,
           s.price_tenths / 10.0 AS price,
           s.selected_by_pct,
           s.status, s.chance_of_playing_next_round, s.news,
           s.element_id
    FROM p
    JOIN s USING (season, code)
    LEFT JOIN t ON t.season = p.season AND t.team_code = p.team_code
);

-- ---------------------------------------------------------------------------
-- sem_projections(p_as_of): every provider's numbers, one row per
-- (source, season, gw, code), joined to identity. player_code IS the stable
-- FPL code (verified: the projection store resolves identity at ingest).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO sem_projections(p_as_of) AS TABLE (
    WITH pr AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY source, season, gw, player_code
                ORDER BY fetched_at DESC) rn
            FROM projection_normalized WHERE fetched_at <= p_as_of) WHERE rn = 1
    )
    SELECT pr.season, pr.gw, pr.player_code AS code,
           pl.web_name, pl.position, pl.team, pl.price,
           pr.source, pr.xpts, pr.xmins, pr.xp_if_appears, pr.p_appear,
           pr.fetched_at
    FROM pr
    LEFT JOIN sem_players(p_as_of) pl
      ON pl.season = pr.season AND pl.code = pr.player_code
);

-- ---------------------------------------------------------------------------
-- sem_projection_consensus(p_as_of): where the sources agree and disagree,
-- one row per (season, gw, code). The spread IS the uncertainty estimate --
-- source disagreement is a better variance signal than any single vendor's.
-- NOTE: consensus is unweighted by design. projection_weight is empty until
-- GW1 actuals score the sources; weighting without a track record would be
-- fabrication (see MASTER_PROMPT Phase 2.5).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO sem_projection_consensus(p_as_of) AS TABLE (
    SELECT season, gw, code, any_value(web_name) AS web_name,
           any_value(position) AS position, any_value(team) AS team,
           any_value(price) AS price,
           COUNT(DISTINCT source)              AS n_sources,
           AVG(xpts)                            AS xpts_mean,
           MIN(xpts)                            AS xpts_min,
           MAX(xpts)                            AS xpts_max,
           MAX(xpts) - MIN(xpts)                AS xpts_spread,
           stddev_samp(xpts)                    AS xpts_sd,
           AVG(xmins)                           AS xmins_mean,
           COUNT(xmins)                         AS n_sources_xmins
    FROM sem_projections(p_as_of)
    WHERE xpts IS NOT NULL
    GROUP BY season, gw, code
);

-- ---------------------------------------------------------------------------
-- sem_player_form(p_as_of): realised per-gameweek returns INCLUDING official
-- xG/xA/xGC, one row per (season, gw, code, fixture). as_of on these rows is
-- the points-finalisation instant, so at a deadline you see only completed,
-- settled gameweeks -- a backtest cannot read a result early.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO sem_player_form(p_as_of) AS TABLE (
    WITH f AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY season, code, fixture_id ORDER BY as_of DESC) rn
            FROM fact_player_fixture WHERE as_of <= p_as_of) WHERE rn = 1
    )
    SELECT f.season, f.gw, f.code, f.fixture_id, f.was_home,
           f.minutes, f.total_points, f.goals_scored, f.assists,
           f.clean_sheets, f.goals_conceded, f.bonus, f.bps, f.starts,
           f.expected_goals, f.expected_assists, f.expected_goals_conceded,
           f.tackles, f.clearances_blocks_interceptions, f.recoveries,
           f.defensive_contribution,
           f.saves, f.yellow_cards, f.red_cards
    FROM f
);

-- ---------------------------------------------------------------------------
-- sem_ownership(p_as_of): what the field holds. FPL's own marginal ownership
-- plus every external effective-ownership metric present, one row per
-- (season, code, metric-source view). eo_* metrics are per-gw where the feed
-- is; selected_by_pct is the state at p_as_of.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO sem_ownership(p_as_of) AS TABLE (
    WITH eo AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY provider, season, gw, code, metric
                ORDER BY as_of DESC) rn
            FROM fact_external_ownership WHERE as_of <= p_as_of) WHERE rn = 1
    )
    SELECT pl.season, pl.code, pl.web_name, pl.position, pl.team, pl.price,
           pl.selected_by_pct,
           eo.gw AS eo_gw, eo.provider AS eo_provider,
           eo.metric AS eo_metric, eo.value AS eo_value
    FROM sem_players(p_as_of) pl
    LEFT JOIN eo ON eo.season = pl.season AND eo.code = pl.code
);

-- ---------------------------------------------------------------------------
-- sem_fixtures(p_as_of): the schedule as known at the instant, one row per
-- (season, fixture_id, team side). Difficulty deliberately NOT here: it lives
-- in the cached ratings artefact (data/warehouse/fixture_difficulty.parquet)
-- because a rating is a model output with its own refresh cycle, not a fact.
-- The fixtures panel joins it; SQL consumers read the parquet directly.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO sem_fixtures(p_as_of) AS TABLE (
    WITH fx AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY season, fixture_id ORDER BY as_of DESC) rn
            FROM fact_fixture WHERE as_of <= p_as_of) WHERE rn = 1
    ), t AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY season, team_code ORDER BY as_of DESC) rn
            FROM dim_team WHERE as_of <= p_as_of) WHERE rn = 1
    )
    SELECT fx.season, fx.fixture_id, fx.gw, fx.kickoff_utc, fx.finished,
           side.team_code, side.opponent_code, side.is_home,
           th.short_name AS team, ta.short_name AS opponent,
           CASE WHEN side.is_home THEN fx.home_score ELSE fx.away_score END AS goals_for,
           CASE WHEN side.is_home THEN fx.away_score ELSE fx.home_score END AS goals_against
    FROM fx
    CROSS JOIN LATERAL (VALUES
        (fx.home_team_code, fx.away_team_code, TRUE),
        (fx.away_team_code, fx.home_team_code, FALSE)
    ) AS side(team_code, opponent_code, is_home)
    LEFT JOIN t th ON th.season = fx.season AND th.team_code = side.team_code
    LEFT JOIN t ta ON ta.season = fx.season AND ta.team_code = side.opponent_code
);
