"""Google OAuth 2.0, authorization code with PKCE, in one file.

No OAuth library. The flow is one redirect and one POST, and the parts worth
not writing by hand are the id_token signature check and Google's key
rotation, which ``pyjwt``'s ``PyJWKClient`` already does.

What leaves this server: a redirect carrying ``state``, ``nonce`` and a PKCE
challenge, and one form POST carrying the code and the verifier. What comes
back and is kept: the subject and the verified email address. The access
token is discarded the moment the id_token verifies, because this platform
calls no Google API after sign-in, and the refresh token is never requested.

The handshake lives entirely in a signed ten minute cookie, so an abandoned
sign-in expires on its own and leaves no server side row to clean up.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from fpl_edge.platform.auth import settings

ISSUER = "https://accounts.google.com"
AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"

#: Exactly what this platform can justify on a consent screen: an id and an
#: address to display. Not profile, not anything else.
SCOPE = "openid email"

TOKEN_TIMEOUT_S = 10.0

#: The failure classes the callback redirects with. The shell reads
#: ``auth_error`` and prints the matching sentence, so no stack trace and no
#: provider text reaches the page.
ERROR_MESSAGES: dict[str, str] = {
    "handshake_expired": (
        "The sign-in took longer than ten minutes, so it was started over. "
        "Press Sign in with Google again."
    ),
    "consent_declined": (
        "Google sign-in was cancelled, so nothing was signed in."
    ),
    "provider_error": (
        "Google returned an error instead of a sign-in. Try again, and if it "
        "repeats, check that the redirect URI in the Google Console matches "
        "this address exactly."
    ),
    "state_mismatch": (
        "The sign-in did not come back to the tab that started it. Press Sign "
        "in with Google again in this tab."
    ),
    "token_invalid": (
        "Google's answer did not verify, so no account was signed in. Try "
        "again."
    ),
    "not_configured": (
        "Google sign-in is not configured on this deployment. DEPLOYMENT.md "
        "section 13.7 lists the owner's steps."
    ),
}


class OAuthError(Exception):
    """A named failure class from the list above, carried to the redirect."""

    def __init__(self, error_class: str, detail: str = ""):
        self.error_class = error_class
        self.detail = detail
        super().__init__(f"{error_class}: {detail}" if detail else error_class)


@dataclass(frozen=True)
class Handshake:
    """The four generated values and the sanitised return path.

    ``code_verifier`` never leaves this server except in the token exchange,
    and the cookie that carries it is HttpOnly and signed.
    """

    state: str
    nonce: str
    code_verifier: str
    next_path: str
    issued_at: int

    def age_s(self) -> float:
        """Seconds since this handshake was minted.

        The cookie's own Max-Age is the browser's copy of this deadline and a
        cookie is client controlled state, so the server checks the age it
        wrote into the signed payload rather than trusting that the browser
        dropped it on time.
        """
        return max(0.0, time.time() - float(self.issued_at))

    def to_cookie_value(self) -> str:
        body = json.dumps({"s": self.state, "n": self.nonce,
                           "v": self.code_verifier, "p": self.next_path,
                           "t": self.issued_at}, separators=(",", ":"))
        return base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")

    @classmethod
    def from_cookie_value(cls, value: str | None) -> Handshake | None:
        if not value:
            return None
        padded = value + "=" * (-len(value) % 4)
        try:
            body = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            return cls(state=str(body["s"]), nonce=str(body["n"]),
                       code_verifier=str(body["v"]), next_path=str(body["p"]),
                       issued_at=int(body["t"]))
        except Exception:  # noqa: BLE001 - an unreadable handshake is an expired one
            return None


def safe_next(raw: str | None) -> str:
    """A same-origin path, or ``/``.

    Anything that is not a path beginning with a single slash is replaced.
    An open redirect here would let a phishing page bounce a real sign-in to
    an attacker's host.
    """
    value = (raw or "").strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or "\n" in value or "\r" in value:
        return "/"
    return value


def new_handshake(next_path: str | None = None) -> Handshake:
    """Mint state, nonce and a PKCE verifier, all from ``secrets``."""
    return Handshake(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(64),
        next_path=safe_next(next_path),
        issued_at=int(time.time()),
    )


def code_challenge(verifier: str) -> str:
    """base64url of sha256 of the verifier, unpadded. PKCE S256."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def authorization_url(handshake: Handshake) -> str:
    """Where the browser goes next."""
    params = {
        "client_id": settings.setting("client_id", required=True),
        "redirect_uri": settings.setting("redirect_uri", required=True),
        "response_type": "code",
        "scope": SCOPE,
        "state": handshake.state,
        "nonce": handshake.nonce,
        "code_challenge": code_challenge(handshake.code_verifier),
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


def state_matches(handshake: Handshake, returned: str | None) -> bool:
    """Constant time comparison of the returned state to the cookie's."""
    return bool(returned) and hmac.compare_digest(handshake.state, str(returned))


def exchange_code(code: str, verifier: str) -> dict[str, Any]:
    """POST the code and the verifier, and return Google's token response.

    ``httpx`` with an explicit timeout, which is the client this repo already
    depends on. The tests replace ``httpx.post`` with a fake endpoint.
    """
    import httpx

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.setting("redirect_uri", required=True),
        "client_id": settings.setting("client_id", required=True),
        "client_secret": settings.setting("client_secret", required=True),
        "code_verifier": verifier,
    }
    try:
        response = httpx.post(TOKEN_ENDPOINT, data=payload,
                              timeout=TOKEN_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - the class is what the redirect carries
        raise OAuthError("provider_error",
                         f"token endpoint unreachable: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise OAuthError("provider_error",
                         f"token endpoint returned HTTP {response.status_code}")
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        raise OAuthError("provider_error", "token endpoint returned no JSON") from None
    if not isinstance(body, dict) or not body.get("id_token"):
        raise OAuthError("token_invalid", "the token response carried no id_token")
    return body


def jwks_client():
    """Google's signing keys, cached in process for an hour.

    A module level cache rather than a new client per sign-in, because
    ``PyJWKClient`` does the caching and building one per callback would fetch
    the key set on every sign-in.
    """
    global _JWKS_CLIENT
    if _JWKS_CLIENT is None:
        from jwt import PyJWKClient

        _JWKS_CLIENT = PyJWKClient(JWKS_URI, cache_keys=True, lifespan=3600)
    return _JWKS_CLIENT


_JWKS_CLIENT = None


@dataclass(frozen=True)
class GoogleIdentity:
    """Everything kept from an id_token. Two claims, nothing else."""

    sub: str
    email: str


def verify_id_token(id_token: str, *, nonce: str) -> GoogleIdentity:
    """Signature, issuer, audience, expiry, nonce and email_verified.

    A failure at any one of these raises ``token_invalid`` and no user row is
    written. The claims that are not checked here are not stored either.
    """
    import jwt

    client_id = settings.setting("client_id", required=True)
    try:
        signing_key = jwks_client().get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=client_id,
            issuer=[ISSUER, "accounts.google.com"],
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except Exception as exc:  # noqa: BLE001 - one class, and never the token text
        raise OAuthError("token_invalid", type(exc).__name__) from None
    if not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise OAuthError("token_invalid", "nonce did not match the handshake")
    if claims.get("email_verified") is not True:
        raise OAuthError("token_invalid", "the Google address is not verified")
    email = str(claims.get("email") or "").strip()
    sub = str(claims.get("sub") or "").strip()
    if not email or not sub:
        raise OAuthError("token_invalid", "the id_token carried no subject or address")
    return GoogleIdentity(sub=sub, email=email)


def is_operator(email: str) -> bool:
    """Whether a verified address is the one the deployment names as operator.

    Case-folded, and False when no operator address is configured: a server
    nobody has claimed has no operator, which is the correct state for it.
    """
    configured = settings.operator_email()
    return bool(configured) and email.strip().casefold() == configured
