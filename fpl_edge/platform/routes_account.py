"""``/api/account/*``: connect the FPL account from the browser.

Three routes over :mod:`fpl_edge.myteam.account`, which is the same code the
CLI's ``fpl myteam auth --paste-cookie`` runs, so the paste that works in a
terminal works here and fails with the same words.

    GET  /api/account/status    token state, last verification, squad source
    POST /api/account/connect   {"cookie": "<pasted text>"} -> Outcome + status
    POST /api/account/verify    the live check again, no new paste
    GET  /api/account/entry     the saved team id, and where it came from
    POST /api/account/entry     {"entry_id": 1234567} -> the id, checked first

The team-id routes are the only two that answer a caller who has connected
nothing: ``entry/{id}/`` is public, so a manager can save and confirm their own
id before pasting any credential. They are still behind the loopback guard,
because they write to the same per-user directory the credential routes do.

Two rules the routes enforce themselves rather than trusting the server's
bind address:

* **Loopback only.** A request whose client host is not 127.0.0.1 or ::1 is
  refused with 403 before any body is read. The server already binds loopback;
  a second check costs nothing and survives someone changing ``--host``.
* **No token leaves.** Responses carry expiry instants, an entry name and the
  failure class, never a token value. The pasted body is read once, handed to
  the token manager, and never logged or echoed: the body is taken as a plain
  dict so a malformed request cannot be reflected back by the validator.

Wiring (app.py): ``app.include_router(routes_account.router)``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from fpl_edge.config import ENV_PATH
from fpl_edge.myteam import account
from fpl_edge.myteam.private import PrivateTeamClient
from fpl_edge.myteam.tokens import TokenManager
from fpl_edge.platform.users import UserContext, current_user, stored_entry_id, write_profile

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else None
    if host not in LOOPBACK_HOSTS:
        raise HTTPException(
            status_code=403,
            detail="account routes answer loopback clients only",
        )


def build_router(
    *,
    env_path: Path | None = None,
    record_path: Path | None = None,
    entry_id: int | None = None,
    entry_lookup=account.default_entry_lookup,
    client_factory=None,
    entry_check=account.check_entry,
) -> APIRouter:
    """A router bound to one token store. Defaults are the live ``.env`` and
    the configured entry; tests pass temp paths and a client pinned to no
    legacy cookie (the default client reads FPL_SESSION_COOKIE from .env)."""
    router = APIRouter(prefix="/api/account", tags=["account"])
    record = record_path if record_path is not None else account.RECORD_PATH
    make_client = client_factory or (lambda m: PrivateTeamClient(tokens=m))

    def manager() -> TokenManager:
        # A fresh manager per request: it reads .env on every call, so a
        # connect made from the CLI shows up here without a restart, and the
        # panels (which construct their own) see this route's writes at once.
        return TokenManager(env_path=env_path or ENV_PATH)

    def status_payload() -> dict[str, Any]:
        return account.account_status(manager(), entry_id=entry_id, record_path=record)

    @router.get("/status")
    def status(request: Request) -> dict[str, Any]:
        _require_loopback(request)
        return status_payload()

    @router.post("/connect")
    def connect(request: Request, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        _require_loopback(request)
        cookie = payload.get("cookie") if isinstance(payload, dict) else None
        if not isinstance(cookie, str) or not cookie.strip():
            # Deliberately not a 422: the validator would echo the body back.
            raise HTTPException(
                status_code=400,
                detail='body must be {"cookie": "<the pasted Cookie header or tokens>"}',
            )
        m = manager()
        outcome = account.connect(
            cookie, manager=m, client=make_client(m), entry_id=entry_id,
            entry_lookup=entry_lookup, record_path=record,
        )
        return {**outcome.to_dict(), "status": status_payload()}

    #: The remediation an unknown id gets. The id is not the league position,
    #: the team name or the manager id: it is the number in the URL when the
    #: manager looks at their own points page.
    _WHERE_IS_MY_ID = (
        "Your team id is the number in the address bar when you open your own "
        "points page on the FPL site, in fantasy.premierleague.com/entry/"
        "<this number>/event/1."
    )

    @router.get("/entry")
    def get_entry(request: Request,
                  user: UserContext = Depends(current_user)) -> dict[str, Any]:
        """The team id this user's panels read, and whether they chose it."""
        _require_loopback(request)
        saved = stored_entry_id(user.data_root)
        return {
            "entry_id": int(user.entry_id),
            "saved": saved is not None,
            "source": "saved on this tab" if saved is not None else
                      "the server default, which is the owner's team",
            "where_is_my_id": _WHERE_IS_MY_ID,
        }

    @router.post("/entry")
    def post_entry(request: Request,
                   payload: Annotated[dict[str, Any], Body()],
                   user: UserContext = Depends(current_user)) -> dict[str, Any]:
        """Save a team id, after checking that a team actually has it.

        Nothing is stored unless ``entry/{id}/`` answered 200. A 404 says the
        id is unknown and where to find the right one; anything else says the
        check could not be completed and offers a retry, because a timeout is
        not evidence that an id is wrong.
        """
        _require_loopback(request)
        raw = payload.get("entry_id") if isinstance(payload, dict) else None
        try:
            entry_id = int(str(raw).strip())
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail=f"entry_id must be a positive whole number. "
                       f"{_WHERE_IS_MY_ID}",
            ) from None
        if entry_id <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"entry_id must be a positive whole number. "
                       f"{_WHERE_IS_MY_ID}",
            )

        check = entry_check(entry_id)
        if not check.found and check.status == 404:
            raise HTTPException(
                status_code=404,
                detail=f"FPL has no team with id {entry_id}. Nothing was "
                       f"saved. {_WHERE_IS_MY_ID}",
            )
        if not check.found:
            where = (f"FPL answered HTTP {check.status}" if check.status
                     else f"the request did not complete ({check.error})")
            raise HTTPException(
                status_code=502,
                detail=f"The id could not be checked: {where}. Nothing was "
                       f"saved, and this does not mean the id is wrong. Try "
                       f"again in a moment.",
            )

        profile = write_profile(user, entry_id=entry_id,
                                team_name=check.team_name,
                                manager_name=check.manager_name)
        return {
            "ok": True,
            "entry_id": entry_id,
            "team_name": profile.get("team_name"),
            "manager_name": profile.get("manager_name"),
            "saved": True,
            "note": "The panels read this team from your next page load.",
        }

    @router.post("/verify")
    def verify(request: Request) -> dict[str, Any]:
        _require_loopback(request)
        m = manager()
        outcome = account.verify(
            manager=m, client=make_client(m), entry_id=entry_id,
            entry_lookup=entry_lookup, record_path=record,
        )
        return {**outcome.to_dict(), "status": status_payload()}

    return router


#: The router app.py includes. Bound to the live .env and the configured entry.
router = build_router()
