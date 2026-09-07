"""``/api/account/*``: connect the FPL account from the browser.

Three routes over :mod:`fpl_edge.myteam.account`, which is the same code the
CLI's ``fpl myteam auth --paste-cookie`` runs, so the paste that works in a
terminal works here and fails with the same words.

    GET  /api/account/status    token state, last verification, squad source
    POST /api/account/connect   {"cookie": "<pasted text>"} -> Outcome + status
    POST /api/account/verify    the live check again, no new paste

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

from fastapi import APIRouter, Body, HTTPException, Request

from fpl_edge.myteam import account
from fpl_edge.myteam.private import PrivateTeamClient
from fpl_edge.myteam.tokens import TokenManager

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
        return TokenManager(env_path=env_path) if env_path else TokenManager()

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
