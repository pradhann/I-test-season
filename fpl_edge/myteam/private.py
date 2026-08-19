"""The authenticated my-team reader, driven by a session cookie the manager
sets themselves.

Boundary, stated once and enforced in code:

* **No password, ever.** FPL's login runs through the Premier League SSO behind
  anti-bot protection; automating it would mean storing a plaintext password
  (which grants full account control, including email change) and circumventing
  bot defences, and this engine does neither. What it accepts instead is the
  *session cookie* of a login the manager performed themselves in their own
  browser — the same credential their browser presents on every page view,
  revocable by logging out, expiring on its own.
* The cookie lives in ``.env`` as ``FPL_SESSION_COOKIE`` and is read at request
  time. It is never logged, never printed, never archived: the raw-response
  archive stores bodies only, and this module's errors describe the cookie's
  state without quoting it.

What the endpoint buys over public reconstruction: the *pre-deadline* squad
(public picks appear only after kickoff), and exact per-player purchase and
selling prices plus the bank — observed, not derived, so the sell-on-fee
arithmetic has nothing left to reconstruct.

Setup (about 30 seconds, needed again only when the session expires):

1. Log in at https://fantasy.premierleague.com in your normal browser.
2. Open DevTools -> Network, click any request to ``/api/...``.
3. Copy the full value of the ``Cookie`` request header.
4. In ``.env``: ``FPL_SESSION_COOKIE=<paste>`` (the file is gitignored).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import httpx

from fpl_edge.config import secret
from fpl_edge.ingest.http import USER_AGENT
from fpl_edge.myteam.sources import BASE, GwPicks, PublicPick, _money
from fpl_edge.types import GwId, Money


class NoSessionError(RuntimeError):
    """FPL_SESSION_COOKIE is not set. Carries the setup steps."""


class StaleSessionError(RuntimeError):
    """The cookie was rejected. It expired or the session was logged out."""


SETUP_STEPS = (
    "Set FPL_SESSION_COOKIE in .env (gitignored):\n"
    "  1. Log in at https://fantasy.premierleague.com in your browser.\n"
    "  2. DevTools -> Network -> click any /api/ request.\n"
    "  3. Copy the full Cookie request-header value.\n"
    "  4. .env: FPL_SESSION_COOKIE=<paste>\n"
    "No password is stored and none is ever asked for; log out to revoke."
)


@dataclass(frozen=True, slots=True)
class PrivateSquad:
    """The my-team payload, normalised.

    ``picks`` reuses the public :class:`GwPicks` shape so everything downstream
    of :func:`fpl_edge.myteam.state.picks_from_public` works unchanged; the
    fields public data cannot carry ride alongside.
    """

    picks: GwPicks
    #: element_id -> purchase price in tenths, straight from the account.
    purchase_by_element: dict[int, int]
    #: element_id -> current selling price in tenths (sell-on fee applied by FPL).
    selling_by_element: dict[int, int]
    bank: Money
    squad_value: Money
    free_transfers: int | None
    transfers_made_this_gw: int
    #: chip name -> status_for_entry ("available" | "played" | "unavailable" ...)
    chips: dict[str, str]
    fetched_at: dt.datetime


class PrivateTeamClient:
    """Reads ``/api/my-team/{entry}/`` with the manager's own session cookie."""

    def __init__(self, cookie: str | None = None, *, base_url: str = BASE,
                 timeout: float = 30.0) -> None:
        self._cookie = cookie if cookie is not None else secret(
            "FPL_SESSION_COOKIE", required=False
        )
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._cookie)

    def fetch(self, entry_id: int) -> PrivateSquad:
        if not self._cookie:
            raise NoSessionError(
                "No FPL session cookie is configured, so the pre-deadline squad "
                "cannot be read.\n" + SETUP_STEPS
            )
        url = f"{self._base}/my-team/{int(entry_id)}/"
        headers = {"User-Agent": USER_AGENT, "Cookie": self._cookie}
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code in (401, 403):
            raise StaleSessionError(
                f"FPL rejected the session cookie (HTTP {resp.status_code}). "
                "It has expired or the session was logged out. Refresh it:\n"
                + SETUP_STEPS
            )
        resp.raise_for_status()
        return self._parse(resp.json())

    @staticmethod
    def _parse(body: dict[str, Any]) -> PrivateSquad:
        raw_picks = body.get("picks") or []
        if len(raw_picks) != 15:
            raise ValueError(
                f"my-team returned {len(raw_picks)} picks; expected 15. "
                "The payload shape may have changed -- inspect it before trusting "
                "anything derived from it."
            )
        picks = tuple(
            PublicPick(
                element=int(p["element"]),
                position=int(p["position"]),
                multiplier=int(p.get("multiplier", 1)),
                is_captain=bool(p.get("is_captain")),
                is_vice_captain=bool(p.get("is_vice_captain")),
            )
            for p in raw_picks
        )
        tr = body.get("transfers") or {}
        bank = _money(tr.get("bank"))
        value = _money(tr.get("value"))
        if bank is None or value is None:
            raise ValueError("my-team carried no bank/value; refusing to guess")

        limit = tr.get("limit")
        chips = {
            str(c.get("name")): str(c.get("status_for_entry"))
            for c in (body.get("chips") or [])
            if c.get("name")
        }
        return PrivateSquad(
            picks=GwPicks(
                gw=GwId(0),  # my-team is "now", not a settled gameweek
                active_chip=None,
                picks=picks,
                bank=bank,
                value=value,
                event_transfers=int(tr.get("made") or 0),
                event_transfers_cost=int(tr.get("cost") or 0),
            ),
            purchase_by_element={
                int(p["element"]): int(p["purchase_price"])
                for p in raw_picks if p.get("purchase_price") is not None
            },
            selling_by_element={
                int(p["element"]): int(p["selling_price"])
                for p in raw_picks if p.get("selling_price") is not None
            },
            bank=bank,
            squad_value=value,
            free_transfers=int(limit) if limit is not None else None,
            transfers_made_this_gw=int(tr.get("made") or 0),
            chips=chips,
            fetched_at=dt.datetime.now(dt.timezone.utc),
        )

    def __repr__(self) -> str:  # never leak the cookie through debug output
        state = "configured" if self.configured else "unconfigured"
        return f"PrivateTeamClient({state})"
