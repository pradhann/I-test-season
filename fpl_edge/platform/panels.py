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
        script="fixture_ticker",
        default_params={"horizon": 5},
        layout="grid",
        description="The next five gameweeks per club. Blanks and doubles are explicit.",
    ),
    Panel(
        id="prices",
        title="Price radar",
        script="price_radar",
        default_params={"limit": 20},
        layout="table",
        width="half",
        description="Observed transfer velocity between the two most recent ingests.",
    ),
    Panel(
        id="ideas",
        title="Idea registry",
        script="idea_registry",
        default_params={"limit": 50},
        layout="list",
        description="Your theses, the engine's verdict, and how they actually resolved.",
    ),
)


def describe_all() -> list[dict[str, Any]]:
    return [p.describe() for p in PANELS]


def panel(panel_id: str) -> Panel:
    for p in PANELS:
        if p.id == panel_id:
            return p
    raise KeyError(f"no panel {panel_id!r}; known: {[p.id for p in PANELS]}")
