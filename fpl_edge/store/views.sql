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
-- NOTE: consensus is unweighted by design. projection_weight stays empty
-- until settled actuals score the sources; weighting without a track record
-- would be fabrication (see MASTER_PROMPT Phase 2.5). Earned weights, with
-- the evidence that earned them, are read via sem_projection_weights below;
-- a weighted blend will be a NEW macro, never a silent change to this one.
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
-- sem_projection_weights(p_as_of): the current ensemble weights WITH THE
-- EVIDENCE THAT EARNED THEM, one row per provider from the latest fit at or
-- before the instant. Empty until the calibration loop
-- (fpl_edge/eval/projection_scoring.py, run by post_gw after settlement) has
-- scored at least one settled gameweek -- by design, not by accident: a
-- weight with no n_obs and no loss is an opinion, and this surface refuses to
-- serve opinions as weights.
--
-- Provenance travels with the number: loss/baseline_loss/n_obs/holdout come
-- straight from projection_weight, and track_record_gws counts the DISTINCT
-- settled gameweeks scored at the instant -- so a consumer answering "which
-- source has been most accurate" can (and must) also say how deep the track
-- record is. With track_record_gws = 1 the leaderboard is one gameweek of
-- evidence, and the column is there precisely so nobody has to remember that.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO sem_projection_weights(p_as_of) AS TABLE (
    WITH latest_fit AS (
        SELECT fit_id
        FROM projection_weight
        WHERE as_of <= p_as_of
        ORDER BY as_of DESC, fit_id DESC
        LIMIT 1
    ), track AS (
        SELECT count(DISTINCT season || ':' || gw) AS track_record_gws
        FROM fact_projection_score
        WHERE as_of <= p_as_of
    )
    SELECT w.provider, w.weight, w.loss, w.loss_metric, w.baseline_loss,
           w.n_obs, w.earned, w.holdout, w.fit_id,
           w.as_of AS fitted_at,
           t.track_record_gws
    FROM projection_weight w
    JOIN latest_fit USING (fit_id)
    CROSS JOIN track t
    ORDER BY w.weight DESC, w.provider
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

-- ---------------------------------------------------------------------------
-- sem_player_match_stats(p_as_of): a third party's per-match read of the same
-- matches -- xG, xA, shots, defensive actions -- COPIED from its publisher and
-- carried under `source` so it can never be mistaken for the official FPL
-- return (that is sem_player_form). One row per (source, season, code,
-- publisher's own match_id).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO sem_player_match_stats(p_as_of) AS TABLE (
    WITH m AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY source, season, code, match_id
                ORDER BY as_of DESC) rn
            FROM fact_player_match_stats WHERE as_of <= p_as_of) WHERE rn = 1
    )
    SELECT m.source, m.season, m.code, m.match_id, m.gw, m.tournament,
           m.minutes_played, m.goals, m.assists,
           m.total_shots, m.shots_on_target, m.xg, m.xa, m.xgot,
           m.chances_created, m.touches_opposition_box,
           m.tackles, m.interceptions, m.recoveries, m.blocks, m.clearances,
           m.defensive_contributions,
           m.saves, m.goals_conceded, m.goals_prevented
    FROM m
);

-- ---------------------------------------------------------------------------
-- sem_manager_picks(p_as_of): what each tracked manager's squad was, one row
-- per (season, gw, entry_id, element). Picks are stamped as_of = the gameweek
-- deadline at ingest, so at a deadline instant you see exactly the squads that
-- had just locked and nothing later. element_id is resolved to the stable
-- player `code` through dim_player AS KNOWN AT p_as_of; a pick whose element
-- has no identity row yet keeps a NULL code rather than being dropped, so a
-- resolution gap is visible instead of silently shrinking squads.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO sem_manager_picks(p_as_of) AS TABLE (
    WITH mp AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY entry_id, season, gw, element_id
                ORDER BY as_of DESC) rn
            FROM fact_manager_pick WHERE as_of <= p_as_of) WHERE rn = 1
    ), m AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY entry_id ORDER BY as_of DESC) rn
            FROM dim_manager WHERE as_of <= p_as_of) WHERE rn = 1
    ), g AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY entry_id, season, gw ORDER BY as_of DESC) rn
            FROM fact_manager_gw WHERE as_of <= p_as_of) WHERE rn = 1
    ), dp AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY season, element_id ORDER BY as_of DESC) rn
            FROM dim_player WHERE as_of <= p_as_of) WHERE rn = 1
    )
    SELECT mp.season, mp.gw, mp.entry_id,
           m.player_name AS manager_name, m.entry_name AS team_name, m.source,
           g.overall_rank, g.points AS gw_points,
           dp.code, dp.web_name,
           mp.element_id, mp.slot, mp.multiplier,
           mp.is_captain, mp.is_vice_captain
    FROM mp
    LEFT JOIN m  ON m.entry_id = mp.entry_id
    LEFT JOIN g  ON g.entry_id = mp.entry_id
             AND g.season = mp.season AND g.gw = mp.gw
    LEFT JOIN dp ON dp.season = mp.season AND dp.element_id = mp.element_id
);

-- ---------------------------------------------------------------------------
-- sem_manager_transfers(p_as_of): every tracked manager's transfers, named in
-- both directions, one row per (season, gw, entry_id, element_in,
-- element_out). as_of on the stored row is the deadline of the gameweek the
-- transfer applied to (when it became public); time_utc is the private
-- click-instant, kept because transfer timing is behaviour worth measuring.
-- Costs arrive in FPL tenths and are exposed in millions like sem_players.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO sem_manager_transfers(p_as_of) AS TABLE (
    WITH tr AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY entry_id, season, gw, element_in, element_out
                ORDER BY as_of DESC) rn
            FROM fact_manager_transfer WHERE as_of <= p_as_of) WHERE rn = 1
    ), m AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY entry_id ORDER BY as_of DESC) rn
            FROM dim_manager WHERE as_of <= p_as_of) WHERE rn = 1
    ), dp AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY season, element_id ORDER BY as_of DESC) rn
            FROM dim_player WHERE as_of <= p_as_of) WHERE rn = 1
    )
    SELECT tr.season, tr.gw, tr.entry_id,
           m.player_name AS manager_name, m.entry_name AS team_name, m.source,
           pin.code  AS code_in,  pin.web_name  AS player_in,
           pout.code AS code_out, pout.web_name AS player_out,
           tr.element_in, tr.element_out,
           tr.element_in_cost  / 10.0 AS price_in,
           tr.element_out_cost / 10.0 AS price_out,
           tr.time_utc
    FROM tr
    LEFT JOIN m ON m.entry_id = tr.entry_id
    LEFT JOIN dp pin  ON pin.season  = tr.season AND pin.element_id  = tr.element_in
    LEFT JOIN dp pout ON pout.season = tr.season AND pout.element_id = tr.element_out
);

-- ---------------------------------------------------------------------------
-- sem_manager_cohort(p_as_of): EXACTLY ONE row per tracked entry, carrying the
-- cohort it belongs to. This macro is the single definition of cohort
-- membership in the warehouse; sem_elite_ownership consumes it, and
-- fpl_edge/models/field/observed.py implements the identical rule in Python
-- (pinned equal by tests/unit/test_field_eo_agreement.py).
--
-- Cohort is DERIVED today: the crawl records why an entry is in the pool as a
-- free-text `dim_manager.source`, and the classification reads its prefix.
-- That is fragile, and this macro exists so that the day ingest writes a real
-- `dim_manager.cohort` column, ONE expression changes.
--
-- THE RULE, mutually exclusive by construction (one CASE over one GROUP BY):
--   1. 'top1k' -- the entry has at least one dim_manager row at or before
--                 p_as_of whose source begins 'top1k'. Rank-sampled
--                 membership is an objective, reproducible fact about the
--                 entry (it stood at overall rank r at a stated season/gw),
--                 so it OUTRANKS curation.
--   2. 'elite' -- otherwise: the curated crawl pool (expert, elite_list,
--                 winner, mini_league, snowball, elite_named).
-- An entry with picks but NO dim_manager row at p_as_of has no row here; it is
-- labelled 'unclassified' by sem_elite_ownership rather than dropped.
--
-- Why top1k wins the tie. The top1k crawl samples a DEFINED population -- the
-- top N of the overall table -- and its shares are read as an estimate of what
-- that population does. Dropping an entry from it because a curator also
-- listed them removes precisely the strongest managers and biases the
-- estimate. The elite pool makes no sampling claim; it is a roster, so losing
-- an overlapping member costs it only sample size, which is visible in
-- n_managers. Before this rule an entry found by both crawls was counted in
-- BOTH denominators and inflated both. How many such entries exist is a
-- property of the crawl on the day, not of this rule, so no count is quoted
-- here: it changes every time the top1k sampler runs. To read today's::
--
--     SELECT count(*) FROM sem_manager_cohort(now())
--     WHERE cohort = 'top1k' AND n_sources > n_top1k_sources;
--
-- Membership is "ANY source row at or before p_as_of", not just the newest, so
-- it never flip-flops with as_of; with the precedence above it is also
-- monotone -- once top1k, top1k at every later instant.
--
-- "AT OR BEFORE" IS INCLUSIVE, and the `<=` below is load-bearing: a crawl
-- stamped at exactly the decision instant is part of that decision, and
-- Snapshot.table reads the Python side with the same `as_of <= ?`. Turning it
-- into `<` moves an entry between cohorts in SQL only -- which is silent SQL
-- vs Python drift, not a rounding difference. Pinned on both sides of the
-- boundary by test_a_manager_row_landing_exactly_on_the_instant_is_visible in
-- tests/unit/test_field_eo_agreement.py.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO sem_manager_cohort(p_as_of) AS TABLE (
    SELECT entry_id,
           CASE WHEN count(*) FILTER (WHERE source LIKE 'top1k%') > 0
                THEN 'top1k' ELSE 'elite' END AS cohort,
           count(DISTINCT source) FILTER (WHERE source LIKE 'top1k%')
               AS n_top1k_sources,
           count(DISTINCT source) AS n_sources,
           -- ORDER BY is not cosmetic. DuckDB parallelises the scan, so an
           -- unordered string_agg returns a different provenance string at
           -- threads=8 than at threads=1 for any entry with more than one
           -- source -- the same nondeterminism Snapshot.table fixes with its
           -- trailing ORDER BY (store/warehouse.py). A provenance field that
           -- will not compare equal to itself is worse than none.
           string_agg(DISTINCT source, '|' ORDER BY source) AS sources,
           min(as_of) AS first_seen
    FROM dim_manager WHERE as_of <= p_as_of
    GROUP BY entry_id
);

-- ---------------------------------------------------------------------------
-- sem_elite_ownership(p_as_of): what the tracked cohorts hold, one row per
-- (season, gw, cohort, code). Cohort comes from sem_manager_cohort above --
-- one entry, one cohort -- plus 'unclassified' for an entry that has picks but
-- no manager row visible at p_as_of (counted and visible, never dropped).
--
-- THE ONE EFFECTIVE-OWNERSHIP DEFINITION (the canonical form for this repo;
-- fpl_edge/models/field/cohorts.py and the ownership_eo panel both adopt it,
-- and tests/unit/test_field_eo_agreement.py pins all three equal):
--
--     ownership = sum over m of weight[m] holding p       / sum of weight[all]
--     eo        = sum over m of weight[m] * multiplier[m,p] / sum of weight[all]
--     captaincy = sum over m of weight[m] captaining p    / sum of weight[all]
--
-- with multiplier the FPL multiplier as the API returned it at the deadline:
-- 0 benched, 1 started, 2 captain, 3 triple captain -- and 1 for a benched
-- pick under Bench Boost, which is why the stored multiplier is used rather
-- than reconstructed from armband plus chip rates. Ownership and eo are
-- tracked SEPARATELY on purpose: a benched player counts toward ownership and
-- carries no scoring exposure.
--
-- WEIGHTS: the per-manager weight vector is not implemented yet, so every
-- weight is 1 and `sum of weight[all]` IS `n_managers`. A weighted variant is
-- a NEW macro (sem_cohort_ownership_weighted), never a silent change here.
--
-- The denominator is the managers in that cohort WITH A STORED SQUAD for that
-- (season, gw). eo_pct sums multipliers, so captaincy makes it exceed 100.
-- NOTE the one place this can differ from the Python loader: the loader drops
-- a squad that fails 15-slot validation (and reports how many), while this
-- macro counts any entry with a stored pick row. They agree exactly whenever
-- every stored squad is complete, which is the state of the live warehouse.
--
-- A pick whose element_id resolves to no code groups under a NULL code row --
-- counted, visible, and excludable by the consumer, never silently dropped.
-- That NULL group is also the one place an entry contributes more than one row
-- to a single (cohort, code), which is why owned_by's DISTINCT is not
-- decoration. Likewise coalesce(multiplier, 0): a missing multiplier is a hole
-- in the crawl, and this macro's answer to a hole is a visible zero -- sum()
-- without it returns NULL for a group where every row is a hole, and a NULL
-- propagates into every consumer's arithmetic as a silent absence.
--
-- EVERY column below is compared against the Python model, row by row, by
-- tests/unit/test_field_eo_agreement.py; a column added here without a twin
-- there fails that module on purpose.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO sem_elite_ownership(p_as_of) AS TABLE (
    WITH mp AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY entry_id, season, gw, element_id
                ORDER BY as_of DESC) rn
            FROM fact_manager_pick WHERE as_of <= p_as_of) WHERE rn = 1
    ), coh AS (
        SELECT entry_id, cohort FROM sem_manager_cohort(p_as_of)
    ), base AS (
        SELECT coalesce(c.cohort, 'unclassified') AS cohort, mp.*
        FROM mp LEFT JOIN coh c ON c.entry_id = mp.entry_id
    ), cohort_n AS (
        SELECT season, gw, cohort, count(DISTINCT entry_id) AS n_managers
        FROM base GROUP BY season, gw, cohort
    ), dp AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY season, element_id ORDER BY as_of DESC) rn
            FROM dim_player WHERE as_of <= p_as_of) WHERE rn = 1
    )
    SELECT b.season, b.gw, b.cohort, dp.code,
           any_value(dp.web_name) AS web_name,
           n.n_managers,
           count(DISTINCT b.entry_id) AS owned_by,
           count(DISTINCT CASE WHEN coalesce(b.multiplier, 0) >= 1
                               THEN b.entry_id END) AS started_by,
           count(DISTINCT CASE WHEN coalesce(b.multiplier, 0) = 0
                               THEN b.entry_id END) AS benched_by,
           count(DISTINCT CASE WHEN b.is_captain THEN b.entry_id END)
               AS captained_by,
           sum(coalesce(b.multiplier, 0)) AS eo_units,
           100.0 * count(DISTINCT b.entry_id) / n.n_managers AS own_pct,
           100.0 * count(DISTINCT CASE WHEN b.is_captain THEN b.entry_id END)
                 / n.n_managers AS captain_pct,
           100.0 * sum(coalesce(b.multiplier, 0)) / n.n_managers AS eo_pct
    FROM base b
    JOIN cohort_n n ON n.season = b.season AND n.gw = b.gw AND n.cohort = b.cohort
    LEFT JOIN dp ON dp.season = b.season AND dp.element_id = b.element_id
    GROUP BY b.season, b.gw, b.cohort, dp.code, n.n_managers
);
