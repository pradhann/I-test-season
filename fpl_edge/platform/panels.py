"""Panels: what the UI shows, and which script feeds each one.

A panel is a *presentation* decision (title, default params, how wide it sits)
pinned to exactly one panel script, which is the *data* decision. Keeping them
separate is what makes the Argus rule enforceable: the frontend renders panels,
panels name scripts, scripts are the only thing that touches the warehouse.
There is no third path, and a panel cannot widen its own data access by
changing how it renders.

Argus pins scripts by commit SHA because its scripts are edited by an agent
between deploys. Ours are ordinary modules in this repo, so the pin is the
repo checkout itself: ``provenance.repo_sha`` on every run says exactly which
code produced the numbers. Same guarantee, no second version store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fpl_edge.platform.registry import script


@dataclass(frozen=True)
class Panel:
    id: str
    title: str
    script: str
    description: str = ""
    default_params: dict[str, Any] = field(default_factory=dict)
    #: Rendering hint only. The server does not interpret it.
    layout: str = "table"
    width: str = "full"

    def describe(self) -> dict[str, Any]:
        spec = script(self.script)
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description or spec.description,
            "script": self.script,
            "default_params": self.default_params,
            "layout": self.layout,
            "width": self.width,
            "params_schema": spec.params_schema,
            "result_schema": spec.result_schema,
        }


PANELS: tuple[Panel, ...] = (
    Panel(
        id="squad",
        title="My squad",
        script="squad_overview",
        layout="pitch",
        width="half",
        description="Your 15, priced and flagged, with where the squad was read from.",
    ),
    Panel(
        id="projections",
        title="Projections",
        script="projection_table",
        default_params={"limit": 50, "sort": "xpts"},
        layout="table",
        description="Projected points and spread, joined to live price and ownership.",
    ),
    Panel(
        id="fixtures",
        title="Fixture ticker",
        script="fixture_board",
        default_params={"horizon": 6},
        layout="grid",
        description=(
            "Six gameweeks per club, split: how easy it is to score in, and "
            "how easy it is to keep clean. Blanks and doubles are explicit."
        ),
    ),
    Panel(
        id="prices",
        title="Price radar",
        script="price_radar",
        default_params={"limit": 20},
        # Not "table": the result is {risers, fallers, window}, and the table
        # renderer reads `rows` -- a key this script's schema FORBIDS. Pinned
        # to table, the panel rendered "No data." over real data forever.
        layout="movers",
        width="half",
        description="Observed transfer velocity between the two most recent ingests.",
    ),
    Panel(
        id="market",
        title="Market watch",
        script="market_watch",
        default_params={"limit": 20},
        layout="table",
        width="half",
        description="Bookmaker-derived clean-sheet probabilities (with "
                    "cross-method spread) and player xG shares.",
    ),
    Panel(
        id="planner",
        title="Transfer planner",
        script="planner_grid",
        default_params={"horizon": 5},
        layout="planner",
        description="Plan moves across the horizon; xPts and cost update live.",
    ),
    Panel(
        id="ownership",
        title="Template & EO",
        script="ownership_eo",
        default_params={"limit": 50},
        layout="table",
        description="What the field owns: template, differentials, effective ownership.",
    ),
    Panel(
        id="ideas",
        title="Idea registry",
        script="idea_registry",
        default_params={"limit": 50},
        layout="list",
        description="Your theses, the engine's verdict, and how they actually resolved.",
    ),
    Panel(
        id="fixture_detail",
        title="One fixture, expanded",
        script="fixture_detail",
        layout="list",
        description=(
            "One fixture: both models, the market with its age, form, team "
            "news, set pieces, predicted lineups and previous meetings."
        ),
    ),
    Panel(
        id="fixture_ticker",
        title="Fixture ticker (legacy blend)",
        script="fixture_ticker",
        layout="table",
        description=(
            "The single blended difficulty per fixture. Superseded by "
            "fixture_board's attack/defence split; kept because the fixtures "
            "view falls back to it when the split artefact is absent."
        ),
    ),
    Panel(
        id="creator_board",
        title="The deadline board",
        script="creator_board",
        default_params={"scope": "panel"},
        layout="table",
        description=(
            "What the tracked panel intends this gameweek and what they "
            "actually own, against your squad. Not a forecast: their measured "
            "record is below chance."
        ),
    ),
    Panel(
        id="creator_detail",
        title="One creator, expanded",
        script="creator_detail",
        layout="list",
        description=(
            "Every item from one creator with its claims, verbatim quotes and "
            "deep links to the moment each was said."
        ),
    ),
    Panel(
        id="player_chatter",
        title="What the panel says about a player",
        script="player_chatter",
        layout="list",
        description=(
            "One player: who on the panel owns him, who said what, and what "
            "has been measured about him. Mounted in the xPoints and Template "
            "drawers so the corpus is reachable wherever a player is in focus."
        ),
    ),
)


def describe_all() -> list[dict[str, Any]]:
    return [p.describe() for p in PANELS]


def panel(panel_id: str) -> Panel:
    for p in PANELS:
        if p.id == panel_id:
            return p
    raise KeyError(f"no panel {panel_id!r}; known: {[p.id for p in PANELS]}")
