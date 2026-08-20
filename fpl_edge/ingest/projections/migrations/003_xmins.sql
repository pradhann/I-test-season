-- The normalised projection contract: (source, player_code, gw, xmins, xpts,
-- fetched_at).
--
-- 001 created fact_projection with points only. Expected MINUTES is the other
-- half of what this platform copies rather than models, and some free sources
-- publish it directly (fplbench's `pred_minutes`, blueladd's `xmins`) rather
-- than as a probability of appearing. Those are different quantities and must
-- not be squashed into one column:
--
--   p_appear  -- P(the player gets ANY minutes), 0..1
--   xmins     -- expected minutes, 0..90ish
--
-- A source publishing 78.4 xmins is saying something a 0.92 p_appear cannot
-- say (it distinguishes a 90-minute lock from a 60-minute starter who gets
-- hooked), and a source publishing 0.92 p_appear is saying something 78.4
-- xmins cannot say (it is a probability, not an expectation). Deriving either
-- from the other requires a minutes model, and building one is exactly what
-- this platform refuses to do. So both columns exist and both stay NULL for
-- sources that do not publish them.
ALTER TABLE fact_projection ADD COLUMN IF NOT EXISTS xmins DOUBLE;

-- The one normalised shape every downstream consumer reads, in the exact
-- vocabulary of the platform contract. `season` rides along because `gw` alone
-- is not a key across seasons -- there is a GW1 every August -- but the four
-- contract columns are named exactly as specified so a consumer never has to
-- know that this repo internally calls a projection's fetch instant `as_of`.
--
-- This is a VIEW, not a table: there is one copy of the data, and no job can
-- write a normalised row that disagrees with the row it was normalised from.
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
