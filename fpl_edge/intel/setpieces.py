"""Set-piece and penalty order, and the change that is worth knowing about.

Why this is worth a module of its own: first-choice penalty duty is worth roughly
0.10 goals per game to the taker (Premier League sides win about 0.13 penalties
a match and takers convert about 0.79 of them). Over a 38-game season that is
close to four goals -- more than the entire gap between an £8m midfielder and a
£5m one. And unlike form, it is a *discrete, observable* fact that changes on a
known day, usually when a manager says so or when the previous taker misses one.

The engine's per-90 rates cannot see this. ``fpl_edge/models/points/shares.py``
says so in its own docstring: xG there includes penalties, so a player who loses
the duty keeps an inflated rate until the history rolls off, and the archive has
no penalties-taken column to separate them. This module is the correction: it
tracks the duty explicitly and alerts when it moves.

Detection
---------
Two observations of FPL's stated order, compared. The states that count as a
change are, in descending order of value:

* off the list -> 1        a new first-choice taker. The big one.
* n -> 1                   promotion to first choice.
* 1 -> off the list        loss of duty. Equally large, opposite sign.
* any other reordering     recorded, valued by the 1/2^(n-1) share model in
                           :mod:`fpl_edge.intel.items`.

A player absent from *both* observations produces nothing. A player absent from
the later one but present in the earlier one produces a real change with
``ord_after = None`` -- which is why :func:`duty_table` returns only what FPL
lists and this function reconstructs the absences, rather than the other way
round.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from fpl_edge.intel.bootstrap import (
    ARCHIVE_DIR,
    RAW_ROOT,
    ArchivedBootstrap,
    duty_table,
    read_archive,
)
from fpl_edge.intel.items import (
    Duty,
    DutyChange,
    IntelItem,
    IntelKind,
    SetPieceDuty,
    content_id,
    value_duty_change,
)

UTC = dt.timezone.utc

SOURCE = "fpl_api:bootstrap-static"

#: Below this, a reordering is not worth a notification. Set so that a swap
#: between third and fourth choice (0.0125 goals/game) stays quiet while a move
#: into or out of first choice (0.05 goals/game) always fires.
ALERT_GOALS_PER_GAME = 0.02

DutyMap = dict[tuple[int, Duty], tuple[int | None, str | None, int | None]]


@dataclass(frozen=True, slots=True)
class DutyScan:
    """The result of walking an archive: the current state, plus every change."""

    duties: list[SetPieceDuty]
    changes: list[DutyChange]
    items: list[IntelItem]
    polls: int
    first_poll: dt.datetime | None
    last_poll: dt.datetime | None

    @property
    def alerts(self) -> list[DutyChange]:
        """Changes big enough to be worth interrupting someone about."""
        return [
            c for c in self.changes
            if abs(c.delta_goals_per_game) >= ALERT_GOALS_PER_GAME
        ]

    def window_note(self) -> str:
        if self.first_poll is None or self.last_poll is None:
            return "no archived polls to compare"
        span = self.last_poll - self.first_poll
        return (
            f"{self.polls} archived polls spanning "
            f"{self.first_poll:%Y-%m-%d %H:%M}Z to {self.last_poll:%Y-%m-%d %H:%M}Z "
            f"({span.total_seconds() / 3600:.1f}h)"
        )


def _describe(code: int, name: str, duty: Duty, before: int | None, after: int | None) -> str:
    who = name or f"player {code}"
    if before is None and after == 1:
        return f"{who} is now FPL's FIRST-CHOICE {duty.label} taker (was not listed)"
    if before is None:
        return f"{who} joins the {duty.label} order at #{after}"
    if after is None:
        return f"{who} is NO LONGER listed for {duty.label} (was #{before})"
    if after == 1:
        return f"{who} is promoted to FIRST CHOICE for {duty.label} (was #{before})"
    direction = "promoted to" if after < before else "demoted to"
    return f"{who} {direction} #{after} for {duty.label} (was #{before})"


def compare(
    before: DutyMap,
    after: DutyMap,
    *,
    season: str,
    prior_as_of: dt.datetime,
    detected_at: dt.datetime,
    names: dict[int, str] | None = None,
) -> list[DutyChange]:
    """Every duty that moved between two observations.

    Pure, and deliberately so: the entire change-detection rule is testable
    against two hand-written dicts with no warehouse, no archive and no clock.
    """
    names = names or {}
    changes: list[DutyChange] = []
    for key in sorted(set(before) | set(after), key=lambda k: (k[0], str(k[1]))):
        code, duty = key
        ord_before = before.get(key, (None, None, None))[0]
        ord_after = after.get(key, (None, None, None))[0]
        if ord_before == ord_after:
            continue
        team_code = (after.get(key) or before.get(key) or (None, None, None))[2]
        delta = value_duty_change(duty, ord_before, ord_after)
        changes.append(
            DutyChange(
                change_id=content_id(
                    "spc", season, code, str(duty), ord_before, ord_after,
                    detected_at.astimezone(UTC).isoformat(),
                ),
                season=season,
                code=int(code),
                duty=duty,
                ord_before=ord_before,
                ord_after=ord_after,
                prior_as_of=prior_as_of,
                detected_at=detected_at,
                delta_goals_per_game=delta,
                headline=_describe(code, names.get(int(code), ""), duty, ord_before, ord_after),
                team_code=team_code,
            )
        )
    return changes


def _names(snap: ArchivedBootstrap) -> dict[int, str]:
    return {
        int(e["code"]): str(e.get("web_name") or "")
        for e in snap.elements
        if e.get("code") is not None
    }


def scan_archive(
    *,
    season: str,
    directory: Path = ARCHIVE_DIR,
    until: dt.datetime | None = None,
) -> DutyScan:
    """Walk every archived bootstrap, oldest first, recording state and changes.

    ``published_at`` for a duty is the poll at which the value was first seen,
    not the poll being processed. FPL states no change timestamp, so the earliest
    poll carrying a value is the tightest honest upper bound we have on when it
    became public -- and re-stamping it on every subsequent poll would make a
    stable order look like continuous breaking news.
    """
    prev: DutyMap | None = None
    prev_at: dt.datetime | None = None
    first_seen: dict[tuple[int, Duty], tuple[int | None, dt.datetime]] = {}
    duties: dict[tuple[int, Duty], SetPieceDuty] = {}
    changes: list[DutyChange] = []
    items: list[IntelItem] = []
    polls = 0
    first_poll: dt.datetime | None = None
    last_poll: dt.datetime | None = None
    names: dict[int, str] = {}

    for snap in read_archive(directory, until=until):
        polls += 1
        first_poll = first_poll or snap.fetched_at
        last_poll = snap.fetched_at
        table = duty_table(snap)
        names = _names(snap)

        if prev is not None and prev_at is not None:
            found = compare(
                prev, table, season=season, prior_as_of=prev_at,
                detected_at=snap.fetched_at, names=names,
            )
            changes.extend(found)
            for c in found:
                if abs(c.delta_goals_per_game) < ALERT_GOALS_PER_GAME:
                    continue
                items.append(
                    IntelItem(
                        item_id=content_id("spi", c.change_id),
                        published_at=c.detected_at,
                        observed_at=c.detected_at,
                        kind=IntelKind.SET_PIECE,
                        headline=c.headline,
                        body=(
                            f"Worth {c.delta_goals_per_game:+.3f} goals per game to the "
                            f"player at FPL's stated order. Detected by comparing the "
                            f"bootstrap poll at {c.prior_as_of:%Y-%m-%d %H:%M}Z with the "
                            f"one at {c.detected_at:%Y-%m-%d %H:%M}Z."
                        ),
                        source=SOURCE,
                        season=season,
                        player_code=c.code,
                        team_code=c.team_code,
                        confidence=1.0,
                    )
                )

        for key, (order, note, team_code) in table.items():
            known = first_seen.get(key)
            if known is None or known[0] != order:
                first_seen[key] = (order, snap.fetched_at)
            duties[key] = SetPieceDuty(
                season=season, code=key[0], duty=key[1], ord=order,
                as_of=first_seen[key][1], source=SOURCE,
                team_code=team_code, note=note,
            )
        # A player who dropped off the list needs a row recording that, dated to
        # the poll that revealed the absence. Without it the store's "latest row
        # wins" read would keep returning the stale duty forever.
        for key in set(duties) - set(table):
            if duties[key].ord is None:
                continue
            duties[key] = SetPieceDuty(
                season=season, code=key[0], duty=key[1], ord=None,
                as_of=snap.fetched_at, source=SOURCE,
                team_code=duties[key].team_code, note=None,
            )
            first_seen[key] = (None, snap.fetched_at)

        prev, prev_at = table, snap.fetched_at

    return DutyScan(
        duties=sorted(duties.values(), key=lambda d: (str(d.duty), d.ord or 99, d.code)),
        changes=changes,
        items=items,
        polls=polls,
        first_poll=first_poll,
        last_poll=last_poll,
    )


#: Where the historical archive keeps one end-of-season ``players_raw.csv`` per
#: season. It carries the same three ``*_order`` columns the live API does.
VAASTAV_DIR = RAW_ROOT / "vaastav"


def _vaastav_duty_table(path: Path) -> tuple[DutyMap, dict[int, str]]:
    """Duty order from one archived ``players_raw.csv``."""
    import pandas as pd

    df = pd.read_csv(path)
    table: DutyMap = {}
    names: dict[int, str] = {}
    columns = {
        Duty.PENALTIES: ("penalties_order", "penalties_text"),
        Duty.DIRECT_FREEKICKS: ("direct_freekicks_order", "direct_freekicks_text"),
        Duty.CORNERS_INDIRECT: (
            "corners_and_indirect_freekicks_order",
            "corners_and_indirect_freekicks_text",
        ),
    }
    for row in df.itertuples():
        code = getattr(row, "code", None)
        if code is None or pd.isna(code):
            continue
        code = int(code)
        names[code] = str(getattr(row, "web_name", "") or "")
        team_code = getattr(row, "team_code", None)
        team_code = None if team_code is None or pd.isna(team_code) else int(team_code)
        for duty, (order_col, text_col) in columns.items():
            order = getattr(row, order_col, None)
            if order is None or pd.isna(order) or int(order) == 0:
                continue
            note = getattr(row, text_col, None)
            note = None if note is None or pd.isna(note) else str(note)
            table[(code, duty)] = (int(order), note, team_code)
    return table, names


def scan_seasons(
    seasons: list[str],
    *,
    season_end: dict[str, dt.datetime],
    directory: Path = VAASTAV_DIR,
) -> DutyScan:
    """Compare end-of-season duty snapshots across seasons.

    The live bootstrap archive only goes back as far as this project's first
    poll, which is hours rather than years, so it cannot yet demonstrate that
    change detection works on anything but a synthetic input. The historical
    archive can: ``players_raw.csv`` is a season-end snapshot carrying the same
    three ``*_order`` columns, so comparing consecutive seasons surfaces every
    duty that genuinely moved between them.

    ``season_end`` maps a season to the instant its snapshot became true --
    normally the last kickoff of that season. Dating these to "now" would let a
    2024-25 penalty order be visible to a snapshot taken in 2023, which is the
    precise failure this whole package exists to prevent, so a season with no
    entry is skipped rather than defaulted.
    """
    prev: DutyMap | None = None
    prev_at: dt.datetime | None = None
    duties: dict[tuple[int, Duty], SetPieceDuty] = {}
    changes: list[DutyChange] = []
    items: list[IntelItem] = []
    polls = 0
    first_poll: dt.datetime | None = None
    last_poll: dt.datetime | None = None
    # Names accumulate across seasons rather than being replaced. A player who
    # LOST a duty is very often a player who left the league, so he is absent
    # from the later snapshot -- and looking his name up only there produces
    # "player 101668 is no longer listed for penalties", which is exactly the
    # headline a human cannot act on.
    names: dict[int, str] = {}

    for season in sorted(seasons):
        path = directory / season / "players_raw.csv"
        when = season_end.get(season)
        if not path.exists() or when is None:
            continue
        table, season_names = _vaastav_duty_table(path)
        names.update({k: v for k, v in season_names.items() if v})
        when = when.astimezone(UTC)
        polls += 1
        first_poll = first_poll or when
        last_poll = when

        if prev is not None and prev_at is not None:
            found = compare(
                prev, table, season=season, prior_as_of=prev_at,
                detected_at=when, names=names,
            )
            changes.extend(found)
            for c in found:
                if abs(c.delta_goals_per_game) < ALERT_GOALS_PER_GAME:
                    continue
                items.append(
                    IntelItem(
                        item_id=content_id("spi", c.change_id),
                        published_at=c.detected_at,
                        observed_at=c.detected_at,
                        kind=IntelKind.SET_PIECE,
                        headline=c.headline,
                        body=(
                            f"Worth {c.delta_goals_per_game:+.3f} goals per game at FPL's "
                            f"stated order. Detected between the end-of-season snapshots "
                            f"for the two seasons ending {c.prior_as_of:%Y-%m-%d} and "
                            f"{c.detected_at:%Y-%m-%d}, so the exact date within that "
                            "window is unknown and the later bound is used."
                        ),
                        source="vaastav:players_raw.csv",
                        season=season,
                        player_code=c.code,
                        team_code=c.team_code,
                        confidence=0.7,
                    )
                )

        for key, (order, note, team_code) in table.items():
            duties[key] = SetPieceDuty(
                season=season, code=key[0], duty=key[1], ord=order,
                as_of=when, source="vaastav:players_raw.csv",
                team_code=team_code, note=note,
            )
        prev, prev_at = table, when

    return DutyScan(
        duties=sorted(duties.values(), key=lambda d: (str(d.duty), d.ord or 99, d.code)),
        changes=changes, items=items, polls=polls,
        first_poll=first_poll, last_poll=last_poll,
    )


def duty_summary(duties: list[SetPieceDuty]) -> str:
    """One line per duty a player holds, or an explicit statement that they hold none."""
    held = [d for d in duties if d.ord is not None]
    if not held:
        return "Not listed for penalties, direct free kicks or corners by FPL."
    lines = []
    for d in sorted(held, key=lambda x: (x.ord or 99, str(x.duty))):
        rank = "FIRST CHOICE" if d.ord == 1 else f"#{d.ord}"
        note = f" — FPL note: {d.note}" if d.note else ""
        lines.append(f"{d.duty.label}: {rank}{note}")
    return "\n".join(lines)
