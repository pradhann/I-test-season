"""The sign-in routes, the key routes, and the one check that reads the matrix.

Seven routes are added to the app:

    GET    /auth/google/start      mint the handshake, redirect to Google
    GET    /auth/google/callback   verify, upsert the user, set the cookies
    POST   /auth/logout            revoke this session, clear the cookies
    GET    /api/me                 who the caller is, for the shell header
    GET    /api/account/key        whether a key is stored, and its last four
    PUT    /api/account/key        store or rotate one
    DELETE /api/account/key        remove it

:func:`install_auth` wires them, adds :func:`enforce_policy` as an
application wide dependency, and re-adds the three generated schema routes
behind the operator tier. It is called from
``fpl_edge/platform/app/factory.py`` and takes any FastAPI instance, so a test
can build a small app with the same enforcement the real one has.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from fpl_edge.platform.auth import keys, oauth, policy, sessions, settings
from fpl_edge.platform.users import UserContext, current_user

log = logging.getLogger(__name__)

#: Set to 0 to store a pasted key without checking it against Anthropic. On
#: by default, because the alternative is a manager who believes they are
#: configured and finds out at the deadline.
VERIFY_ENV = "FPL_EDGE_VERIFY_USER_KEY"


# ------------------------------------------------------------- route walking

def iter_app_routes(app: FastAPI):
    """Every (method, path, route) the app serves, including included routers.

    FastAPI 0.141 includes routers lazily: ``app.routes`` holds an opaque
    wrapper per ``include_router`` call rather than the routes themselves, so
    a walk that stops at the top level sees four routes on an app with fifty.
    The tests and the matrix both need the real list, so the recursion lives
    here rather than in each of them.
    """
    try:
        from fastapi.routing import _IncludedRouter
    except ImportError:  # pragma: no cover - older FastAPI includes eagerly
        _IncludedRouter = ()

    def walk(router, prefix: str = ""):
        for route in getattr(router, "routes", []):
            if _IncludedRouter and isinstance(route, _IncludedRouter):
                context = route.include_context
                own = getattr(router, "prefix", "") or ""
                extra = context.prefix
                if own and extra.startswith(own):
                    extra = extra[len(own):]
                yield from walk(context.included_router, prefix + extra)
                continue
            path = prefix + (getattr(route, "path", "") or "")
            methods = getattr(route, "methods", None)
            if methods is None:
                yield "MOUNT", path or "/", route
                continue
            for method in sorted(methods):
                if method in ("HEAD", "OPTIONS"):
                    continue
                yield method, path, route

    return list(walk(app.router))


def route_pairs(app: FastAPI) -> list[tuple[str, str]]:
    """The (METHOD, PATH) pairs, mounts excluded. The route table dump."""
    return sorted({(m, p) for m, p, _ in iter_app_routes(app) if m != "MOUNT"})


# --------------------------------------------------------------- the check

class NoKeyRefused(HTTPException):
    """403 for a signed-in manager with no stored Anthropic key.

    Its own class so the handler below can serve the flat body section 7.4 of
    the spec names, with ``error``, ``detail`` and ``remediation_url`` at the
    top level rather than nested under FastAPI's ``detail``. The UI prints
    ``detail`` verbatim, so the shape is part of the contract.
    """

    def __init__(self) -> None:
        super().__init__(status_code=403, detail=keys.NO_KEY_DETAIL)


def _refuse(status: int, detail: Any) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def enforce_policy(request: Request, response: Response) -> None:
    """One dependency, one table. Runs ahead of every route on the app.

    Order: resolve the session, apply CSRF, then the tier, then the key. CSRF
    comes before the tier so a forged cross-site POST is refused as a forgery
    rather than answered for the session it rode in on.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    method = request.method.upper()

    session = sessions.session_for_request(request, response=response)
    request.state.auth_session = session

    if method in policy.UNSAFE_METHODS and session is not None:
        presented = request.headers.get("X-CSRF-Token")
        if not sessions.csrf_matches(session, presented):
            raise _refuse(403, "csrf token missing or wrong")

    tier = policy.tier_for(method, path, dict(request.path_params or {}))
    if tier is None:
        # Fail closed. An unclassified route is a bug, and the matrix test
        # names it on the commit that adds it; until then it is operator only
        # rather than open.
        tier = policy.OPERATOR
    if tier == policy.BEARER:
        # The Mac ASR worker compares a shared secret inside the handler.
        return
    if tier == policy.ANONYMOUS:
        return

    anon_owner = settings.anon_is_owner()
    if session is None:
        if anon_owner:
            # The owner's own machine, and every CLI and test that builds an
            # app without configuring sign-in. This is the deployment saying
            # that an unauthenticated caller is the operator.
            return
        raise _refuse(401, "sign in with Google to use this")
    if tier == policy.OPERATOR and not session.user.is_operator:
        raise _refuse(403, (
            f"{path} is an operator route. It reads or writes this "
            f"deployment's own infrastructure, and this account is signed in "
            f"as {session.user.email}, which is not the operator."
        ))
    if policy.needs_key(method, path) and not session.user.is_operator:
        # 403 and not 401: the caller is authenticated and the thing missing
        # is a resource. There is no fallback. Not to ANTHROPIC_API_KEY from
        # the server environment, not to the operator's CLI login, not to a
        # shared key. A fallback here would mean the operator paying for a
        # stranger's chat and finding out on an invoice.
        caller_id = sessions.user_id_for(session.user.sub, is_operator=False)
        if not keys.has_key(caller_id):
            raise NoKeyRefused


# ------------------------------------------------------------ the key check

def verify_key(value: str) -> tuple[bool, str]:
    """One minimal call to Anthropic, to confirm a pasted key works.

    Returns whether it worked and the provider's own words when it did not.
    The text is shown verbatim and the key is not stored on a failure.
    """
    if (os.environ.get(VERIFY_ENV) or "").strip() in ("0", "false", "no"):
        return True, ""
    try:
        import anthropic

        from fpl_edge.config import CHAT_MODEL

        client = anthropic.Anthropic(api_key=value)
        client.messages.create(model=CHAT_MODEL, max_tokens=1,
                               messages=[{"role": "user", "content": "hi"}])
    except Exception as exc:  # noqa: BLE001 - the provider's class is the answer
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


# ---------------------------------------------------------------- the router

def _me_payload(request: Request) -> dict[str, Any]:
    session = getattr(request.state, "auth_session", None)
    if session is None:
        return {
            "signed_in": False,
            "sign_in_available": settings.sign_in_configured(),
            "anon_is_owner": settings.anon_is_owner(),
        }
    state = keys.state(sessions.user_id_for(session.user.sub,
                                            is_operator=session.user.is_operator))
    return {
        "signed_in": True,
        "sign_in_available": True,
        "anon_is_owner": False,
        "email": session.user.email,
        "is_operator": session.user.is_operator,
        "key_set": state.set,
        "last4": state.last4,
        "entry_id": session.user.entry_id,
    }


def build_router() -> APIRouter:
    """The seven routes. Built by a function so a test can mount them alone."""
    router = APIRouter(tags=["auth"])

    @router.get("/auth/google/start")
    def google_start(request: Request, next: str = "/") -> RedirectResponse:
        """Mint the handshake and send the browser to Google.

        A 302 rather than a JSON body with a URL in it: the sign-in button is
        a link, and a redirect keeps the whole flow in the browser's own
        navigation history where the back button behaves.
        """
        if not settings.sign_in_configured():
            return RedirectResponse(url="/?auth_error=not_configured",
                                    status_code=302)
        handshake = oauth.new_handshake(next)
        response = RedirectResponse(url=oauth.authorization_url(handshake),
                                    status_code=302)
        sessions.set_handshake_cookie(response, handshake.to_cookie_value())
        return response

    @router.get("/auth/google/callback")
    def google_callback(request: Request, code: str = "", state: str = "",
                        error: str = "") -> RedirectResponse:
        """Verify the return from Google and issue the session.

        Every failure is a redirect to ``/?auth_error=<class>``, never a stack
        trace and never the provider's raw text, so the shell prints one of
        the sentences in ``oauth.ERROR_MESSAGES`` and the page stays the page.
        """
        handshake = oauth.Handshake.from_cookie_value(
            sessions.unsign(request.cookies.get(sessions.HANDSHAKE_COOKIE)))
        if handshake is None or handshake.age_s() > sessions.HANDSHAKE_MAX_AGE_S:
            return _auth_error("handshake_expired")
        if error:
            return _auth_error("consent_declined" if error == "access_denied"
                               else "provider_error")
        if not oauth.state_matches(handshake, state):
            client = request.client.host if request.client else "unknown"
            # Logged with the address because it is either a stale tab or a
            # forged callback, and the two are told apart by how often it
            # happens from one place.
            log.warning("oauth state mismatch from %s", client)
            return _auth_error("state_mismatch")
        try:
            tokens = oauth.exchange_code(code, handshake.code_verifier)
            identity = oauth.verify_id_token(tokens["id_token"],
                                             nonce=handshake.nonce)
        except oauth.OAuthError as exc:
            log.warning("google sign-in refused: %s", exc.error_class)
            return _auth_error(exc.error_class)
        except settings.AuthNotConfigured:
            return _auth_error("not_configured")

        with sessions.connect() as conn:
            user = sessions.upsert_user(
                conn, sub=identity.sub, email=identity.email,
                is_operator=oauth.is_operator(identity.email))
            sid, csrf, _ = sessions.create_session(
                conn, sub=user.sub,
                user_agent=request.headers.get("user-agent"))
            sessions.purge_expired(conn)

        response = RedirectResponse(url=handshake.next_path, status_code=302)
        sessions.set_session_cookies(response, sid, csrf)
        # The handshake has done its work; leaving it set would hand the next
        # sign-in a stale state to compare against.
        response.set_cookie(sessions.HANDSHAKE_COOKIE, "", max_age=0,
                            httponly=True, secure=settings.cookie_secure(),
                            samesite="lax", path="/auth")
        return response

    @router.post("/auth/logout")
    def logout(request: Request) -> Response:
        """Revoke this session. 204 when there was nothing to revoke.

        Other sessions for the same account are untouched, because signing
        out of a phone should not sign out the laptop. Signing out everywhere
        is a separate action on the Account tab.
        """
        session = getattr(request.state, "auth_session", None)
        if session is not None:
            with sessions.connect() as conn:
                sessions.delete_session(conn, session.sid)
        response = Response(status_code=204)
        sessions.clear_cookies(response)
        return response

    @router.get("/api/me")
    def me(request: Request) -> JSONResponse:
        """Who the caller is, for the shell header and the sign-in button."""
        return JSONResponse(_me_payload(request))

    # -- the per-user Anthropic key ----------------------------------------

    @router.get("/api/account/key")
    def get_key(request: Request,
                user: UserContext = Depends(current_user)) -> JSONResponse:
        return JSONResponse(_key_state(request).to_dict())

    @router.put("/api/account/key")
    def put_key(request: Request,
                payload: Annotated[dict[str, Any], Body()],
                user: UserContext = Depends(current_user)) -> JSONResponse:
        """Store or rotate this account's key.

        The body is a plain dict rather than a pydantic model for the reason
        ``routes_account.py`` already records for the FPL cookie: a validation
        error would echo the body back in the 422 detail, and the body is the
        key.
        """
        user_id = _key_user_id(request)
        pasted = payload.get("key") if isinstance(payload, dict) else None
        try:
            value = keys.validate(pasted)
        except keys.KeyInvalid as exc:
            raise _refuse(400, str(exc)) from None
        if not keys.key_storage_configured():
            raise _refuse(503, (
                "this deployment cannot store a key yet: the key encryption "
                "secret is not set. DEPLOYMENT.md section 13.3 names it."
            ))
        ok, detail = verify_key(value)
        if not ok:
            raise _refuse(400, (
                f"Anthropic refused that key, so nothing was stored. "
                f"{detail}"
            ))
        state = keys.store(user_id, value)
        del value
        return JSONResponse(state.to_dict())

    @router.delete("/api/account/key")
    def delete_key(request: Request,
                   user: UserContext = Depends(current_user)) -> JSONResponse:
        """Remove the stored key. It does not sign the account out.

        Revoking the key at ``console.anthropic.com`` is a separate act, and
        the Account tab says so, because deleting this row does nothing about
        a key that has already been copied somewhere else.
        """
        return JSONResponse(keys.remove(_key_user_id(request)).to_dict())

    return router


def _auth_error(error_class: str) -> RedirectResponse:
    response = RedirectResponse(url=f"/?auth_error={error_class}",
                                status_code=302)
    response.set_cookie(sessions.HANDSHAKE_COOKIE, "", max_age=0,
                        httponly=True, secure=settings.cookie_secure(),
                        samesite="lax", path="/auth")
    return response


def _key_user_id(request: Request) -> str:
    """The user id the key routes act on.

    A signed-in caller acts on their own row. An unauthenticated caller only
    reaches here when the deployment answers anonymous requests as the
    operator, and then the row is the operator's, under the reserved owner id
    that already names the operator's directory.
    """
    from fpl_edge.platform.users import OWNER_USER_ID

    session = getattr(request.state, "auth_session", None)
    if session is None:
        return OWNER_USER_ID
    return sessions.user_id_for(session.user.sub,
                                is_operator=session.user.is_operator)


def _key_state(request: Request) -> keys.KeyState:
    try:
        return keys.state(_key_user_id(request))
    except Exception:  # noqa: BLE001 - an unreadable store is "nothing stored"
        return keys.NOT_SET


# ------------------------------------------------------------------ wiring

def install_auth(app: FastAPI) -> FastAPI:
    """Add the policy check, the auth routes, and the gated schema routes.

    Called before the static mount in ``create_app``, because that mount is a
    catch-all matched in order and a router added after it answers 404.
    """
    if getattr(app.state, "auth_installed", False):
        return app
    # Order is load bearing and silent when wrong. FastAPI copies the parent
    # router's dependency list into each include_router call, so a dependency
    # appended after a router was included never reaches that router's routes
    # and every one of them answers 200 to anybody. Fail here instead.
    from fastapi.routing import APIRoute

    already = [p for m, p, r in iter_app_routes(app)
               if m != "MOUNT" and isinstance(r, APIRoute)]
    if already:
        raise RuntimeError(
            f"install_auth must run before any router is included; this app "
            f"already serves {len(already)} routes ({already[0]} among them), "
            f"and the access matrix would not reach them."
        )
    app.router.dependencies.append(Depends(enforce_policy))
    app.add_exception_handler(NoKeyRefused, _no_key_handler)
    app.include_router(build_router())
    _install_schema_routes(app)
    app.state.auth_installed = True
    return app


async def _no_key_handler(request: Request, exc: Exception) -> JSONResponse:
    del request, exc
    return JSONResponse(keys.NO_KEY_BODY, status_code=403)


def _install_schema_routes(app: FastAPI) -> None:
    """Re-add /docs, /redoc and /openapi.json as ordinary routes.

    FastAPI adds its own three with ``app.add_route``, which produces plain
    Starlette routes that carry no dependencies, so the application wide check
    never runs on them and the schema that names every operator route stays
    public. ``create_app`` passes ``docs_url=None`` and the two siblings, and
    these three take their place inside the check.
    """
    from fastapi.openapi.docs import (
        get_redoc_html,
        get_swagger_ui_html,
        get_swagger_ui_oauth2_redirect_html,
    )

    served = {p for m, p, _ in iter_app_routes(app) if m == "GET"}
    if {"/openapi.json", "/docs", "/redoc"} & served:
        # A scratch app built as plain FastAPI() already serves its own three.
        # Adding a second set would shadow nothing and confuse the route table.
        return

    router = APIRouter(include_in_schema=False)

    @router.get("/openapi.json")
    def openapi_schema() -> JSONResponse:
        return JSONResponse(app.openapi())

    @router.get("/docs")
    def swagger_ui():
        return get_swagger_ui_html(
            openapi_url="/openapi.json", title=f"{app.title} docs",
            oauth2_redirect_url="/docs/oauth2-redirect")

    @router.get("/docs/oauth2-redirect")
    def swagger_ui_redirect():
        return get_swagger_ui_oauth2_redirect_html()

    @router.get("/redoc")
    def redoc():
        return get_redoc_html(openapi_url="/openapi.json",
                              title=f"{app.title} docs")

    app.include_router(router)
