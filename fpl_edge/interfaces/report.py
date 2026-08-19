"""The weekly decision report, and the hook other teams plug into.

The report is assembled from registered sections rather than written as one
function, because the parts that matter most -- the squad, the transfer plan, the
chip call -- belong to the optimiser and simulation teams and do not exist yet.
A monolithic renderer would either have to import modules that are not written,
or hardcode placeholders that quietly become lies once the real thing lands.

So: :func:`register_section` takes a callable, sections render in priority order,
and the report states plainly which parts are missing. The interfaces team owns
the deadline, the idea log and the tracking summary. Everything else arrives by
registration, from that team's own package, with no edit to this file.

That the report says "no squad optimiser is registered" is a feature. A weekly
report that looks complete while silently omitting the transfer recommendation is
worse than one that admits the gap.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Callable

from fpl_edge.interfaces.bias import review as run_review
from fpl_edge.interfaces.ideas import IdeaStatus
from fpl_edge.interfaces.registry import IdeaRegistry
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc

#: A section renders itself from (warehouse, season, gw, as_of) and returns
#: markdown-ish plain text, or None to omit itself entirely this week.
SectionFn = Callable[[Warehouse, str, int, dt.datetime], "str | None"]


@dataclass(frozen=True, slots=True)
class Section:
    name: str
    render: SectionFn
    priority: int = 100
    #: Named so the report can list what is missing when nobody registers it.
    provides: str = ""


_SECTIONS: list[Section] = []

#: What a complete report is supposed to contain. Anything here without a
#: registered section is reported as an explicit gap rather than omitted.
EXPECTED = {
    "deadline": "the deadline and gameweek status",
    "ideas": "your open ideas for this gameweek",
    "tracking": "how last week's ideas resolved",
    "squad": "the recommended XI, captain and bench order",
    "transfers": "the transfer recommendation and whether to take a hit",
    "chips": "the chip call",
    "risk": "rank-utility and the differential/template trade-off",
}


def register_section(
    name: str, render: SectionFn, *, priority: int = 100, provides: str = ""
) -> None:
    """Add a section to the weekly report. Re-registering a name replaces it."""
    global _SECTIONS
    _SECTIONS = [s for s in _SECTIONS if s.name != name]
    _SECTIONS.append(Section(name=name, render=render, priority=priority, provides=provides or name))
    _SECTIONS.sort(key=lambda s: (s.priority, s.name))


def registered() -> tuple[str, ...]:
    return tuple(s.provides for s in _SECTIONS)


def missing() -> tuple[str, ...]:
    have = set(registered())
    return tuple(k for k in EXPECTED if k not in have)


# -- the sections this team owns --------------------------------------------


def _deadline_section(wh: Warehouse, season: str, gw: int, as_of: dt.datetime) -> str | None:
    snap = wh.snapshot_at(as_of)
    try:
        deadline = snap.deadline(season, gw)
    except KeyError:
        return f"## GW{gw}\n\nNo deadline known for {season} GW{gw} at {as_of:%Y-%m-%d %H:%M}Z."
    remaining = deadline - as_of
    hours = remaining.total_seconds() / 3600.0
    when = "PASSED" if hours < 0 else f"{hours:.1f}h away"
    return (
        f"## {season} GW{gw}\n\n"
        f"Deadline {deadline:%Y-%m-%d %H:%M}Z ({when}).\n"
        f"Report generated at {as_of:%Y-%m-%d %H:%M}Z from a snapshot at that instant."
    )


def _ideas_section(wh: Warehouse, season: str, gw: int, as_of: dt.datetime) -> str | None:
    reg = IdeaRegistry(wh)
    open_ideas = [
        i for i in reg.ideas(season=season, status=IdeaStatus.OPEN)
        if int(i.gw) <= gw <= i.last_gw
    ]
    if not open_ideas:
        return "## Your ideas\n\nNo open ideas covering this gameweek."
    lines = [f"## Your ideas ({len(open_ideas)} covering GW{gw})", ""]
    for idea in open_ideas:
        v = reg.latest_verdict(idea.idea_id)
        flag = " [ACTED]" if idea.acted else ""
        lines.append(f"- {idea.thesis}{flag}")
        lines.append(f"  said {idea.created_utc:%d %b %H:%M}Z: \"{idea.raw_text}\"")
        if v is not None:
            lines.append(
                f"  engine: {v.stance} at P={v.p_thesis_true:.0%} "
                f"[{v.provider} {v.provider_version}, {v.confidence} confidence]"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def _tracking_section(wh: Warehouse, season: str, gw: int, as_of: dt.datetime) -> str | None:
    rev = run_review(wh, season=season)
    b = rev.scoreboard
    if b.n_total == 0:
        return None
    lines = ["## Your record", ""]
    lines.append(f"{b.n_total} ideas: {b.n_resolved} resolved, {b.n_open} open, {b.n_void} void.")
    if b.hit_rate is not None:
        lines.append(f"Hit rate {b.hit_rate:.0%}; mean margin {b.mean_margin:+.1f} points.")
    if b.acted_hit_rate is not None and b.unacted_hit_rate is not None:
        lines.append(
            f"Acted on {b.acted_n} at {b.acted_hit_rate:.0%}; "
            f"skipped {b.unacted_n} at {b.unacted_hit_rate:.0%}."
        )
    sig = [f for f in rev.findings if f.significant]
    if sig:
        lines.append("")
        lines.append("Biases with evidence behind them:")
        for f in sig:
            lines.append(f"- {f.name}: {f.verdict()}")
    return "\n".join(lines)


register_section("deadline", _deadline_section, priority=10, provides="deadline")
register_section("ideas", _ideas_section, priority=20, provides="ideas")
register_section("tracking", _tracking_section, priority=30, provides="tracking")


@dataclass(frozen=True, slots=True)
class WeeklyReport:
    season: str
    gw: int
    as_of: dt.datetime
    body: str
    gaps: tuple[str, ...] = field(default_factory=tuple)

    def render(self) -> str:
        out = [f"# FPL decision report — {self.season} GW{self.gw}", "", self.body]
        if self.gaps:
            out += [
                "",
                "## Not in this report yet",
                "",
                "These sections have no registered provider, so the report does not "
                "cover them. It is not that there is nothing to say -- it is that "
                "nothing has been built to say it, and inventing a recommendation "
                "here would be worse than the gap.",
                "",
            ]
            out += [f"- **{k}** — {EXPECTED[k]}" for k in self.gaps]
        return "\n".join(out)


def weekly_report(
    warehouse: Warehouse,
    *,
    season: str = "2026-27",
    gw: int | None = None,
    as_of: dt.datetime | None = None,
) -> WeeklyReport:
    """Assemble the report for a gameweek from whatever is registered."""
    when = (as_of or dt.datetime.now(UTC)).astimezone(UTC)
    snap = warehouse.snapshot_at(when)
    if gw is None:
        try:
            gw = int(snap.next_gw(season))
        except KeyError:
            gw = 1

    chunks: list[str] = []
    for section in _SECTIONS:
        try:
            piece = section.render(warehouse, season, gw, when)
        except Exception as exc:  # noqa: BLE001 - one broken section must not kill the report
            piece = f"## {section.name}\n\nSection failed: {type(exc).__name__}: {exc}"
        if piece:
            chunks.append(piece)

    return WeeklyReport(
        season=season, gw=gw, as_of=when,
        body="\n\n".join(chunks), gaps=missing(),
    )
