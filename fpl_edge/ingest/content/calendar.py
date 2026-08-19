"""Gameweek deadlines for every season the warehouse holds.

``dim_event`` carries deadlines for 2026-27 only -- the FPL API exposes the
current season's events and nothing else, and the historical archive that
backfilled 2022-23..2025-26 does not include them. That is a problem, because
without a historical deadline there is no way to ask the question this whole
package turns on: *was this claim published before the deadline it applies to?*
Without an answer, every backfilled claim is a potential leak.

The gap is closed with a verified rule rather than a guess.
``deadlines.offset_before_first_kickoff_minutes`` is 90, verified against the
FPL rules page and recorded in ``fpl_edge/rules/registry.yaml``. The first
kickoff of each historical gameweek is in ``fact_fixture.kickoff_utc``. So a
historical deadline is ``min(kickoff) - 90 minutes``, derived rather than
invented, and the derivation is read from the rule registry at runtime so it
tracks the registry if the rule is ever corrected.

Derived deadlines are flagged as such. They are exact when the fixture list is
exact, but a gameweek that was rescheduled after the fact would move its own
first kickoff, so a derived deadline can be *later* than the real one. That
error direction is the dangerous one -- it would admit a claim that was actually
too late -- so :func:`load_calendar` applies a conservative safety margin to
derived deadlines only, pulling them earlier. A borderline claim is dropped
rather than trusted.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from fpl_edge.ingest.content.claims import GameweekCalendar
from fpl_edge.rules.loader import rules
from fpl_edge.store import Warehouse

UTC = dt.UTC

#: Pulled off every DERIVED deadline. A gameweek's first fixture can be moved
#: after the fact, which would push a derived deadline later than the real one
#: and admit a claim that was in truth published too late. Two hours is longer
#: than any plausible rescheduling of a *first* kickoff within a gameweek and
#: costs only claims published in the final two hours before a historical
#: deadline. Not applied to authoritative deadlines from dim_event.
DERIVED_SAFETY_MARGIN = dt.timedelta(hours=2)


@dataclass(frozen=True, slots=True)
class CalendarReport:
    authoritative_gws: int
    derived_gws: int
    seasons: tuple[str, ...]

    def render(self) -> str:
        return (
            f"deadlines: {self.authoritative_gws} authoritative (dim_event), "
            f"{self.derived_gws} derived from first kickoff - 90min - "
            f"{int(DERIVED_SAFETY_MARGIN.total_seconds() // 60)}min margin; "
            f"seasons {', '.join(self.seasons)}"
        )


def load_calendar(warehouse: Warehouse) -> tuple[GameweekCalendar, CalendarReport]:
    offset_minutes = int(
        rules().rule("deadlines.offset_before_first_kickoff_minutes").require()
    )
    offset = dt.timedelta(minutes=offset_minutes)

    events = warehouse.sql(
        "SELECT season, gw, max(deadline_utc) AS deadline FROM dim_event GROUP BY 1, 2"
    )
    rows: list[tuple[str, int, dt.datetime]] = []
    have: set[tuple[str, int]] = set()
    for row in events.itertuples(index=False):
        stamp = pd.Timestamp(row.deadline)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(UTC)
        rows.append((str(row.season), int(row.gw), stamp.to_pydatetime().astimezone(UTC)))
        have.add((str(row.season), int(row.gw)))
    authoritative = len(rows)

    fixtures = warehouse.sql(
        "SELECT season, gw, min(kickoff_utc) AS first_kickoff FROM fact_fixture "
        "WHERE gw IS NOT NULL AND kickoff_utc IS NOT NULL GROUP BY 1, 2"
    )
    derived = 0
    for row in fixtures.itertuples(index=False):
        key = (str(row.season), int(row.gw))
        if key in have:
            continue
        stamp = pd.Timestamp(row.first_kickoff)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(UTC)
        deadline = stamp.to_pydatetime().astimezone(UTC) - offset - DERIVED_SAFETY_MARGIN
        rows.append((key[0], key[1], deadline))
        have.add(key)
        derived += 1

    seasons = tuple(sorted({s for s, _, _ in rows}))
    return GameweekCalendar(rows), CalendarReport(authoritative, derived, seasons)
