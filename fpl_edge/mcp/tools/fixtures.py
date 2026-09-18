"""``fixtures``, ``fixture`` and ``club_form``: the schedule, both lenses.

``fixture_board`` serves two different numbers per cell and this server does
not collapse them. The colour asks only what the opponent does, so two clubs
facing the same opponent get the same number and rows are comparable. The
fixture-specific number, which is a different number, is served beside it.

The deprecated blended difficulty parquet is not read here. The old MCP tool
read it while the Fixtures page read the split ratings, so a number Claude
quoted and a number the page drew could differ; one panel ends that.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.mcp.adapter import panel_call
from fpl_edge.mcp.server import mcp

SEASON_DEFAULT = "2026-27"


@mcp.tool()
def fixtures(
    season: str = SEASON_DEFAULT,
    horizon: int = 6,
    from_gw: int | None = None,
    as_of: str | None = None,
    divergence_ranks: int = 10,
) -> dict[str, Any]:
    """Every club's next fixtures, rated on both lenses, with blanks and doubles.

    The same grid the Fixtures page draws. Two ratings per cell and they mean
    different things: how easy the fixture is to score in, and how easy it is
    to keep a clean sheet in. A club with a poor attack and a tight defence is
    a kind defensive fixture and a hard afternoon for your forwards, and the
    payload says both rather than averaging them into one colour.

    Blanks and doubles are explicit rows, not missing ones.

    Args:
        season: FPL season, for example "2026-27".
        horizon: How many gameweeks ahead to show, 1 to 12.
        from_gw: Start gameweek. Omit for the next unplayed one.
        as_of: ISO-8601 instant carrying a timezone. Every warehouse read is
            point in time to it. Omit for now.
        divergence_ranks: How many clubs to list where the two lenses disagree
            most.

    Returns:
        The envelope: result, provenance, budget, and gap when the ratings
        artefact is missing. If gap is present, quote its reason.
    """
    return panel_call("fixtures", "fixture_board", {
        "season": season, "horizon": horizon, "from_gw": from_gw,
        "as_of": as_of, "include_form": True, "include_calibration": True,
        "divergence_ranks": divergence_ranks,
    })


@mcp.tool()
def fixture(
    fixture_id: int,
    season: str = SEASON_DEFAULT,
    as_of: str | None = None,
    meetings_limit: int = 8,
) -> dict[str, Any]:
    """One fixture, expanded: both models, the market, form, team news, lineups.

    Everything the Fixtures drawer shows for a single match: this engine's own
    goal model and the ratings model side by side, the bookmakers' prices with
    their age, both clubs' recent form, the stored team news, set-piece
    duties, predicted lineups and the previous meetings.

    The market age matters and is in the payload: a price fetched yesterday is
    not the price now, and the panel says which it is.

    Args:
        fixture_id: The fixture id, as the fixtures grid carries it.
        season: FPL season, for example "2026-27".
        as_of: ISO-8601 instant carrying a timezone. Omit for now.
        meetings_limit: How many previous meetings between the two clubs.

    Returns:
        The envelope: result, provenance, budget, and gap when the fixture is
        not in the warehouse. If gap is present, quote its reason.
    """
    return panel_call("fixture", "fixture_detail", {
        "season": season, "fixture_id": fixture_id, "as_of": as_of,
        "meetings_limit": meetings_limit,
    })


@mcp.tool()
def club_form(
    season: str = SEASON_DEFAULT,
    horizon: int = 6,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Every club's recent results and scoring rates, from the fixtures board.

    The form block the fixtures grid carries: goals for and against, recent
    results and the rates the ratings were fitted on. Use it for "how has this
    club been playing" rather than for one match, which is what fixture is for.

    Args:
        season: FPL season, for example "2026-27".
        horizon: How many gameweeks ahead the accompanying grid covers.
        as_of: ISO-8601 instant carrying a timezone. Omit for now.

    Returns:
        The envelope: result, provenance, budget, and gap when the form block
        has nothing. If gap is present, quote its reason.
    """
    return panel_call("club_form", "fixture_board", {
        "season": season, "horizon": horizon, "as_of": as_of,
        "include_form": True, "include_calibration": False,
    })
