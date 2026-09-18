"""Connect, verify and describe the FPL account credential, in one place.

The CLI (``fpl myteam auth``) and the web UI (``/api/account/*``) used to be
two different stories about the same three steps: store the pasted tokens,
redeem the refresh grant once to prove it is alive, read the private team.
This module is the single implementation both call, so the paste that works
in the terminal works in the browser and fails with the same words.

Why the paste exists at all, stated once so every surface can repeat it: FPL
has no API login. Since the move to PingOne OAuth the only durable credential
is the refresh token, and the only place it is minted is a browser session the
manager logs into themselves. Nothing here can remove that step; it can make
everything after it one paste.

Nothing in this module returns, logs or stores a token value outside the
``.env`` file :class:`~fpl_edge.myteam.tokens.TokenManager` already owns. The
verification record it keeps (``data/cache/fpl_account.json``) holds instants,
an entry id and an entry name, never a credential.

Whose account this is comes from the caller: the routes and the CLI pass an
entry id, and a caller that passes none gets the owner context, which follows
the team id saved on the Account tab. These three functions read a stored FPL
login, so they are the owner's alone until a second user can store one.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from fpl_edge.config import ENV_PATH
from fpl_edge.ingest.http import USER_AGENT
from fpl_edge.myteam.private import (
    NoSessionError,
    PrivateSquad,
    PrivateTeamClient,
    StaleSessionError,
)
from fpl_edge.myteam.sources import BASE
from fpl_edge.platform.users import owner_context
from fpl_edge.myteam.tokens import (
    AuthNotConfiguredError,
    RefreshRefusedError,
    TokenManager,
    extract_tokens_from_cookie,
    jwt_expiry,
    jwt_payload,
)

#: The one-line answer to "why is it so hard". Shown verbatim by the UI.
WHY_HARD = (
    "FPL has no API login: since PingOne OAuth the only durable credential is "
    "the refresh token, and only a browser session you log into yourself can "
    "mint one. This tool cannot remove that step; it makes everything after it "
    "one paste."
)

#: The terminal equivalent of the web form, for people who prefer it.
CLI_COMMAND = "pbpaste | uv run fpl myteam auth --paste-cookie"

#: The verification record. Under data/cache/ because that directory is
#: already gitignored and declared safe to delete; losing it costs one
#: "verify again" click, nothing more.
RECORD_PATH = Path("data/cache/fpl_account.json")

#: Remediation the CLI has always printed when the refresh grant is refused
#: right after a paste. Kept as one constant so the UI and CLI say the same.
REFRESH_REFUSED_REMEDIATION = (
    "The access token will still last ~8 hours, but renewal will fail after "
    "that. Re-copy the cookie from a browser session you have just used, and "
    "check nothing else is redeeming the same token "
    "(`launchctl list | grep fpledge`)."
)

Progress = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one connect or verify attempt found. Never carries a token.

    ``stage`` names how far it got: ``paste`` (parsing the cookie), ``store``
    (writing the pair), ``refresh`` (redeeming the grant), ``fetch`` (reading
    the private team), ``done``. ``error_class`` is one of ``malformed_paste``,
    ``not_configured``, ``refresh_refused``, ``session_rejected``,
    ``network_error``; None on success.
    """

    ok: bool
    stage: str
    message: str
    error_class: str | None = None
    remediation: str | None = None
    entry_id: int | None = None
    entry_name: str | None = None
    player_name: str | None = None
    squad: dict[str, Any] | None = None
    stored_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationRecord:
    """Last attempt and last success, kept apart so a failed re-verify does
    not erase the name the account once proved to be."""

    path: Path = RECORD_PATH
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = RECORD_PATH) -> VerificationRecord:
        try:
            return cls(path=path, data=json.loads(path.read_text()))
        except (OSError, ValueError):
            return cls(path=path, data={})

    def note(self, outcome: Outcome) -> None:
        now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        attempt = {
            "at": now,
            "ok": outcome.ok,
            "stage": outcome.stage,
            "error_class": outcome.error_class,
            "entry_id": outcome.entry_id,
        }
        self.data["last_attempt"] = attempt
        if outcome.ok:
            self.data["last_ok"] = {
                "at": now,
                "entry_id": outcome.entry_id,
                "entry_name": outcome.entry_name,
                "player_name": outcome.player_name,
            }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            tmp.replace(self.path)
        except OSError:
            pass  # a record is a convenience; the credential store is .env


# -- status ------------------------------------------------------------------


def _expiry_block(token: str | None, now: dt.datetime) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        exp = jwt_expiry(token)
    except Exception:  # noqa: BLE001 - a malformed cache is reported, not raised
        return {"expires_at": None, "expired": None, "days_left": None,
                "malformed": True}
    return {
        "expires_at": exp.isoformat(timespec="seconds"),
        "expired": exp <= now,
        "days_left": max(0, (exp - now).days),
    }


def account_status(
    manager: TokenManager,
    *,
    entry_id: int | None = None,
    record_path: Path = RECORD_PATH,
) -> dict[str, Any]:
    """Token state and squad provenance, with no network call and no token value.

    ``squad_source`` says which team the panels will read on their next
    request: ``private`` when a refresh token is stored and the last live check
    succeeded, ``public`` when none is stored or the last check failed, and
    ``unverified`` when a token is stored but no live check has run from here.
    That third value exists because a refresh token's own ``exp`` proves
    nothing: the issuer can revoke it early (the owner's case), and only a
    live grant tells private from public. It is what the panels' own code path
    (:meth:`QuestionRouter._team_state`) will decide, restated without making
    the request.
    """
    now = dt.datetime.now(dt.UTC)
    env = manager._read()
    access = env.get("FPL_ACCESS_TOKEN")
    refresh = env.get("FPL_REFRESH_TOKEN")
    record = VerificationRecord.load(record_path).data
    last_ok = record.get("last_ok")
    last_attempt = record.get("last_attempt")

    disabled = PrivateTeamClient.disabled_by_env()
    if disabled:
        source, reason = "public", (
            "private reads are switched off for this process "
            "(FPL_EDGE_DISABLE_PRIVATE is set)"
        )
    elif not refresh:
        source, reason = "public", (
            "not connected: the panels read your public picks from the last "
            "deadline, so transfers you have made since are invisible"
        )
    elif last_attempt and not last_attempt.get("ok"):
        source, reason = "public", (
            "the last live check failed, so the panels fall back to public "
            "picks until a verify succeeds"
        )
    elif last_ok:
        source, reason = "private", "live from your FPL account"
    else:
        source, reason = "unverified", (
            "a refresh token is stored but no live check has run from here; the "
            "issuer can revoke a token before its own expiry, so press Verify "
            "again to learn whether the panels read your account or public picks"
        )

    return {
        "ok": True,
        "entry_id": int(entry_id if entry_id is not None
                        else owner_context().entry_id),
        "refresh_stored": bool(refresh),
        "access": _expiry_block(access, now),
        "refresh": _expiry_block(refresh, now),
        "last_ok": last_ok,
        "last_attempt": last_attempt,
        "squad_source": source,
        "squad_source_reason": reason,
        "summary": manager.status(),
        "why_hard": WHY_HARD,
        "cli": CLI_COMMAND,
    }


# -- connect / verify ---------------------------------------------------------


def _squad_dict(squad: PrivateSquad) -> dict[str, Any]:
    return {
        "picks": len(squad.picks.picks),
        "bank_tenths": squad.bank.tenths,
        "value_tenths": squad.squad_value.tenths,
        "free_transfers": squad.free_transfers,
        "chips": dict(sorted(squad.chips.items())),
    }


@dataclass(frozen=True)
class EntryCheck:
    """What ``entry/{id}/`` said about a team id, including how it failed.

    ``status`` is the HTTP status, or None when the request never completed.
    The distinction is the whole point of this type: 404 means no team has
    that id, and a timeout means the check could not be run. Telling a manager
    their id is wrong because FPL was slow sends them looking for a number
    that was right all along.
    """

    found: bool
    status: int | None
    team_name: str | None = None
    manager_name: str | None = None
    error: str | None = None


def check_entry(entry_id: int, *, timeout: float = 15.0) -> EntryCheck:
    """Look one team id up on the public endpoint. No cookie, no bearer.

    ``entry/{id}/`` is public, so this works for a manager validating their own
    id before they have connected anything.
    """
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(
                f"{BASE}/entry/{int(entry_id)}/",
                headers={"User-Agent": USER_AGENT},
            )
    except Exception as exc:  # noqa: BLE001 - a network failure is not a verdict
        return EntryCheck(found=False, status=None,
                          error=f"{type(exc).__name__}: {exc}")
    if resp.status_code != 200:
        return EntryCheck(found=False, status=resp.status_code)
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - a 200 that is not JSON is not a team
        return EntryCheck(found=False, status=resp.status_code,
                          error=f"{type(exc).__name__}: {exc}")
    player = " ".join(
        x for x in (body.get("player_first_name"), body.get("player_last_name")) if x
    ) or None
    return EntryCheck(found=True, status=200,
                      team_name=body.get("name") or None,
                      manager_name=player)


def default_entry_lookup(entry_id: int) -> tuple[str | None, str | None]:
    """Team name and manager name from the public entry endpoint.

    Best-effort: the connect has already succeeded by the time this runs, so a
    failure here costs a label, not the connection. A thin wrapper over
    :func:`check_entry` so there is still one place that knows the URL.
    """
    check = check_entry(entry_id)
    return check.team_name, check.manager_name


def _classify(exc: BaseException) -> tuple[str, str | None]:
    """Map the myteam exception zoo onto the UI's failure classes."""
    if isinstance(exc, RefreshRefusedError):
        return "refresh_refused", REFRESH_REFUSED_REMEDIATION
    if isinstance(exc, AuthNotConfiguredError):
        return "malformed_paste", None
    if isinstance(exc, NoSessionError):
        return "not_configured", None
    if isinstance(exc, StaleSessionError):
        # A setup that still carries the legacy FPL_SESSION_COOKIE falls
        # through to the cookie path after the bearer fails, and the client
        # reports both. The first failure is the true story: when it was the
        # issuer refusing the grant, say so, or the UI headline blames the
        # cookie the manager never relied on.
        if "refused the refresh grant" in str(exc):
            return "refresh_refused", REFRESH_REFUSED_REMEDIATION
        return "session_rejected", None
    if isinstance(exc, (httpx.HTTPError, OSError)):
        return "network_error", (
            "FPL or the token issuer could not be reached. Nothing was changed; "
            "try again in a moment."
        )
    return "network_error", None


def _fetch_and_label(
    client: PrivateTeamClient,
    entry_id: int,
    entry_lookup: Callable[[int], tuple[str | None, str | None]],
    *,
    stored_status: str | None,
) -> Outcome:
    try:
        squad = client.fetch(entry_id)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a class
        error_class, remediation = _classify(exc)
        return Outcome(
            ok=False, stage="fetch", message=str(exc), error_class=error_class,
            remediation=remediation, entry_id=entry_id, stored_status=stored_status,
        )
    name, player = entry_lookup(entry_id)
    sq = _squad_dict(squad)
    return Outcome(
        ok=True, stage="done", entry_id=entry_id, entry_name=name,
        player_name=player, squad=sq, stored_status=stored_status,
        message=(
            f"Session OK. Live squad read: {sq['picks']} picks, "
            f"bank {sq['bank_tenths'] / 10:.1f}, value {sq['value_tenths'] / 10:.1f}, "
            f"free transfers {sq['free_transfers']}, chips: "
            + ", ".join(f"{k}={v}" for k, v in sq["chips"].items())
        ),
    )


def connect(
    cookie: str,
    *,
    manager: TokenManager | None = None,
    client: PrivateTeamClient | None = None,
    entry_id: int | None = None,
    entry_lookup: Callable[[int], tuple[str | None, str | None]] = default_entry_lookup,
    record_path: Path = RECORD_PATH,
    progress: Progress | None = None,
) -> Outcome:
    """The CLI's ``auth --paste-cookie`` as one call: store, prove, read.

    Accepts the whole Cookie header, ``access_token=...; refresh_token=...``,
    or just ``refresh_token=...``, exactly as :meth:`TokenManager.ingest_from_cookie`
    does. Nothing is written when the paste carries no decodable refresh
    token; on any later failure the stored pair is left exactly as the CLI
    would have left it.
    """
    manager = manager if manager is not None else TokenManager(env_path=ENV_PATH)
    eid = int(entry_id if entry_id is not None else owner_context().entry_id)
    say = progress or (lambda _msg: None)

    # -- paste: refuse garbage before it is written anywhere ------------------
    text = (cookie or "").strip()
    _access, refresh = extract_tokens_from_cookie(text)
    if refresh:
        try:
            jwt_payload(refresh)["exp"]
        except Exception:  # noqa: BLE001 - not a JWT, or one with no exp
            return Outcome(
                ok=False, stage="paste", error_class="malformed_paste",
                message=(
                    "The refresh_token in the paste is not a decodable token. "
                    "Copy the value exactly as the browser shows it, without "
                    "quotes or a trailing semicolon, or paste the whole Cookie "
                    "header."
                ),
                entry_id=eid,
            )

    # -- store: the CLI's exact code path ---------------------------------------
    try:
        stored_status = manager.ingest_from_cookie(text)
    except AuthNotConfiguredError as exc:
        return Outcome(ok=False, stage="paste", error_class="malformed_paste",
                       message=str(exc), entry_id=eid)
    except Exception as exc:  # noqa: BLE001 - e.g. an undecodable access token
        return Outcome(
            ok=False, stage="paste", error_class="malformed_paste",
            message=f"The paste could not be read as FPL tokens: {exc}",
            entry_id=eid,
        )
    say(f"Stored. {stored_status}")

    # -- refresh: prove the grant, do not assume it -------------------------------
    say("Proving the refresh grant (redeeming once)...")
    try:
        manager.prove_refresh()
    except Exception as exc:  # noqa: BLE001 - report any refusal plainly
        error_class, remediation = _classify(exc)
        if error_class not in ("refresh_refused", "network_error"):
            error_class = "refresh_refused"
        outcome = Outcome(
            ok=False, stage="refresh", error_class=error_class,
            message=f"The refresh grant does not work: {exc}",
            remediation=remediation or REFRESH_REFUSED_REMEDIATION,
            entry_id=eid, stored_status=stored_status,
        )
        VerificationRecord.load(record_path).note(outcome)
        return outcome
    say("Refresh grant verified. Renewal is automatic.")

    # -- fetch: the private team, with the fresh bearer ----------------------------
    say("Verifying against the live account (refreshing if needed)...")
    client = client if client is not None else PrivateTeamClient(tokens=manager)
    outcome = _fetch_and_label(client, eid, entry_lookup, stored_status=stored_status)
    VerificationRecord.load(record_path).note(outcome)
    return outcome


def verify(
    *,
    manager: TokenManager | None = None,
    client: PrivateTeamClient | None = None,
    entry_id: int | None = None,
    entry_lookup: Callable[[int], tuple[str | None, str | None]] = default_entry_lookup,
    record_path: Path = RECORD_PATH,
) -> Outcome:
    """The live check on its own: read the private team with what is stored.

    The access token refreshes through the stored grant if it has aged out,
    which is the everyday case and the one that proves the chain is alive.
    """
    manager = manager if manager is not None else TokenManager(env_path=ENV_PATH)
    eid = int(entry_id if entry_id is not None else owner_context().entry_id)
    client = client if client is not None else PrivateTeamClient(tokens=manager)
    if not manager.configured and not client.configured:
        outcome = Outcome(
            ok=False, stage="fetch", error_class="not_configured", entry_id=eid,
            message=(
                "No FPL auth is configured, so the pre-deadline squad cannot be "
                "read. Paste your browser session once to connect."
            ),
        )
        return outcome
    outcome = _fetch_and_label(client, eid, entry_lookup, stored_status=None)
    VerificationRecord.load(record_path).note(outcome)
    return outcome

