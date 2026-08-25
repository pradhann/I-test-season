-- The projection track record: per-(provider, gameweek) accuracy against the
-- OFFICIAL settled actuals in fact_player_fixture.
--
-- This table exists so that projection_weight can be EARNED. The weight
-- table's own contract ("a weight with no n_obs and no loss is not a weight,
-- it is an opinion") requires evidence, and evidence has to accumulate
-- somewhere gameweek by gameweek: refitting from raw tables every time would
-- re-derive the whole history on every run and make the fit's inputs
-- invisible to a post-mortem. One row here is one measurement, made once,
-- kept forever.
--
-- Grain: (provider, season, gw, scope, metric).
--   scope   'overall'   -- every player the provider projected, zero-filled
--                          actuals for projected players who did not feature
--           'pos:GKP' | 'pos:DEF' | 'pos:MID' | 'pos:FWD'
--           'p_appear'  -- appearance-probability calibration, where published
--   metric  'mae' | 'rmse' for xp scopes; 'brier' for p_appear
--   value   the provider's own score (lower is better, always)
--   baseline the SAME metric for the all-provider consensus mean on the SAME
--           observations -- the number the provider had to beat. NULL only
--           when no consensus existed (cannot happen while the provider
--           itself is in the consensus, kept nullable for honesty).
--
-- Point-in-time discipline, both directions:
--   * the projection scored is each provider's LAST fetch at or before the
--     gameweek's deadline (dim_event.deadline_utc) -- never a post-kickoff
--     revision (that is enforced in fpl_edge/eval/projection_scoring.py and
--     tested by breaking the filter);
--   * deadline_utc records which instant the projections were read as-of, so
--     the measurement is reproducible;
--   * as_of is the SCORING instant. A score cannot exist before the gameweek
--     settled, so a snapshot read at any deadline sees only fully-past
--     track record.
CREATE TABLE IF NOT EXISTS fact_projection_score (
    provider      VARCHAR NOT NULL,
    season        VARCHAR NOT NULL,
    gw            INTEGER NOT NULL,       -- the gameweek that was scored
    scope         VARCHAR NOT NULL,
    metric        VARCHAR NOT NULL,
    value         DOUBLE NOT NULL,
    baseline      DOUBLE,
    n_obs         INTEGER NOT NULL,       -- player observations behind value
    deadline_utc  TIMESTAMPTZ NOT NULL,   -- projections read as-of this instant
    as_of         TIMESTAMPTZ NOT NULL,   -- when the score was computed
    PRIMARY KEY (provider, season, gw, scope, metric, as_of)
);
