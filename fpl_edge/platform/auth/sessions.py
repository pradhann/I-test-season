"""The auth database, the session row, and the three cookies.

Storage is one SQLite file (``settings.auth_db_path``) with WAL enabled, and
it holds three tables: ``users``, ``sessions`` and ``user_keys``. The keys
table is written by :mod:`fpl_edge.platform.auth.keys` and declared here so
one function owns the schema.

Two properties are worth naming because they are the reason for the shapes
below.

* **The session row holds ``sha256(sid)``, never the sid.** A copy of this
  file is then a list of who signed in, not a bag of live cookies.
* **The cookie value is ``sid.signature``**, signed with the session secret,
  so a forged or truncated sid is rejected before any database read. Rotating
  the secret invalidates every cookie at once, which is the intended blast
  radius of a suspected leak.

Cookie names are ``itest_session`` (HttpOnly), ``itest_csrf`` (readable by the
page, which has to echo it in a header) and ``itest_oauth`` (the ten minute
sign-in handshake, scoped to ``/auth``).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fpl_edge.platform.auth import settings

UTC = dt.UTC

SESSION_COOKIE = "itest_session"
CSRF_COOKIE = "itest_csrf"
HANDSHAKE_COOKIE = "itest_oauth"

#: Absolute session lifetime. After it, one click through Google, which does
#: not re-prompt for consent.
SESSION_MAX_AGE_S = 30 * 24 * 3600

#: A session older than this gets the same sid with a later expiry and a
#: re-issued cookie, so an active manager is never signed out mid gameweek.
SESSION_RENEW_AFTER_S = 24 * 3600

#: The whole budget for one sign-in handshake.
HANDSHAKE_MAX_AGE_S = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  sub           TEXT PRIMARY KEY,
  email         TEXT NOT NULL,
  is_operator   INTEGER NOT NULL DEFAULT 0,
  entry_id      INTEGER,
  created_utc   TEXT NOT NULL,
  last_seen_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  sid_hash     TEXT PRIMARY KEY,
  sub          TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
  csrf_hash    TEXT NOT NULL,
  created_utc  TEXT NOT NULL,
  expires_utc  TEXT NOT NULL,
  user_agent   TEXT
);
CREATE INDEX IF NOT EXISTS sessions_sub ON sessions(sub);
-- Keyed by user id, which is what names the per-user directory, so the
-- operator's key is reachable from the machine the server runs on before any
-- Google sign-in has happened. No foreign key for the same reason.
CREATE TABLE IF NOT EXISTS user_keys (
  user_id      TEXT PRIMARY KEY,
  ciphertext   BLOB NOT NULL,
  nonce        BLOB NOT NULL,
  last4        TEXT NOT NULL,
  key_version  INTEGER NOT NULL,
  created_utc  TEXT NOT NULL,
  rotated_utc  TEXT
);
"""


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def _iso(when: dt.datetime) -> str:
    return when.astimezone(UTC).isoformat()


def _parse(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@contextmanager
def connect(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """An auth database connection with the schema applied, committed on exit.

    The file and its parent are created on first use. WAL so a read during a
    write does not block, which matters because every request reads this file
    and only a sign-in writes it.
    """
    db = Path(path) if path is not None else settings.auth_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------- identity

@dataclass(frozen=True)
class UserRow:
    """One signed-in person. Three columns of identity and two timestamps."""

    sub: str
    email: str
    is_operator: bool
    entry_id: int | None

    @property
    def user_id(self) -> str:
        return user_id_for(self.sub, is_operator=self.is_operator)


def user_id_for(sub: str, *, is_operator: bool) -> str:
    """The per-user directory name for a Google subject.

    The operator maps to the reserved owner id, so there is one definition of
    what the operator can do and one directory holding the operator's plan,
    briefing and transcripts. Everyone else gets ``g`` plus their subject when
    that is digits, which is what Google issues and what keeps the directory
    listing readable for support, and ``g`` plus a digest otherwise, so a
    subject with a character a directory name cannot hold still maps to
    exactly one directory.
    """
    from fpl_edge.platform.users import OWNER_USER_ID

    if is_operator:
        return OWNER_USER_ID
    raw = str(sub or "").strip()
    if raw.isdigit() and 0 < len(raw) < 64:
        return f"g{raw}"
    return "g" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def upsert_user(conn: sqlite3.Connection, *, sub: str, email: str,
                is_operator: bool) -> UserRow:
    """Create or refresh the row for a verified Google subject.

    ``email`` is overwritten on every sign-in because a Google account can
    change its address and the header should follow it. ``is_operator`` is
    stored rather than compared at read time so that changing the operator
    address later does not silently demote a running session.
    """
    now = _iso(_now())
    conn.execute(
        "INSERT INTO users (sub, email, is_operator, created_utc, last_seen_utc) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(sub) DO UPDATE SET email=excluded.email, "
        "is_operator=excluded.is_operator, last_seen_utc=excluded.last_seen_utc",
        (sub, email, 1 if is_operator else 0, now, now),
    )
    row = conn.execute("SELECT * FROM users WHERE sub = ?", (sub,)).fetchone()
    return UserRow(sub=row["sub"], email=row["email"],
                   is_operator=bool(row["is_operator"]),
                   entry_id=row["entry_id"])


# ------------------------------------------------------------------ signing

def _secret_bytes() -> bytes:
    return (settings.setting("session_secret", required=True) or "").encode("utf-8")


def sign(value: str) -> str:
    """``value.signature``, the form both the session and handshake cookies
    take. base64url with the padding stripped, so nothing needs quoting."""
    mac = hmac.new(_secret_bytes(), value.encode("utf-8"), hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")
    return f"{value}.{sig}"


def unsign(cookie_value: str | None) -> str | None:
    """The value a cookie carries, or None when the signature does not hold."""
    if not cookie_value or "." not in cookie_value:
        return None
    value, _, sig = cookie_value.rpartition(".")
    if not value:
        return None
    try:
        expected = sign(value).rpartition(".")[2]
    except Exception:  # noqa: BLE001 - an unconfigured secret is "no session"
        return None
    return value if hmac.compare_digest(sig, expected) else None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ session

@dataclass(frozen=True)
class SessionRow:
    """A live session and the person behind it."""

    sid: str
    user: UserRow
    csrf_hash: str
    created: dt.datetime
    expires: dt.datetime


def create_session(conn: sqlite3.Connection, *, sub: str,
                   user_agent: str | None = None) -> tuple[str, str, dt.datetime]:
    """Mint a session. Returns the raw sid, the raw CSRF token and the expiry.

    Both raw values leave this function once, into the two ``Set-Cookie``
    headers. The row keeps their digests.
    """
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = _now()
    expires = now + dt.timedelta(seconds=SESSION_MAX_AGE_S)
    conn.execute(
        "INSERT INTO sessions (sid_hash, sub, csrf_hash, created_utc, "
        "expires_utc, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
        (_hash(sid), sub, _hash(csrf), _iso(now), _iso(expires),
         (user_agent or "")[:300] or None),
    )
    return sid, csrf, expires


def read_session(conn: sqlite3.Connection, sid: str) -> SessionRow | None:
    """The session behind a raw sid, or None. An expired row reads as absent."""
    row = conn.execute(
        "SELECT s.*, u.email, u.is_operator, u.entry_id FROM sessions s "
        "JOIN users u ON u.sub = s.sub WHERE s.sid_hash = ?",
        (_hash(sid),),
    ).fetchone()
    if row is None:
        return None
    expires = _parse(row["expires_utc"])
    if expires is None or expires <= _now():
        return None
    return SessionRow(
        sid=sid,
        user=UserRow(sub=row["sub"], email=row["email"],
                     is_operator=bool(row["is_operator"]),
                     entry_id=row["entry_id"]),
        csrf_hash=row["csrf_hash"],
        created=_parse(row["created_utc"]) or _now(),
        expires=expires,
    )


def remaining_seconds(session: SessionRow) -> int:
    """How long this session still has, from the row's own absolute expiry."""
    return max(0, int((session.expires - _now()).total_seconds()))


def needs_reissue(session: SessionRow) -> bool:
    """Whether the browser's copy of the two cookies should be refreshed.

    The cookies were set with a thirty day Max-Age when the session was
    created, and a browser that has held them for a day is a day closer to
    dropping them than the server row is to expiring. Re-issuing with the
    remaining lifetime keeps the two clocks together, so an active manager is
    never signed out by a cookie the server still considers live.

    The expiry itself is absolute and is never pushed out. After thirty days
    the manager signs in again, which costs one click because Google does not
    re-prompt for consent.
    """
    return (_now() - session.created).total_seconds() >= SESSION_RENEW_AFTER_S


def delete_session(conn: sqlite3.Connection, sid: str) -> int:
    """Revoke one session. Other sessions for the same person are untouched,
    because signing out of a phone should not sign out the laptop."""
    cur = conn.execute("DELETE FROM sessions WHERE sid_hash = ?", (_hash(sid),))
    return int(cur.rowcount or 0)


def delete_sessions_for(conn: sqlite3.Connection, sub: str) -> int:
    """Sign out everywhere, offered on the Account tab."""
    cur = conn.execute("DELETE FROM sessions WHERE sub = ?", (sub,))
    return int(cur.rowcount or 0)


def purge_expired(conn: sqlite3.Connection) -> int:
    """Hygiene, never correctness: an expired row already reads as absent."""
    cur = conn.execute("DELETE FROM sessions WHERE expires_utc <= ?",
                       (_iso(_now()),))
    return int(cur.rowcount or 0)


def csrf_matches(session: SessionRow, presented: str | None) -> bool:
    """Whether a header value is the token this session was issued."""
    if not presented:
        return False
    return hmac.compare_digest(_hash(presented), session.csrf_hash)


# ------------------------------------------------------------------ cookies

def _cookie_kwargs(max_age: int, *, http_only: bool, path: str) -> dict[str, Any]:
    return {
        "max_age": max_age,
        "httponly": http_only,
        "secure": settings.cookie_secure(),
        "samesite": "lax",
        "path": path,
    }


def set_session_cookies(response, sid: str, csrf: str, *,
                        max_age: int = SESSION_MAX_AGE_S) -> None:
    """The two cookies a signed-in browser carries.

    SameSite is Lax rather than Strict because the return from Google and any
    link into the app from outside are top-level navigations, and Strict would
    drop the cookie on the first one and show a signed-in person a signed-out
    page. Lax already withholds the cookie from a cross-site POST, which is
    the case CSRF cares about.
    """
    response.set_cookie(SESSION_COOKIE, sign(sid),
                        **_cookie_kwargs(max_age, http_only=True, path="/"))
    # Readable by the page on purpose: the SPA echoes it in X-CSRF-Token.
    response.set_cookie(CSRF_COOKIE, csrf,
                        **_cookie_kwargs(max_age, http_only=False, path="/"))


def set_handshake_cookie(response, value: str) -> None:
    """The signed sign-in handshake, scoped to ``/auth`` for ten minutes."""
    response.set_cookie(HANDSHAKE_COOKIE, sign(value),
                        **_cookie_kwargs(HANDSHAKE_MAX_AGE_S, http_only=True,
                                         path="/auth"))


def clear_cookies(response) -> None:
    """Expire all three. Called by logout and by a failed handshake."""
    for name, path in ((SESSION_COOKIE, "/"), (CSRF_COOKIE, "/"),
                       (HANDSHAKE_COOKIE, "/auth")):
        response.set_cookie(name, "", max_age=0, httponly=name != CSRF_COOKIE,
                            secure=settings.cookie_secure(), samesite="lax",
                            path=path)


def session_for_request(request, path: Path | str | None = None, *,
                        response=None) -> SessionRow | None:
    """The session a request carries, or None. The one reader of the cookie.

    When ``response`` is given and the session is a day old, both cookies are
    re-issued on it with the session's remaining lifetime. The CSRF cookie is
    re-issued from the value the request itself presented, verified against
    the stored digest first, because the server holds the digest and never
    the token.
    """
    # getattr rather than a bare attribute read: a job, a CLI command and the
    # MCP server all resolve a context with no request at all, and a caller
    # that hands in something which is not a Starlette request is "no
    # session" rather than an AttributeError one layer down.
    cookies = getattr(request, "cookies", None) or {}
    raw = cookies.get(SESSION_COOKIE)
    sid = unsign(raw)
    if not sid:
        return None
    db = Path(path) if path is not None else settings.auth_db_path()
    if not db.exists():
        # No store means no session. Reading must not create the database:
        # a read that did would leave a SQLite file in the repo tree the
        # first time anybody presented a cookie to a server that has never
        # signed anyone in.
        return None
    try:
        with connect(path) as conn:
            session = read_session(conn, sid)
    except sqlite3.Error:
        # An unreadable auth database is "no session", never a 500 on every
        # route at once. The sign-in route reports the real failure.
        return None
    if session is None:
        return None
    if response is not None and needs_reissue(session):
        presented = request.cookies.get(CSRF_COOKIE)
        left = remaining_seconds(session)
        response.set_cookie(SESSION_COOKIE, sign(sid),
                            **_cookie_kwargs(left, http_only=True, path="/"))
        if csrf_matches(session, presented):
            response.set_cookie(CSRF_COOKIE, presented,
                                **_cookie_kwargs(left, http_only=False, path="/"))
    return session
