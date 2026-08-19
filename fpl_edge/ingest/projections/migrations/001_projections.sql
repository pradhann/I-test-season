-- Third-party projections, external ownership, and the ensemble weights those
-- two earn.
--
-- This migration deliberately does NOT live in fpl_edge/store/schema.sql. That
-- file is the shared contract every other team reads; adding tables there makes
-- every team's DDL conflict. The runner in fpl_edge/ingest/projections/store.py
-- applies this file idempotently against the same DuckDB file, exactly as
-- fpl_edge/interfaces/registry.py does for the idea registry. One warehouse,
-- one connection, several owners of DDL.
--
-- Point-in-time discipline is the same as store/schema.sql: `as_of` is the
-- instant the fact became publicly observable. For a third-party projection
-- that instant is the FETCH TIME, not the gameweek deadline and not the moment
-- the provider computed it. We cannot observe when FPL Form ran its model; we
-- can only observe when we read it. Stamping anything earlier would claim
-- knowledge we did not have, and would leak a provider's post-deadline revision
-- backwards into a pre-deadline decision.

CREATE TABLE IF NOT EXISTS schema_migration (
    version     VARCHAR PRIMARY KEY,
    applied_utc TIMESTAMPTZ NOT NULL,
    sha256      VARCHAR NOT NULL
);

-- One row per (provider, player, target gameweek) projection.
--
-- `xp` is the number to ensemble: expected FPL points for that player in that
-- gameweek, already multiplied through by the provider's own probability that
-- the player appears. Providers that publish the two factors separately keep
-- them in `xp_if_appears` and `p_appear` so the decomposition survives; a
-- provider that publishes only a product leaves both NULL rather than having
-- one invented for it.
--
-- `code` is the cross-season-stable FPL player code, never element_id. Every
-- provider keys on element_id, which is reassigned each season; resolving to
-- `code` at ingestion is what makes these rows joinable to fact_player_fixture
-- next season instead of silently attributing a projection to a stranger.
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

-- External estimates of what the FIELD owns, which is not a projection of
-- points but is the other half of a rank-utility decision. Kept in its own
-- table because the unit is a fraction of managers, not points, and mixing the
-- two in one `value` column is how a units bug becomes invisible.
--
-- `metric` values in use:
--   'eo_predicted'  -- LiveFPL predicted effective ownership for the coming GW,
--                      i.e. ownership + captaincy share. CAN EXCEED 1.0.
--   'own_top10k'    -- share of top-10k managers owning the player, 0-1
--   'own_elite'     -- share of LiveFPL's elite cohort owning the player, 0-1
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

-- The weights the ensemble actually used, together with the measurement that
-- earned them. A weight with no `n_obs` and no `loss` is not a weight, it is an
-- opinion; storing the evidence next to the number is what stops one turning
-- into the other over a season.
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
