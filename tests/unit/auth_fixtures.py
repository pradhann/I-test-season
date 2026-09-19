"""Shared setup for the four auth suites: a configured deployment, and a
signed-in browser.

Nothing here reaches the network and nothing here is a real credential. The
Google client id, the client secret, the session secret and the encryption
secret are literals chosen to be obviously fake, and every one of them is set
with ``monkeypatch.setenv`` so it exists for one test only.

``sign_in`` writes the ``users`` and ``sessions`` rows the callback would have
written and sets the two cookies on the test client. It goes through
``fpl_edge.platform.auth.sessions``, the same functions the callback uses, so
a test signed in this way is signed in the way the server means it.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

OPERATOR_EMAIL = "operator@example.test"
OTHER_EMAIL = "someone.else@example.test"

CLIENT_ID = "test-client.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-not-a-real-secret"
REDIRECT_URI = "http://localhost:8321/auth/google/callback"
SESSION_SECRET = "test-session-secret-do-not-use-anywhere-real"
KEY_SECRET = base64.b64encode(b"test-key-encryption-secret-32byt").decode()

#: A key-shaped string that is not a key. Every leak assertion looks for it.
SENTINEL = "sk-ant-api03-ZZZTESTSENTINELZZZ0000000000"


@dataclass(frozen=True)
class SignedIn:
    """What a test needs after signing a browser in."""

    sub: str
    sid: str
    csrf: str
    email: str
    is_operator: bool
    user_id: str

    def headers(self) -> dict[str, str]:
        """The CSRF header every state changing request carries."""
        return {"X-CSRF-Token": self.csrf}


def configure(monkeypatch, tmp_path: Path, *, anon_is_owner: bool = False,
              operator_email: str = OPERATOR_EMAIL,
              sign_in_available: bool = True) -> Path:
    """Point the whole auth layer at ``tmp_path`` and return the auth db path.

    ``sign_in_available=False`` leaves the Google client unset, which is the
    state of a deployment the owner has not configured yet.
    """
    auth_db = tmp_path / "auth" / "auth.sqlite3"
    monkeypatch.setenv("FPL_EDGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTH_DB", str(auth_db))
    monkeypatch.setenv("SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setenv("USER_KEY_ENC_SECRET", KEY_SECRET)
    monkeypatch.setenv("OPERATOR_EMAIL", operator_email)
    monkeypatch.setenv("COOKIE_SECURE", "0")
    # The store-time check against Anthropic is a network call. Every test
    # that cares about it turns it back on and replaces the function.
    monkeypatch.setenv("FPL_EDGE_VERIFY_USER_KEY", "0")
    if sign_in_available:
        monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
        monkeypatch.setenv("GOOGLE_REDIRECT_URI", REDIRECT_URI)
    else:
        for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                     "GOOGLE_REDIRECT_URI"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FPL_EDGE_ANON_IS_OWNER", "1" if anon_is_owner else "0")
    return auth_db


def sign_in(client, *, email: str = OTHER_EMAIL, is_operator: bool = False,
            sub: str | None = None) -> SignedIn:
    """Write the rows the callback writes and set the two cookies."""
    from fpl_edge.platform.auth import sessions

    subject = sub or ("100000000000000000001" if is_operator
                      else "200000000000000000002")
    with sessions.connect() as conn:
        user = sessions.upsert_user(conn, sub=subject, email=email,
                                    is_operator=is_operator)
        sid, csrf, _ = sessions.create_session(conn, sub=user.sub,
                                               user_agent="pytest")
    client.cookies.set(sessions.SESSION_COOKIE, sessions.sign(sid))
    client.cookies.set(sessions.CSRF_COOKIE, csrf)
    return SignedIn(sub=user.sub, sid=sid, csrf=csrf, email=email,
                    is_operator=is_operator, user_id=user.user_id)


def sign_out(client) -> None:
    """Drop the cookies without calling the route."""
    from fpl_edge.platform.auth import sessions

    client.cookies.delete(sessions.SESSION_COOKIE)
    client.cookies.delete(sessions.CSRF_COOKIE)
