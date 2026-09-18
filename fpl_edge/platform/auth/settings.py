"""Every deployment variable the auth layer reads, in one place.

Two naming sets reached this workstream. ``docs/platform/AUTH.md`` section 2.5
names ``GOOGLE_REDIRECT_URI``, ``SESSION_SECRET``, ``USER_KEY_ENC_SECRET`` and
``OPERATOR_EMAIL``; the build brief names ``OAUTH_REDIRECT_URL``,
``SESSION_SIGNING_KEY``, ``KEY_ENCRYPTION_KEY`` and ``OWNER_EMAIL`` for the
same four values. Both are accepted, the spec name is read first, and
``DEPLOYMENT.md`` section 13.3 lists both rows against one description. A
deployment that sets one of a pair is configured; a deployment that sets both
to different values is a mistake, so :func:`setting` raises rather than
picking a winner.

Nothing here caches. ``fpl_edge.config.secret`` reads the environment first
and ``.env`` second on every call, and the tests move these values with
``monkeypatch.setenv`` between requests.
"""

from __future__ import annotations

import os
from pathlib import Path

from fpl_edge.config import secret

#: Logical name -> the environment names that carry it, spec name first.
ALIASES: dict[str, tuple[str, ...]] = {
    "client_id": ("GOOGLE_CLIENT_ID",),
    "client_secret": ("GOOGLE_CLIENT_SECRET",),
    "redirect_uri": ("GOOGLE_REDIRECT_URI", "OAUTH_REDIRECT_URL"),
    "session_secret": ("SESSION_SECRET", "SESSION_SIGNING_KEY"),
    "key_secret": ("USER_KEY_ENC_SECRET", "KEY_ENCRYPTION_KEY"),
    "key_secret_prev": ("USER_KEY_ENC_SECRET_PREV", "KEY_ENCRYPTION_KEY_PREV"),
    "operator_email": ("OPERATOR_EMAIL", "OWNER_EMAIL"),
    "public_entry_id": ("PUBLIC_ENTRY_ID",),
}

#: Where the sessions and the encrypted keys live. A separate SQLite file
#: beside the warehouse, never inside it: the warehouse is append-only
#: analytics behind a single writer, and a row written on every sign-in would
#: contend for that write lock against the pipelines.
AUTH_DB_ENV = "AUTH_DB"
AUTH_DB_DEFAULT = "auth/auth.sqlite3"

#: Set to 1 to keep answering an unauthenticated request as the operator,
#: which is how the owner's Mac runs today and how the whole suite runs. Set
#: to 0 to force the signed-out route set even before a Google client exists,
#: which is what the matrix test drives.
ANON_IS_OWNER_ENV = "FPL_EDGE_ANON_IS_OWNER"

#: Overrides the Secure flag on the three cookies. Left unset, the flag is
#: derived from the redirect URI's scheme, which Google permits as ``http``
#: for localhost only, so local development works and a deployment cannot
#: drop the flag by forgetting a second variable.
COOKIE_SECURE_ENV = "COOKIE_SECURE"


class AuthNotConfigured(RuntimeError):
    """A sign-in route was reached on a deployment with no Google client."""


def setting(name: str, *, required: bool = False) -> str | None:
    """One configured value, by its logical name. None when nothing sets it."""
    names = ALIASES[name]
    found: dict[str, str] = {}
    for env_name in names:
        value = secret(env_name, required=False)
        if value:
            found[env_name] = value
    if len(found) > 1 and len(set(found.values())) > 1:
        pair = ", ".join(f"{k}={'<set>'}" for k in found)
        raise RuntimeError(
            f"{pair} are two names for the same value and they disagree. Set "
            f"one of {' or '.join(names)} and remove the other."
        )
    for env_name in names:
        if env_name in found:
            return found[env_name]
    if required:
        raise AuthNotConfigured(
            f"{names[0]} is not set, so Google sign-in cannot run. "
            f"DEPLOYMENT.md section 13.7 lists the owner's steps."
        )
    return None


def sign_in_configured() -> bool:
    """True when a Google OAuth client and a session secret both exist.

    The whole sign-in surface hangs off this. A deployment with no client id
    cannot have a signed-in user at all, so the sign-in button is not drawn
    and ``/auth/google/start`` answers with the reason rather than sending a
    browser to Google with an empty ``client_id``.
    """
    return bool(setting("client_id") and setting("session_secret"))


def anon_is_owner() -> bool:
    """Whether a request with no session is answered as the operator.

    The flag decides, and when it is unset the answer is whether sign-in
    exists at all. A deployment with no Google client has no way for anyone
    to sign in, so treating its callers as the operator is the behaviour the
    server already has and the only one that leaves the Mac and the CLI
    working. The moment the owner configures a client, the default flips to
    signed-out and the anonymous route set from the matrix applies.
    """
    raw = (os.environ.get(ANON_IS_OWNER_ENV) or "").strip()
    if raw:
        return raw not in ("0", "false", "False", "no")
    return not sign_in_configured()


def operator_email() -> str | None:
    """The one Google address that owns this deployment, case-folded."""
    value = setting("operator_email")
    return value.strip().casefold() if value else None


def public_entry_id() -> int | None:
    """The FPL entry an anonymous visitor's panels read, or None."""
    raw = setting("public_entry_id")
    if not raw:
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    return value if value > 0 else None


def cookie_secure() -> bool:
    """Whether the three cookies carry Secure."""
    raw = (os.environ.get(COOKIE_SECURE_ENV) or "").strip()
    if raw:
        return raw not in ("0", "false", "False", "no")
    redirect = setting("redirect_uri") or ""
    return not redirect.startswith("http://")


def auth_db_path() -> Path:
    """The SQLite file holding users, sessions and encrypted keys."""
    from fpl_edge.platform.users import data_root

    configured = (os.environ.get(AUTH_DB_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return data_root() / AUTH_DB_DEFAULT
