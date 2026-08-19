"""Reading the intel FPL already gives us, out of the raw archive.

``fpl_edge.ingest.fpl_api`` lands price, ownership, availability and news in
``fact_player_state``. It does not land four other fields that ``bootstrap-static``
returns and that are, between them, the most valuable non-price information in
the whole payload:

``penalties_order``, ``direct_freekicks_order``, ``corners_and_indirect_freekicks_order``
    FPL's own statement of who takes what. First-party, not a scrape, not a
    forum consensus. Roughly 65 / 54 / 79 players carry a non-null order in the
    2026-27 payload.
``scout_news_link``
    A URL, usually to a club's own site, attached to a player. In the 2026-27
    payload these point at things like "every word of Mikel's post-Dortmund
    press conference" on arsenal.com. This is FPL editorially linking a player
    to press-conference coverage, which is exactly the signal a scraped team-news
    page would be trying to reconstruct -- except that this one is first-party
    and arrives with no licence question attached.
``price_change_projections`` / ``price_change_hourly_rate`` / ``price_change_locked_until``
    FPL's own forecast of imminent price movement. The rule registry records
    ``prices.in_season_change_time_utc`` as UNVERIFIED, so the engine is not
    allowed to assume a nightly change time -- but FPL stating a projected
    percentage and an hourly rate is an observation, not an assumption, and it
    is the honest way to answer "is he about to rise?".

Rather than ask the ingest team to widen ``fact_player_state`` (their table,
their migration), this module reads the archived response bodies directly. Every
body ``Fetcher.get_json`` ever wrote is still on disk under ``data/raw/fpl_api``
with the fetch instant in its filename, so the archive is a complete, replayable
history of these fields at poll resolution.

Timestamp discipline
--------------------
Only ``news_added`` carries a real publication instant. For everything else FPL
states a value with no indication of when it changed, so this module sets
``published_at`` to **the poll at which we first observed that value**. That is
an upper bound on the true publication instant, and erring upward is the safe
direction: it can make the engine look slower than it was, but it can never let
a snapshot see a set-piece order before we had evidence for it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fpl_edge.intel.items import Duty

UTC = dt.timezone.utc

#: Resolved against the repository rather than the current working directory.
#: The MCP server runs from its own checkout and the Telegram bot may run from
#: anywhere; a cwd-relative path made both of them silently report "no archived
#: bootstrap body" for every player while the archive sat there untouched.
#: FPL_EDGE_RAW overrides the root for tests and for a relocated archive.
REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = Path(os.environ.get("FPL_EDGE_RAW") or (REPO_ROOT / "data" / "raw"))
ARCHIVE_DIR = RAW_ROOT / "fpl_api"

#: Written by ``Fetcher.get_json`` as ``<slug>_<%Y%m%dT%H%M%SZ>_<sha8>.json``.
_STAMP = re.compile(r"_(\d{8}T\d{6}Z)_")

#: FPL's field name for each duty, and the accompanying free-text note.
DUTY_FIELDS: dict[Duty, tuple[str, str]] = {
    Duty.PENALTIES: ("penalties_order", "penalties_text"),
    Duty.DIRECT_FREEKICKS: ("direct_freekicks_order", "direct_freekicks_text"),
    Duty.CORNERS_INDIRECT: (
        "corners_and_indirect_freekicks_order",
        "corners_and_indirect_freekicks_text",
    ),
}


@dataclass(frozen=True, slots=True)
class ArchivedBootstrap:
    """One archived ``bootstrap-static`` body and the instant it was fetched."""

    path: Path
    fetched_at: dt.datetime
    body: dict[str, Any]

    @property
    def elements(self) -> list[dict[str, Any]]:
        return list(self.body.get("elements") or [])

    @property
    def teams(self) -> list[dict[str, Any]]:
        return list(self.body.get("teams") or [])

    def team_code_by_id(self) -> dict[int, int]:
        """Per-season ``team`` id -> stable ``team_code``.

        Needed because ``elements[].team`` is the per-season 1..20 id, which is
        reassigned alphabetically every August. Storing it would make Man Utd
        become Newcastle across a season boundary; only ``code`` is stable.
        """
        return {int(t["id"]): int(t["code"]) for t in self.teams if "id" in t and "code" in t}


def stamp_of(path: Path) -> dt.datetime | None:
    """Fetch instant encoded in an archive filename, or None if absent."""
    m = _STAMP.search(path.name)
    if not m:
        return None
    return dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def archive_paths(
    directory: Path = ARCHIVE_DIR,
    *,
    prefix: str = "bootstrap",
    until: dt.datetime | None = None,
) -> list[Path]:
    """Archived bodies in fetch order, oldest first.

    ``until`` drops anything fetched after a given instant, which is how a
    replay reconstructs what the collector would have seen at a past deadline
    without needing the network.
    """
    if not directory.exists():
        return []
    dated: list[tuple[dt.datetime, Path]] = []
    for path in directory.glob(f"{prefix}*.json"):
        when = stamp_of(path)
        if when is None:
            continue
        if until is not None and when > until.astimezone(UTC):
            continue
        dated.append((when, path))
    return [p for _, p in sorted(dated)]


def read_archive(
    directory: Path = ARCHIVE_DIR, *, until: dt.datetime | None = None
) -> Iterator[ArchivedBootstrap]:
    """Stream archived bootstraps oldest-first.

    A generator rather than a list: there are hundreds of ~2MB bodies and the
    change detector only ever needs two in memory at once.
    """
    for path in archive_paths(directory, until=until):
        when = stamp_of(path)
        if when is None:  # pragma: no cover - archive_paths already filtered
            continue
        try:
            body = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            # A truncated body is a missing observation, not a crash. The
            # collector's job is to survive a half-written file left behind by
            # a poller that was killed mid-write.
            continue
        if not isinstance(body, dict) or "elements" not in body:
            continue
        yield ArchivedBootstrap(path=path, fetched_at=when, body=body)


def duty_table(snap: ArchivedBootstrap) -> dict[tuple[int, Duty], tuple[int | None, str | None, int | None]]:
    """``(player code, duty) -> (order, note, team_code)`` for one poll.

    Only players FPL actually lists are present. Absence is the caller's problem
    to interpret, and :mod:`fpl_edge.intel.setpieces` interprets it as "not on
    the list", which is the state that makes a drop-off detectable.
    """
    by_id = snap.team_code_by_id()
    out: dict[tuple[int, Duty], tuple[int | None, str | None, int | None]] = {}
    for e in snap.elements:
        code = e.get("code")
        if code is None:
            continue
        team_code = by_id.get(int(e.get("team", 0)))
        for duty, (order_field, text_field) in DUTY_FIELDS.items():
            order = e.get(order_field)
            if order in (None, 0):
                continue
            note = e.get(text_field) or None
            out[(int(code), duty)] = (int(order), note, team_code)
    return out


def parse_news_added(value: object) -> dt.datetime | None:
    """FPL's ``news_added``, which is an ISO instant with a ``Z`` suffix.

    Returns None for null and for anything unparseable. A malformed timestamp
    must not become "now": that would date an old injury to the current poll and
    make it visible to a snapshot that should never have seen it.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PricePressure:
    """FPL's own view of how close a player is to a price change.

    Every field here is quoted from the payload rather than modelled. The rule
    registry marks the in-season price-change time UNVERIFIED and forbids the
    price model from assuming one; quoting FPL's stated projection sidesteps that
    entirely, because it is an observation of what FPL says rather than an
    inference about what FPL will do.
    """

    code: int
    percent: float | None
    hourly_rate: float | None
    locked_until: str | None
    calibrating: bool
    #: ``[(days ahead, likelihood, projected percent)]``, straight from the API.
    projections: tuple[tuple[int, float, float], ...] = ()
    transfers_in_event: int = 0
    transfers_out_event: int = 0
    cost_change_event: int = 0

    @property
    def net_transfers_event(self) -> int:
        return int(self.transfers_in_event) - int(self.transfers_out_event)

    def summary(self) -> str:
        if self.percent is None:
            return "FPL publishes no price-change percentage for this player."
        direction = "rise" if self.percent >= 0 else "fall"
        best = max((p for p in self.projections), key=lambda p: p[1], default=None)
        tail = ""
        if best is not None and best[1] > 0:
            tail = f"; FPL projects {best[2]:+.0f}% in {best[0]}d at {best[1]:.0%} likelihood"
        return (
            f"{self.percent:+.1f}% toward a {direction} "
            f"(hourly rate {self.hourly_rate or 0:+.2f}, "
            f"net transfers this event {self.net_transfers_event:+,}){tail}"
        )


def _num(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def price_pressure(element: dict[str, Any]) -> PricePressure:
    """Pull the price-change block out of one ``elements[]`` entry."""
    raw = element.get("price_change_projections") or []
    projections: list[tuple[int, float, float]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        offset = entry.get("offset")
        likelihood = _num(entry.get("likelihood"))
        projected = _num(entry.get("projected_percent"))
        if offset is None:
            continue
        projections.append((int(offset), likelihood or 0.0, projected or 0.0))
    return PricePressure(
        code=int(element.get("code", 0)),
        percent=_num(element.get("price_change_percent")),
        hourly_rate=_num(element.get("price_change_hourly_rate")),
        locked_until=element.get("price_change_locked_until") or None,
        calibrating=bool(element.get("price_change_calibrating")),
        projections=tuple(projections),
        transfers_in_event=int(element.get("transfers_in_event") or 0),
        transfers_out_event=int(element.get("transfers_out_event") or 0),
        cost_change_event=int(element.get("cost_change_event") or 0),
    )


def latest_element(
    code: int, *, directory: Path = ARCHIVE_DIR, until: dt.datetime | None = None
) -> tuple[dict[str, Any], dt.datetime] | None:
    """The most recent archived ``elements[]`` entry for one player.

    Reads newest-first and stops at the first hit, so the common case costs one
    JSON parse rather than several hundred.
    """
    for path in reversed(archive_paths(directory, until=until)):
        when = stamp_of(path)
        if when is None:  # pragma: no cover
            continue
        try:
            body = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for e in body.get("elements") or []:
            if int(e.get("code", -1)) == int(code):
                return e, when
    return None
