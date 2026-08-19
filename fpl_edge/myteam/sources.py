"""The public FPL entry endpoints, and the one we are forbidden to touch.

Everything this package knows about the manager's team comes from endpoints that
need no login:

===========================================  =====================================
``/api/entry/{id}/``                         profile, and the bank / squad value /
                                             transfer count *as at the last
                                             deadline that has passed*
``/api/entry/{id}/history/``                 per-gameweek points, rank, bank,
                                             value, transfers and hits, plus the
                                             chips played and past seasons
``/api/entry/{id}/transfers/``               every transfer this entry ever made,
                                             with the price paid and the price
                                             received
``/api/entry/{id}/event/{gw}/picks/``        the 15, their order, captain and
                                             chip -- **404 until the gameweek
                                             starts**, public thereafter
===========================================  =====================================

``/api/my-team/{id}/`` is the endpoint that would answer everything instantly. It
requires a logged-in session cookie and returns 403 to us. We do not have the
manager's password, will not ask for one, and will not store a session cookie, so
that endpoint is treated as non-existent: :func:`forbid_authenticated_url` raises
on any URL that would reach it, and it is called on every request this module
makes. The check is deliberately in the request path rather than in a comment,
because a comment does not fail a test.

The consequence is a genuine, unavoidable hole: **before a gameweek kicks off
there is no public way to see the squad.** That is the gap
:mod:`fpl_edge.myteam.manual` exists to fill, once, by asking.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

import httpx

from fpl_edge.ingest.http import Fetcher
from fpl_edge.types import GwId, Money

BASE = "https://fantasy.premierleague.com/api"

#: Endpoint fragments that only answer to an authenticated session. Reaching any
#: of these means someone has started down the password road.
AUTHENTICATED_PATHS = ("/my-team/", "/me/", "/accounts/login")


class AuthenticatedEndpointError(RuntimeError):
    """An endpoint requiring a login was requested.

    Raised rather than attempted. We have no password and will not obtain one,
    so an authenticated call cannot succeed -- it can only produce a 403 and the
    temptation to fix it by adding credentials.
    """


def forbid_authenticated_url(url: str) -> str:
    """Return ``url`` unchanged, or raise if it needs a login."""
    lowered = url.lower()
    for fragment in AUTHENTICATED_PATHS:
        if fragment in lowered:
            raise AuthenticatedEndpointError(
                f"{url} requires a logged-in FPL session. This engine has no "
                f"password for the account and must never acquire one. Squad state "
                f"before a gameweek starts comes from `fpl myteam set` (or the "
                f"bot's /setsquad), not from authenticating."
            )
    return url


# -- value objects -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EntrySummary:
    """``/api/entry/{id}/``.

    ``last_deadline_*`` are None for an entry that has not yet passed a
    deadline. That is not an error and must not be coerced to zero: a bank of
    None means "the season has not started", while a bank of 0.0 means "you have
    spent every penny", and confusing the two makes the budget arithmetic lie.
    """

    entry_id: int
    name: str
    player_name: str
    started_event: int | None
    current_event: int | None
    entered_events: tuple[int, ...]
    last_deadline_bank: Money | None
    last_deadline_value: Money | None
    last_deadline_total_transfers: int
    years_active: int | None
    favourite_team: int | None
    summary_overall_points: int | None
    summary_overall_rank: int | None

    @property
    def has_started(self) -> bool:
        return bool(self.entered_events)


@dataclass(frozen=True, slots=True)
class GwHistoryRow:
    """One row of ``history.current`` -- the state *after* that gameweek."""

    gw: GwId
    points: int
    total_points: int
    rank: int | None
    overall_rank: int | None
    bank: Money
    value: Money
    event_transfers: int
    event_transfers_cost: int
    points_on_bench: int


@dataclass(frozen=True, slots=True)
class ChipPlay:
    name: str
    gw: GwId
    played_utc: dt.datetime | None


@dataclass(frozen=True, slots=True)
class PastSeason:
    """One row of ``history.past``.

    Note what is *not* here: the entry id for that season, and the number of
    transfers made in it. FPL issues a new entry id every season and exposes no
    public mapping from a manager to their previous ids, so per-season transfer
    counts for past seasons are unobtainable from public data. See
    :mod:`fpl_edge.myteam.state` for how that is reported rather than guessed.
    """

    season_name: str
    total_points: int
    rank: int


@dataclass(frozen=True, slots=True)
class EntryHistory:
    current: tuple[GwHistoryRow, ...]
    past: tuple[PastSeason, ...]
    chips: tuple[ChipPlay, ...]

    @property
    def last_gw(self) -> GwId | None:
        return self.current[-1].gw if self.current else None


@dataclass(frozen=True, slots=True)
class TransferRow:
    """One row of ``/api/entry/{id}/transfers/``.

    ``element_in_cost`` is the price *paid*, which is exactly the purchase price
    the sell-on fee is computed against later. ``element_out_cost`` is the price
    *received*, i.e. already net of the fee -- it is not the outgoing player's
    market price and must never be used as one.
    """

    gw: GwId
    element_in: int
    element_in_cost: Money
    element_out: int
    element_out_cost: Money
    made_utc: dt.datetime | None


@dataclass(frozen=True, slots=True)
class PublicPick:
    element: int
    position: int          # 1..15, FPL's own slot order
    multiplier: int
    is_captain: bool
    is_vice_captain: bool


@dataclass(frozen=True, slots=True)
class GwPicks:
    """``/api/entry/{id}/event/{gw}/picks/`` once the gameweek has started."""

    gw: GwId
    active_chip: str | None
    picks: tuple[PublicPick, ...]
    bank: Money | None
    value: Money | None
    event_transfers: int
    event_transfers_cost: int


# -- parsing -----------------------------------------------------------------


def _money(value: Any) -> Money | None:
    """FPL entry money is already in integer tenths. Never float it."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"expected an integer number of tenths from the FPL API, got {value!r}. "
            "Float pounds break the sell-on fee by a tenth per player."
        )
    return Money(value)


def _ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def parse_entry(body: dict[str, Any]) -> EntrySummary:
    return EntrySummary(
        entry_id=int(body["id"]),
        name=str(body.get("name", "")),
        player_name=" ".join(
            x for x in (body.get("player_first_name"), body.get("player_last_name")) if x
        ),
        started_event=body.get("started_event"),
        current_event=body.get("current_event"),
        entered_events=tuple(int(e) for e in (body.get("entered_events") or ())),
        last_deadline_bank=_money(body.get("last_deadline_bank")),
        last_deadline_value=_money(body.get("last_deadline_value")),
        last_deadline_total_transfers=int(body.get("last_deadline_total_transfers") or 0),
        years_active=body.get("years_active"),
        favourite_team=body.get("favourite_team"),
        summary_overall_points=body.get("summary_overall_points"),
        summary_overall_rank=body.get("summary_overall_rank"),
    )


def parse_history(body: dict[str, Any]) -> EntryHistory:
    current = tuple(
        GwHistoryRow(
            gw=GwId(int(r["event"])),
            points=int(r.get("points") or 0),
            total_points=int(r.get("total_points") or 0),
            rank=r.get("rank"),
            overall_rank=r.get("overall_rank"),
            bank=Money(int(r.get("bank") or 0)),
            value=Money(int(r.get("value") or 0)),
            event_transfers=int(r.get("event_transfers") or 0),
            event_transfers_cost=int(r.get("event_transfers_cost") or 0),
            points_on_bench=int(r.get("points_on_bench") or 0),
        )
        for r in (body.get("current") or ())
    )
    past = tuple(
        PastSeason(
            season_name=str(r["season_name"]),
            total_points=int(r["total_points"]),
            rank=int(r["rank"]),
        )
        for r in (body.get("past") or ())
    )
    chips = tuple(
        ChipPlay(name=str(c["name"]), gw=GwId(int(c["event"])), played_utc=_ts(c.get("time")))
        for c in (body.get("chips") or ())
    )
    return EntryHistory(
        current=tuple(sorted(current, key=lambda r: int(r.gw))),
        past=past,
        chips=tuple(sorted(chips, key=lambda c: int(c.gw))),
    )


def parse_transfers(body: list[dict[str, Any]]) -> tuple[TransferRow, ...]:
    rows = tuple(
        TransferRow(
            gw=GwId(int(t["event"])),
            element_in=int(t["element_in"]),
            element_in_cost=Money(int(t["element_in_cost"])),
            element_out=int(t["element_out"]),
            element_out_cost=Money(int(t["element_out_cost"])),
            made_utc=_ts(t.get("time")),
        )
        for t in (body or ())
    )
    # The API returns newest-first. Purchase-price derivation walks forward in
    # time, so sort here once rather than relying on the endpoint's order.
    return tuple(sorted(rows, key=lambda t: (int(t.gw), t.made_utc or dt.datetime.min.replace(tzinfo=dt.timezone.utc))))


def parse_picks(gw: int, body: dict[str, Any]) -> GwPicks:
    hist = body.get("entry_history") or {}
    return GwPicks(
        gw=GwId(int(gw)),
        active_chip=body.get("active_chip") or None,
        picks=tuple(
            PublicPick(
                element=int(p["element"]),
                position=int(p["position"]),
                multiplier=int(p.get("multiplier", 1)),
                is_captain=bool(p.get("is_captain")),
                is_vice_captain=bool(p.get("is_vice_captain")),
            )
            for p in (body.get("picks") or ())
        ),
        bank=_money(hist.get("bank")),
        value=_money(hist.get("value")),
        event_transfers=int(hist.get("event_transfers") or 0),
        event_transfers_cost=int(hist.get("event_transfers_cost") or 0),
    )


# -- the client --------------------------------------------------------------


_ENTRY_ID = re.compile(r"^\d+$")


class PublicEntryClient:
    """Reads only the endpoints that answer without a login.

    Every response goes through :class:`~fpl_edge.ingest.http.Fetcher`, so the
    raw body is archived with its sha256 and any claim made about the squad is
    traceable to bytes we actually received.
    """

    def __init__(self, fetcher: Fetcher | None = None, *, base_url: str = BASE) -> None:
        self._owns = fetcher is None
        self._fetcher = fetcher or Fetcher("fpl_entry", base_url=base_url)
        self.base_url = base_url

    def _get(self, endpoint: str) -> Any:
        forbid_authenticated_url(f"{self.base_url}/{endpoint.lstrip('/')}")
        return self._fetcher.get_json(endpoint).body

    @staticmethod
    def _check(entry_id: int) -> str:
        text = str(int(entry_id))
        if not _ENTRY_ID.match(text):
            raise ValueError(f"entry id must be a positive integer, got {entry_id!r}")
        return text

    def entry(self, entry_id: int) -> EntrySummary:
        return parse_entry(self._get(f"entry/{self._check(entry_id)}/"))

    def history(self, entry_id: int) -> EntryHistory:
        return parse_history(self._get(f"entry/{self._check(entry_id)}/history/"))

    def transfers(self, entry_id: int) -> tuple[TransferRow, ...]:
        return parse_transfers(self._get(f"entry/{self._check(entry_id)}/transfers/"))

    def picks(self, entry_id: int, gw: int) -> GwPicks | None:
        """The 15 for a gameweek, or None if the gameweek has not started.

        A 404 here is the normal, expected answer before kickoff -- FPL does not
        publish anyone's picks until the deadline has passed. It is returned as
        None rather than raised, because "not yet visible" is a state this
        package is built around, not an error.
        """
        try:
            body = self._get(f"entry/{self._check(entry_id)}/event/{int(gw)}/picks/")
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise
        return parse_picks(int(gw), body)

    def close(self) -> None:
        if self._owns:
            self._fetcher.close()

    def __enter__(self) -> "PublicEntryClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
