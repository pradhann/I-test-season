"""The theses section of the weekly report, via the interfaces registry hook.

Registered through :func:`fpl_edge.interfaces.report.register_section`, which is
the sanctioned way for another team's material to appear in ``fpl weekly``
without editing the report module.
"""

from __future__ import annotations

import datetime as dt

from fpl_edge.interfaces.report import register_section
from fpl_edge.store import Warehouse
from fpl_edge.theses.scoreboard import compute
from fpl_edge.theses.store import ThesesStore


def _theses_section(
    wh: Warehouse, season: str, gw: int, as_of: dt.datetime
) -> str | None:
    store = ThesesStore()
    open_rows = [
        t for t, _ in store.load_open()
        if t.season == season and t.gw_start <= gw <= t.gw_end
    ]
    resolved = [t for t, _ in store.load_resolved()]

    if not open_rows and not resolved:
        return None

    lines = [f"## Theses ({len(open_rows)} open covering GW{gw})", ""]
    for t in open_rows:
        flag = " [ACTED]" if t.acted else ""
        lines.append(
            f"- {t.id} [{t.scoreboard_key}]: "
            f"{t.falsifiable_prediction or '(watch)'}{flag} — settles {t.window_label}"
        )
    if resolved:
        lines.append("")
        for r in compute(resolved):
            if r.entity_type != "source":
                continue
            hit = "unscored so far" if r.hit_rate is None else \
                f"{r.correct}/{r.sample} correct ({r.hit_rate:.0%})"
            lines.append(f"- record — {r.entity}: {hit}")
    return "\n".join(lines)


def register() -> None:
    register_section("theses", _theses_section, priority=25, provides="theses")
