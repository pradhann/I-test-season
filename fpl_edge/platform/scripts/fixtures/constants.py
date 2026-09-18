"""Artefact names, staleness thresholds and the input-provenance row.

Shared by both panels and the build job, so it sits below all three and imports
nothing else in the package."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# artefacts and thresholds
# ---------------------------------------------------------------------------

#: Written by build.py from the same fit as the split below. The legacy blended
#: number; kept for back-compatibility and always labelled deprecated. It has
#: exactly one reader, fixture_board's per-cell ``legacy_difficulty``. The MCP
#: tool that was the second one now reads the split artefact through the board
#: itself, so nothing else asks for this file by name.
DIFFICULTY_NAME = "fixture_difficulty.parquet"

#: Written by ``--build`` below: the fitted split, one row per club.
RATINGS_NAME = "fixture_ratings.parquet"

#: Written by ``--build`` below: the empirical calibration regression.
CALIBRATION_NAME = "fixture_calibration.parquet"

#: A fit only moves when matches finish, so within a normal week it cannot be
#: more than one round out of date. Beyond it, the refresh job has stopped.
RATINGS_STALE_HOURS = 7 * 24.0

#: Odds move on team news, so a price older than this predates the press
#: conferences that decide the fixture. Different decay law, different cutoff.
ODDS_STALE_HOURS = 12.0
ODDS_USELESS_HOURS = 72.0

#: Predicted XIs publish around T-48h; older than a day and the XI is a guess
#: about a team sheet that has since moved.
LINEUP_STALE_HOURS = 36.0
INTEL_STALE_HOURS = 7 * 24.0

#: The colour domain, in goals per match either side of a league-average
#: fixture. Two population standard deviations, so the best and worst four
#: clubs read as off-scale and everybody else uses the full ramp instead of
#: being crushed into the middle by two outliers.
SCALE_DOMAIN = 0.60

#: A reference attacker's share of their club's goal involvement, used only to
#: convert expected goals into the FPL points the calibration block prints.
#: 4 is the midfielder goal value and the clean-sheet value.
ATTACKER_SHARE = 0.30
FPL_GOAL_POINTS = 4.0

#: Rank gap at which the two lenses are called out as disagreeing. Ranks run
#: over the 2N (opponent, venue) population, so 10 places is a quarter of it.
DIVERGENCE_RANKS = 10

#: Form window. Below this many completed matches the residual is noise and
#: says so rather than drawing a confident sparkline over one point.
FORM_WINDOW = 6
FORM_MIN_MATCHES = 3

SEASON_DEFAULT = "2026-27"


# ---------------------------------------------------------------------------
# freshness
# ---------------------------------------------------------------------------


def _hours_since(when: Any, now: dt.datetime) -> float | None:
    """Age in hours, rounded to two decimals because it is rendered.

    Clamped at zero: a negative age means the stamp is in the future relative to
    the requested instant, which is a leak rather than a fresh input, and it is
    reported as such in `notes` rather than as a negative number in a chip.
    """
    if when is None:
        return None
    ts = pd.to_datetime(when, utc=True, errors="coerce")
    if ts is pd.NaT or pd.isna(ts):
        return None
    return round(max(0.0, (now - ts.to_pydatetime()).total_seconds() / 3600.0), 2)


def _iso(when: Any) -> str | None:
    if when is None:
        return None
    ts = pd.to_datetime(when, utc=True, errors="coerce")
    if ts is pd.NaT or pd.isna(ts):
        return None
    return ts.isoformat()


def _input_row(
    name: str,
    *,
    source: str,
    as_of: Any,
    now: dt.datetime,
    stale_after_hours: float | None,
    rows: int | None,
    effect_when_stale: str,
    detail: str,
    missing: bool = False,
) -> dict[str, Any]:
    """One freshness row. ``state`` is derived, never asserted by the caller.

    Four states, and the distinction between the last two is the one that
    matters: ``stale`` means the data is old, ``missing`` means it was never
    there. A red dot that cannot tell those apart is not worth drawing.
    """
    age = _hours_since(as_of, now)
    if missing or as_of is None or not rows:
        state = "missing"
    elif stale_after_hours is not None and age is not None and age > stale_after_hours:
        state = "stale"
    else:
        state = "fresh"
    return {
        "name": name,
        "source": source,
        "as_of": _iso(as_of),
        "age_hours": None if age is None else round(age, 2),
        "rows": None if rows is None else int(rows),
        "state": state,
        "stale_after_hours": stale_after_hours,
        "effect_when_stale": effect_when_stale,
        "detail": detail,
    }


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "source", "state", "effect_when_stale", "detail"],
    "properties": {
        "name": {"type": "string"},
        "source": {"type": "string"},
        "as_of": {"type": ["string", "null"]},
        "age_hours": {"type": ["number", "null"]},
        "rows": {"type": ["integer", "null"]},
        "state": {"enum": ["fresh", "stale", "missing", "failed"]},
        "stale_after_hours": {"type": ["number", "null"]},
        "effect_when_stale": {"type": "string"},
        "detail": {"type": "string"},
    },
}
