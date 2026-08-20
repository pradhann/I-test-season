"""Self-renewing FPL authentication via the standard OAuth refresh grant.

FPL's web app authenticates against PingOne (the Premier League's SSO). The
browser holds two JWTs: an ``access_token`` that lives ~8 hours and a
``refresh_token`` with ``offline_access`` scope that lives ~6 months. Pasting
cookies every 8 hours is not a workflow; the refresh grant is. This module
turns a one-time paste into a self-renewing credential:

1. The manager pastes their browser's Cookie header ONCE (or just the two
   token values). ``ingest_from_cookie`` extracts and stores the tokens and the
   OAuth coordinates (issuer, client id) read from the JWT itself.
2. Every authenticated call goes through :meth:`TokenManager.access_token`,
   which returns the stored token while fresh and otherwise performs the
   documented OAuth2 ``refresh_token`` grant against the issuer's ``/token``
   endpoint -- the same request the manager's own browser makes when its token
   ages out. PingOne rotates refresh tokens, so the new pair is persisted
   back to ``.env`` atomically.

Boundaries, unchanged from the rest of this package:

* No password is ever stored, asked for, or replayed. A refresh token is
  revocable (log out of the originating session / "sign out everywhere") and
  scoped; a password is neither.
* Tokens are never logged or printed. Errors report expiry times and HTTP
  status, never the credential.
* If the grant is refused (revoked session, rotated client, or the endpoint
  demanding interaction), the failure mode is a clear instruction to paste a
  fresh browser session -- not a retry loop against an auth server.

Two facts measured against the live API on 2026-08-20, both counter-intuitive:

* **A session cookie alone no longer authenticates.** ``GET /api/my-team/{id}/``
  with the full browser Cookie header and no bearer returns 403
  ``"Authentication credentials were not provided."`` The ``FPL_SESSION_COOKIE``
  path in :mod:`fpl_edge.myteam.private` therefore cannot succeed on its own; it
  survives only so a pre-token setup fails loudly rather than silently. The
  bearer is not an optimisation, it is the only door.
* **Redemption is single-use and cross-process.** The issuer burns a refresh
  token the moment it is redeemed, so two processes refreshing concurrently
  leave one holding a spent token and receiving an HTTP 400 that is
  indistinguishable from a revoked session. This is why the refresh is guarded
  by a file lock (:meth:`TokenManager._process_lock`) and not merely a
  ``threading.Lock``.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import contextlib
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

from fpl_edge.config import ENV_PATH, load_env
from fpl_edge.ingest.http import USER_AGENT

#: Refresh when the access token has less than this long to live: a token that
#: expires mid-request-sequence is indistinguishable from a revoked one.
REFRESH_MARGIN_S = 300


class AuthNotConfiguredError(RuntimeError):
    """No refresh token stored yet. Carries the one-time setup steps."""


class RefreshRefusedError(RuntimeError):
    """The issuer rejected the refresh grant; a fresh browser paste is needed."""


def jwt_payload(token: str) -> dict:
    body = token.split(".")[1]
    body += "=" * (-len(body) % 4)
    return json.loads(base64.urlsafe_b64decode(body))


def jwt_expiry(token: str) -> dt.datetime:
    return dt.datetime.fromtimestamp(jwt_payload(token)["exp"], dt.timezone.utc)


def extract_tokens_from_cookie(cookie: str) -> tuple[str | None, str | None]:
    """Pull the two PingOne JWTs out of a pasted browser Cookie header."""

    def get(name: str) -> str | None:
        m = re.search(rf"(?:^|;\s*){name}=([^;\s]+)", cookie)
        return m.group(1) if m else None

    return get("access_token"), get("refresh_token")


def _set_env_var(text: str, key: str, value: str) -> str:
    line = f"{key}={value}"
    if re.search(rf"^{key}=", text, re.M):
        return re.sub(rf"^{key}=.*$", line, text, flags=re.M)
    return text.rstrip("\n") + "\n" + line + "\n"


@dataclass
class TokenManager:
    """Holds the token pair, refreshing and persisting as needed.

    Thread-safe: the Telegram bot and a CLI command may both want a token.
    """

    env_path: Path = ENV_PATH
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_lock", threading.Lock())

    @contextlib.contextmanager
    def _process_lock(self):
        """Serialise refreshes across PROCESSES, not just threads.

        The refresh token rotates: the issuer invalidates it the instant it is
        redeemed. The bot, the launchd DAG and a CLI command are separate
        processes, so a `threading.Lock` guards none of them from each other.
        Two of them refreshing at once means one wins, and the loser replays a
        token the issuer has already burned -- an HTTP 400 that reads exactly
        like a revoked session and sends the manager to re-paste a cookie that
        was never the problem. Observed in production, hence the flock.

        Whoever waits here re-reads `.env` afterwards (see `access_token`) and
        finds the winner's fresh token, so the second refresh never happens.
        """
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-POSIX
            yield
            return
        lock_path = self.env_path.with_name(self.env_path.name + ".lock")
        try:
            handle = open(lock_path, "a+")  # noqa: SIM115 - closed in finally
        except OSError:  # pragma: no cover - unwritable dir; degrade, never block
            yield
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    # -- storage --------------------------------------------------------------

    def _read(self) -> dict[str, str]:
        return load_env(self.env_path)

    def _persist(self, access: str, refresh: str | None) -> None:
        """Atomic-enough for a single-user dotfile: write via temp + replace."""
        text = self.env_path.read_text() if self.env_path.exists() else ""
        text = _set_env_var(text, "FPL_ACCESS_TOKEN", access)
        if refresh:
            text = _set_env_var(text, "FPL_REFRESH_TOKEN", refresh)
        tmp = self.env_path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.chmod(0o600)
        tmp.replace(self.env_path)

    # -- the useful surface ---------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self._read().get("FPL_REFRESH_TOKEN"))

    def status(self) -> str:
        """Human-readable state that never includes a token."""
        env = self._read()
        access, refresh = env.get("FPL_ACCESS_TOKEN"), env.get("FPL_REFRESH_TOKEN")
        if not refresh:
            return "not configured: no refresh token stored"
        parts = []
        now = dt.datetime.now(dt.timezone.utc)
        if access:
            exp = jwt_expiry(access)
            parts.append(
                f"access token {'expired' if exp <= now else 'valid'} "
                f"(exp {exp:%Y-%m-%d %H:%MZ})"
            )
        else:
            parts.append("no access token cached")
        rexp = jwt_expiry(refresh)
        # Not-expired is necessary but NOT sufficient: these are rotating
        # refresh tokens, so the issuer can revoke or supersede one long before
        # its own `exp`. Saying "valid" here once sent a manager to re-paste a
        # cookie that was never the problem. Only a live grant proves validity.
        parts.append(
            f"refresh token {'EXPIRED' if rexp <= now else 'unexpired'} "
            f"(exp {rexp:%Y-%m-%d %H:%MZ}, {max(0, (rexp - now).days)} days left; "
            f"the issuer can still revoke it -- only a live refresh proves it works)"
        )
        return "; ".join(parts)

    def access_token(self) -> str:
        """A currently-valid access token, refreshing through the issuer if needed."""
        with self._lock, self._process_lock():
            # Read inside the lock: a process that queued here while another
            # was refreshing must see the winner's newly-persisted pair.
            env = self._read()
            access = env.get("FPL_ACCESS_TOKEN")
            now = dt.datetime.now(dt.timezone.utc)
            if access:
                try:
                    if jwt_expiry(access) - now > dt.timedelta(seconds=REFRESH_MARGIN_S):
                        return access
                except Exception:  # noqa: BLE001 - malformed cache falls through to refresh
                    pass
            return self._refresh(env)

    def _refresh(self, env: dict[str, str]) -> str:
        refresh = env.get("FPL_REFRESH_TOKEN")
        if not refresh:
            raise AuthNotConfiguredError(
                "No FPL refresh token stored. One-time setup: log in at "
                "fantasy.premierleague.com, copy the Cookie header from any /api/ "
                "request (DevTools -> Network), then run "
                "`uv run fpl myteam auth --paste-cookie`. After that, renewal is "
                "automatic for ~6 months."
            )
        payload = jwt_payload(refresh)
        issuer = env.get("FPL_OAUTH_ISSUER") or payload.get("iss")
        client_id = env.get("FPL_OAUTH_CLIENT_ID") or payload.get("client_id")
        if not client_id:
            # PingOne refresh tokens do not always carry client_id; the access
            # token does, and ingest_from_cookie stores it. Reaching here means
            # setup skipped that step.
            raise AuthNotConfiguredError(
                "FPL_OAUTH_CLIENT_ID is not stored and the refresh token does not "
                "carry it. Re-run the one-time setup with a full Cookie paste."
            )
        exp = dt.datetime.fromtimestamp(payload["exp"], dt.timezone.utc)
        if exp <= dt.datetime.now(dt.timezone.utc):
            raise RefreshRefusedError(
                f"The stored refresh token expired at {exp:%Y-%m-%d %H:%MZ}. "
                "Log in once in your browser and re-run "
                "`uv run fpl myteam auth --paste-cookie`."
            )

        resp = httpx.post(
            f"{issuer}/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": client_id,
            },
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            # OAuth error bodies are the diagnosis: invalid_grant means the
            # token really is spent, invalid_client means we are asking the
            # wrong issuer. Discarding them made every failure look identical.
            reason = ""
            try:
                err = resp.json()
                reason = "; ".join(
                    str(err[k]) for k in ("error", "error_description") if err.get(k)
                )
            except Exception:  # noqa: BLE001 - a non-JSON body is still a clue
                reason = resp.text[:200].strip()
            raise RefreshRefusedError(
                f"The token endpoint at {issuer} refused the refresh grant "
                f"(HTTP {resp.status_code})"
                + (f": {reason}. " if reason else ". ")
                + "Log in once in your browser and re-run "
                "`uv run fpl myteam auth --paste-cookie`."
            )
        body = resp.json()
        access = body.get("access_token")
        if not access:
            raise RefreshRefusedError(
                "The token endpoint answered 200 but returned no access_token; "
                "response keys: " + ", ".join(sorted(body))
            )
        # PingOne rotates refresh tokens; persist the new pair or the next
        # refresh uses a consumed token and fails for no visible reason.
        self._persist(access, body.get("refresh_token"))
        return access

    def ingest_from_cookie(self, cookie: str) -> str:
        """One-time setup from a pasted browser Cookie header. Returns status()."""
        access, refresh = extract_tokens_from_cookie(cookie)
        if not refresh:
            raise AuthNotConfiguredError(
                "The pasted cookie contains no refresh_token. Make sure you are "
                "logged in and copied the FULL Cookie header from a request to "
                "fantasy.premierleague.com/api/..."
            )
        text = self.env_path.read_text() if self.env_path.exists() else ""
        if access:
            text = _set_env_var(text, "FPL_ACCESS_TOKEN", access)
            payload = jwt_payload(access)
            if payload.get("iss"):
                text = _set_env_var(text, "FPL_OAUTH_ISSUER", payload["iss"])
            if payload.get("client_id"):
                text = _set_env_var(text, "FPL_OAUTH_CLIENT_ID", payload["client_id"])
        text = _set_env_var(text, "FPL_REFRESH_TOKEN", refresh)
        tmp = self.env_path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.chmod(0o600)
        tmp.replace(self.env_path)
        return self.status()
