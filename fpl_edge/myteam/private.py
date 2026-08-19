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
    "  1. Log in at https://fantasy.premierleague.com in your browser.\n"
    "  2. DevTools -> Network -> click any /api/ request.\n"
    "  3. Copy the full Cookie request-header value.\n"
    "  4. Run: uv run fpl myteam auth --paste-cookie   (prompts, stores, done)\n"
    "That is a ONE-TIME step: the stored refresh token renews itself for about "
    "six months. No password is stored and none is ever asked for; revoke by "
    "logging out of that browser session."
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
                 timeout: float = 30.0, tokens: object | None = None) -> None:
        self._cookie = cookie if cookie is not None else secret(
            "FPL_SESSION_COOKIE", required=False
        )
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        # Injectable so tests pin it to a temp .env. Defaulting to the real
        # TokenManager() inside fetch() made unit tests read the developer's
        # live .env and try to refresh REAL tokens over the network.
        self._tokens = tokens

    @property
    def configured(self) -> bool:
        return bool(self._cookie)

    @staticmethod
    def disabled_by_env() -> bool:
        """True when auto-private reads are switched off for this process.

        Set FPL_EDGE_DISABLE_PRIVATE=1 to guarantee no authenticated request
        can happen -- the test suite sets it globally, because a unit test that
        quietly refreshes REAL tokens over the network is how this was found.
        """
        import os

        return os.environ.get("FPL_EDGE_DISABLE_PRIVATE", "") not in ("", "0")

    def fetch(self, entry_id: int) -> PrivateSquad:
        """Read my-team, preferring the self-renewing bearer token.

        Order of attempts, each with a distinct failure story:

        1. **Bearer** via :class:`~fpl_edge.myteam.tokens.TokenManager` -- the
           access token auto-refreshes through the OAuth grant, so this path
           needs no manual upkeep for the refresh token's ~6-month life. A 401
           forces one refresh-and-retry before giving up, because an access
           token can expire between the freshness check and the request.
        2. **Raw cookie** fallback for a setup that predates the token flow.
        """
        from fpl_edge.myteam.tokens import (
            AuthNotConfiguredError,
            RefreshRefusedError,
            TokenManager,
        )

        url = f"{self._base}/my-team/{int(entry_id)}/"
        manager = self._tokens if self._tokens is not None else TokenManager()

        if manager.configured:
            try:
                resp = self._get_bearer(url, manager.access_token())
                if resp.status_code in (401, 403):
                    # Force one refresh: the cached token may have just aged out
                    # or been rotated by another client.
                    resp = self._get_bearer(url, manager._refresh(manager._read()))
                if resp.status_code in (401, 403):
                    raise StaleSessionError(
                        f"FPL rejected a freshly refreshed token "
                        f"(HTTP {resp.status_code}). The session was revoked; "
                        f"log in once in your browser and re-run "
                        f"`uv run fpl myteam auth --paste-cookie`."
                    )
                resp.raise_for_status()
                return self._parse(resp.json())
            except (AuthNotConfiguredError, RefreshRefusedError) as exc:
                if not self._cookie:
                    raise StaleSessionError(str(exc)) from exc
                # fall through to the cookie path

        if not self._cookie:
            raise NoSessionError(
                "No FPL auth is configured, so the pre-deadline squad cannot be "
                "read. One-time setup:\n" + SETUP_STEPS
            )
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

    def _get_bearer(self, url: str, token: str) -> httpx.Response:
        """One my-team GET with the bearer header the FPL web app itself sends."""
        headers = {
            "User-Agent": USER_AGENT,
            "X-API-Authorization": f"Bearer {token}",
        }
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            return client.get(url, headers=headers)

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
