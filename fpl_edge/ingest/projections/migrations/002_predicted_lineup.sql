-- Predicted lineups: a third party's opinion about WHO STARTS, which is the
-- xMins signal the platform copies rather than models.
--
-- Kept separate from fact_projection because the unit is different in kind:
-- fact_projection carries points (and a probability the provider derived
-- itself); this table carries a roster decision -- "this human is in the XI
-- this provider expects" -- and the strength of that claim. Squashing it into
-- p_appear would erase the distinction between "the model thinks 85%" and
-- "a journalist wrote his name on a team sheet", which downstream weighting
-- needs to keep separate because the two fail differently.
--
-- `predicted_start` is TRUE for the eleven names in the provider's XI and
-- FALSE for players the same page explicitly rules out or doubts (their
-- injury list). Players the page does not mention appear in no row at all:
-- absence of evidence is recorded as absence, never as FALSE.
--
-- `certainty` is the provider's own label for the whole team sheet:
--   'expected'  -- pre-team-news prediction (Rotowire "is-expected")
--   'confirmed' -- official XI, published ~60-75 min before kickoff
--                  (Rotowire "is-confirmed")
-- plus, for predicted_start = FALSE rows, the player-level flag:
--   'out'           -- provider lists the player as OUT
--   'questionable'  -- provider lists the player as QUES/doubtful
--
-- as_of is the fetch instant, same PIT rule as every other table here. A
-- lineup page fetched after kickoff is still stamped at the fetch, so a
-- backtest reading "as of the deadline" can never see it.
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
