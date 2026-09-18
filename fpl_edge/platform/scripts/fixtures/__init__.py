"""The fixtures data path: a horizon ticker, a per-fixture drilldown, and the
cached ratings artefact both of them read.

The governing idea
------------------
**Every fixture is two fixtures -- one for your attackers, one for your
defenders -- and nothing here ever averages them.** A single difficulty number
is the mean of the answers to two different questions, and a mean is not an
answer to either. Measured on this repo's own fitted model, attack-ease and
defence-ease over the 40 (club, venue) pairs share only about half their
variance, and individual fixtures invert completely: hosting a side that will
not score is a defender's fixture and an attacker's trap, and the blended number
calls it "average", which is the one thing it is not.

Two numbers, and they are not interchangeable
---------------------------------------------
Every cell carries **both** of these, under separate keys, never merged:

``opponent_only``
    Holds *your* club at league average and asks only what the OPPONENT does at
    that venue. Two different clubs facing the same opponent at the same venue
    get identical numbers -- on purpose. This is a *fixture* view, not a power
    ranking, and it is what the ticker colours by.

``fixture_specific``
    Your own club's fitted strength IS in this number. Arsenal at Hull and
    Coventry at Hull differ here. This is what a drilldown shows, and it is the
    honest prediction; it is not comparable across rows, which is exactly why it
    does not drive the colour.

The two are nested under distinct keys with distinct docstrings so that a
consumer cannot reach for one and get the other. Both are always served.

Polarity, once, everywhere
--------------------------
Every ``*_ease`` field is signed and **positive means better for you**:
``attack_ease`` positive means easier to score, ``defence_ease`` positive means
easier to keep a clean sheet. The defence axis is therefore a *flip* of the
opponent's goal rate, not the rate itself. One polarity for both axes means one
diverging colour scale with a real zero -- a league-average fixture -- rather
than two scales the reader has to hold in their head.

Why a cached artefact, and what is in it
----------------------------------------
A Dixon-Coles fit is a model run with its own refresh cycle, so it belongs in a
nightly job, not in a panel with a 10s budget. The build job also writes
``fixture_difficulty.parquet``, but that file stores only the *blended*
number -- ``lam_O - mu_O``, min-max normalised -- and the subtraction is
irreversible: 740 rows carry exactly 40 distinct values, and no arithmetic
recovers the two halves from the one scalar. (I checked whether the split can be
algebraically recovered from the 40 blended values: it cannot. Solving for
(attack, defence) per club given the home-advantage multiplier is exact and
linear, but the three global unknowns -- the min-max scale, its offset, and the
multiplier -- are constrained by only one identity, so the system is short by
two. The split has to be *stored*, not reconstructed.)

So this module owns a second artefact, ``fixture_ratings.parquet``: one row per
club carrying the fitted attack and defence parameters plus the fit's scalars
(intercept, home advantage, rho, league means). From those, every quantity on
this page -- both lenses, every score matrix, every clean-sheet probability --
is pure arithmetic in the panel, microseconds not minutes. Build it with::

    python -m fpl_edge.platform.scripts.fixtures --build

which is what the post-gameweek job and the T-30h presser refresh both call.
One run of it fits once and writes all three artefacts. The panel NEVER builds
them: when the artefact is missing the board serves the
schedule and every difficulty field is null with a reason naming this command.

Freshness is a first-class field, not a log line
------------------------------------------------
``provenance.generated_at`` is when the panel ran, which is always "seconds
ago", and a panel that reads a four-day-old odds table and stamps itself fresh
is lying by omission. Every result here therefore carries ``inputs[]``: one row
per source with its ``as_of``, its age in hours, its row count, a state, the
threshold that decides that state, and -- the part that matters -- what
staleness *does to the number*. Thresholds are per input because staleness means
different things per source: a fit only moves when matches finish, so a week is
fine; odds move on team news, so twelve hours is not.

The empty contract
------------------
No value is ever imputed. A missing input produces ``null`` **and** a sibling
string saying why, in the words a reader can act on. Whole-panel emptiness uses
the one sanctioned ``{empty, reason}`` shape, which the root schemas here keep
disjoint via ``additionalProperties: false``.
"""

from __future__ import annotations

# Importing board and detail is what registers the two panel scripts, exactly
# as importing the single fixtures.py module used to.
from fpl_edge.platform.scripts.fixtures.board import (
    BOARD_PARAMS,
    BOARD_RESULT,
    MARKET_CELL_SCHEMA,
    fixture_board,
)
from fpl_edge.platform.scripts.fixtures.build import main, write_artefacts
from fpl_edge.platform.scripts.fixtures.constants import (
    ATTACKER_SHARE,
    CALIBRATION_NAME,
    DIFFICULTY_NAME,
    DIVERGENCE_RANKS,
    FORM_MIN_MATCHES,
    FORM_WINDOW,
    FPL_GOAL_POINTS,
    INPUT_SCHEMA,
    INTEL_STALE_HOURS,
    LINEUP_STALE_HOURS,
    ODDS_STALE_HOURS,
    ODDS_USELESS_HOURS,
    RATINGS_NAME,
    RATINGS_STALE_HOURS,
    SCALE_DOMAIN,
    SEASON_DEFAULT,
)
from fpl_edge.platform.scripts.fixtures.detail import (
    DETAIL_PARAMS,
    DETAIL_RESULT,
    DISAGREE_PP,
    fixture_detail,
)
from fpl_edge.platform.scripts.fixtures.ratings import (
    BUILD_HINT,
    CALIBRATION_COLUMNS,
    DIFFICULTY_COLUMNS,
    RATINGS_COLUMNS,
    build_board_ratings,
    build_calibration,
    build_fixture_difficulty,
    fit_once,
    load_legacy_difficulty,
    load_ratings,
    model_calibration,
    opponent_difficulty,
)

#: Every public name fixtures.py exported. Private helpers are deliberately
#: NOT re-exported: a test that monkeypatched one here would patch a copy the
#: owning module never reads.
__all__ = [
    "ATTACKER_SHARE",
    "BOARD_PARAMS",
    "BOARD_RESULT",
    "BUILD_HINT",
    "CALIBRATION_COLUMNS",
    "CALIBRATION_NAME",
    "DETAIL_PARAMS",
    "DETAIL_RESULT",
    "DIFFICULTY_COLUMNS",
    "DIFFICULTY_NAME",
    "DISAGREE_PP",
    "DIVERGENCE_RANKS",
    "FORM_MIN_MATCHES",
    "FORM_WINDOW",
    "FPL_GOAL_POINTS",
    "INPUT_SCHEMA",
    "INTEL_STALE_HOURS",
    "LINEUP_STALE_HOURS",
    "MARKET_CELL_SCHEMA",
    "ODDS_STALE_HOURS",
    "ODDS_USELESS_HOURS",
    "RATINGS_COLUMNS",
    "RATINGS_NAME",
    "RATINGS_STALE_HOURS",
    "SCALE_DOMAIN",
    "SEASON_DEFAULT",
    "build_board_ratings",
    "build_calibration",
    "build_fixture_difficulty",
    "fit_once",
    "fixture_board",
    "fixture_detail",
    "load_legacy_difficulty",
    "load_ratings",
    "main",
    "model_calibration",
    "opponent_difficulty",
    "write_artefacts",
]
