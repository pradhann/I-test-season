"""Whose team a request is about, and where that manager's own files live.

The engine was one operator. ``fpl_edge/config.py`` builds a module-level
``USER`` and thirty call sites read the entry id off it, so a second manager
on the same server would see the owner's squad, the owner's plan and the
owner's chat history. This module is the replacement: one context, resolved
once per request, carrying the entry id and a directory that belongs to that
manager alone.

Three rules hold the split together.

1. **Shared state stays shared and read-only.** The warehouse, the projection
   and forecast parquets, the odds, the creator corpus and the crawls describe
   the game rather than a manager. Nothing here touches them.
2. **Per-user state lives under ``{data_root}/users/{user_id}/`` and nothing
   else writes there.** :meth:`UserContext.path` is the only way to build a
   path inside it, and it refuses any component that would escape.
3. **Private FPL endpoints are the owner's alone in this phase.**
   ``my-team/{id}/`` needs the requesting manager's own bearer token, and the
   only stored token is the operator's ``.env``. Any other user gets
   :data:`PRIVATE_GAP` from the panels that need it, which is a named gap, not
   an error and never the owner's data.

Identity arrives through one seam. :func:`resolve_identity` reads the session
cookie through ``fpl_edge.platform.auth`` and returns an :class:`Identity`, or
``None`` when the deployment answers an unauthenticated request as the
operator. No other module builds a user path or decides who a request is.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fpl_edge.config import ENV_PATH, owner_entry_id, owner_team_name

try:  # Starlette ships with FastAPI; the CLI and the bot import this module too.
    from starlette.requests import Request
except ImportError:  # pragma: no cover - no web server installed
    Request = Any  # type: ignore[assignment,misc]

#: The owner's user id. Reserved: an identity from workstream E may never be
#: issued this value, because it addresses the operator's own directory.
OWNER_USER_ID = "owner"

#: Where every data directory in this repo already sits. Workstream B sets
#: this to the Railway volume mount point, so the per-user store lands on the
#: same volume as the warehouse and survives a redeploy.
DATA_ROOT_ENV = "FPL_EDGE_DATA_ROOT"

#: The name the boot sequence uses for the same directory
#: (``fpl_edge/platform/boot.py``, read at ``app/factory.py``). Read as a
#: fallback so a deployment that sets one and not the other still puts the
#: per-user store on the volume. A store off the volume looks fine until the
#: first redeploy takes every user's plan and conversation with it.
BOOT_DATA_DIR_ENV = "FPL_EDGE_DATA_DIR"

#: A user id is a directory name. Lowercase alphanumerics, dashes and
#: underscores, no separators and no dots, so an identity value cannot become
#: a path traversal and a directory listing stays readable for support.
USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: What a panel says when it needs a private FPL read and the caller is not
#: the owner. One string, so the Dashboard, the Planner and the squad card
#: word it identically.
PRIVATE_GAP = (
    "connect your FPL account to see this. Your squad, bank and chips come "
    "from an endpoint that needs your own FPL login, and this server holds a "
    "login for the owner only."
)


#: The four artefacts that describe one manager rather than the game, named
#: here so the reader and the writer of each cannot drift apart. Every one of
#: them sits in ``data/warehouse/`` today, in a path with no user in it.
PLANS_DIR = "plans"
GW1_PLAN_NAME = "gw1_plan.json"
TRANSFER_PLAN_NAME = "transfer_plan.json"
BRIEFING_INTEL_NAME = "briefing_intel.json"
CHAT_DIR = "chat"


class UserIdInvalid(ValueError):
    """A user id that is not a directory name. Never a path, never a dot."""


class PrivateReadDenied(PermissionError):
    """A private FPL read was attempted for a user who has no stored login."""


def data_root() -> Path:
    """The base directory the per-user store hangs off.

    Read on every call rather than captured at import: the tests point it at a
    tmp directory with ``monkeypatch.setenv`` and a boot sequence may set it
    after this module is first imported.
    """
    return Path(os.environ.get(DATA_ROOT_ENV, "")
                or os.environ.get(BOOT_DATA_DIR_ENV, "")
                or "data")


def users_root() -> Path:
    """``{FPL_EDGE_DATA_ROOT}/users``. The one place user paths start."""
    return data_root() / "users"


def validate_user_id(user_id: str) -> str:
    """Return the id, or raise. The only gate between an identity and a path."""
    if not isinstance(user_id, str) or not USER_ID_RE.match(user_id):
        raise UserIdInvalid(
            f"user id {user_id!r} is not a directory name: lowercase letters, "
            f"digits, dashes and underscores only, 1 to 64 characters. A value "
            f"with a separator or a dot in it would address another user's "
            f"directory or the tree above it."
        )
    return user_id


@dataclass(frozen=True)
class UserContext:
    """One manager, for the life of one request, job or CLI command.

    ``data_root`` is this user's own directory, ``{users_root()}/{user_id}``.
    It is a field rather than a property so a test, a job or workstream E can
    hand in a directory without reaching for the environment.

    The context holds no secret. ``token_env_path`` names a file the owner's
    token manager reads, and ``anthropic_key`` is a method that decrypts on
    demand and returns an object whose repr is four characters, so nothing a
    repr of this dataclass could print is a credential.
    """

    user_id: str
    entry_id: int
    display_name: str | None
    data_root: Path
    is_owner: bool

    @property
    def can_read_private(self) -> bool:
        """True when this user has a stored FPL login of their own.

        The owner's login is the process-wide ``.env`` that the CLI writes.
        Any other user has no stored login in this phase, so the answer is
        False and the panels that need one serve :data:`PRIVATE_GAP`.
        """
        return bool(self.is_owner)

    def path(self, *parts: str) -> Path:
        """A path inside this user's directory. Raises on anything outside it.

        Every per-user artefact goes through here. A component containing a
        separator, a dot segment or an absolute path resolves outside
        ``data_root`` and is refused, so a conversation id or a file name that
        arrived over HTTP cannot address another user's files.
        """
        base = self.data_root.resolve()
        target = base.joinpath(*parts).resolve()
        if target != base and base not in target.parents:
            raise UserIdInvalid(
                f"{Path(*parts)} resolves outside {base}, which is user "
                f"{self.user_id}'s own directory. Per-user files never escape "
                f"it."
            )
        return target

    def artefact(self, *parts: str, legacy: Path | None = None) -> Path:
        """A per-user artefact, with the owner's pre-split file as a fallback.

        The owner's plan, briefing and chat transcripts sit beside the
        warehouse today, in paths with no user in them. Moving them is one
        ``mv`` the operator runs once (DEPLOYMENT.md), so until they do, the
        owner reads the file that is actually there. Any other user reads
        their own directory or nothing: ``legacy`` is never consulted for
        them, which is what keeps one manager's plan out of another's panel.
        """
        mine = self.path(*parts)
        if mine.exists():
            return mine
        if legacy is not None and self.is_owner:
            return legacy
        return mine

    def ensure_dir(self, *parts: str) -> Path:
        """Create a directory inside this user's own tree and return it."""
        target = self.path(*parts)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def token_env_path(self) -> Path:
        """The file the FPL token manager reads and writes for this user.

        The owner's is the repo ``.env``, which is what the CLI paste flow
        already writes. For anyone else there is no stored login, so this
        raises rather than handing back a path that would read the operator's
        tokens.
        """
        if not self.is_owner:
            raise PrivateReadDenied(
                f"user {self.user_id} has no stored FPL login. {PRIVATE_GAP}"
            )
        return ENV_PATH

    def anthropic_key(self):
        """This manager's own model key, decrypted now, or None.

        Called at the point of use, inside the turn that is about to spend
        tokens, never when the context is built. A request that calls no model
        never decrypts a key, so the value spends less time in process memory
        and fewer code paths can log it.

        None has two meanings and both are correct. For the owner it means
        run on the operator's own Claude CLI login, which is what the engine
        does today and what the briefing task and the content pipeline keep
        doing. For anybody else it means no key is stored, and the route that
        needed one has already refused the request with
        ``fpl_edge.platform.auth.keys.NO_KEY_DETAIL``.

        The return is a ``keys.UserKey``, whose ``repr`` prints the last four
        characters only, so an f-string of it in a log line is safe by
        construction rather than by review.
        """
        from fpl_edge.platform.auth import keys as auth_keys

        try:
            return auth_keys.load(self.user_id)
        except auth_keys.KeyUnreadable:
            raise
        except Exception:  # noqa: BLE001 - no store yet is "no key", not a 500
            return None

    def to_dict(self) -> dict[str, Any]:
        """The context as a log line. No path, no secret, no display name."""
        return {"user_id": self.user_id, "entry_id": int(self.entry_id),
                "is_owner": bool(self.is_owner)}


@dataclass(frozen=True)
class Identity:
    """Who a request is, before a store is attached.

    Workstream E returns one of these from :func:`resolve_identity` after it
    verifies the session cookie. ``entry_id`` is the team id that user saved
    on the Account tab; None means they have not saved one yet.
    """

    user_id: str
    entry_id: int | None = None
    display_name: str | None = None


def owner_context() -> UserContext:
    """The operator, and whichever team id they run against.

    Every job, every CLI command and every request without a session resolves
    here. This is the only function in the repo that reads the owner's entry
    id, and it reads it through :func:`fpl_edge.config.owner_entry_id`.

    Three sources, in order: the id saved on the Account tab, then
    ``FPL_ENTRY_ID``, then the committed default. The saved id wins because it
    is the operator's own later choice, and the deployment variable is what a
    store with nothing saved in it starts from.
    """
    root = users_root() / OWNER_USER_ID
    saved = stored_entry_id(root)
    return UserContext(
        user_id=OWNER_USER_ID,
        entry_id=int(saved) if saved else owner_entry_id(),
        display_name=owner_team_name(),
        data_root=root,
        is_owner=True,
    )


def context_for(identity: Identity) -> UserContext:
    """Build a context for a signed-in user. The seam workstream E calls.

    An identity whose id is the reserved owner id gets the owner context, so
    there is one definition of what the owner can do. Anyone else gets their
    own directory, their own saved entry id, and no private FPL access.
    """
    user_id = validate_user_id(identity.user_id)
    if user_id == OWNER_USER_ID:
        owner = owner_context()
        if identity.entry_id is None:
            return owner
        return UserContext(
            user_id=owner.user_id, entry_id=int(identity.entry_id),
            display_name=identity.display_name, data_root=owner.data_root,
            is_owner=True,
        )
    root = users_root() / user_id
    entry_id = identity.entry_id
    if entry_id is None:
        entry_id = stored_entry_id(root)
    return UserContext(
        user_id=user_id,
        entry_id=int(entry_id) if entry_id else 0,
        display_name=identity.display_name,
        data_root=root,
        is_owner=False,
    )


#: The per-user profile: the entry id the manager saved on the Account tab and
#: the team name the public endpoint returned for it. Plaintext on purpose.
#: ``entry/{id}/`` serves both to anyone, so encrypting them would suggest they
#: are secret and would make the directory unreadable for support.
PROFILE_NAME = "profile.json"


def stored_entry_id(root: Path) -> int | None:
    """The entry id saved in a user directory's profile, or None.

    An unreadable or absent profile is "not saved yet", not an error: the
    Account tab's team-id input is how it gets written, and a user who has not
    used it yet sees the panels' existing empty states.
    """
    import json

    path = Path(root) / PROFILE_NAME
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        value = int(body.get("entry_id"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def write_profile(ctx: UserContext, *, entry_id: int,
                  team_name: str | None = None,
                  manager_name: str | None = None) -> dict[str, Any]:
    """Save the entry id a user typed on the Account tab. Returns the profile.

    Written atomically through a temp file in the same directory, because the
    next request reads it and a half-written profile would report the user as
    having saved nothing.
    """
    import datetime as dt
    import json

    ctx.ensure_dir()
    path = ctx.path(PROFILE_NAME)
    now = dt.datetime.now(dt.UTC).isoformat()
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
    profile = {
        "entry_id": int(entry_id),
        "team_name": team_name,
        "manager_name": manager_name,
        "created": existing.get("created") or now,
        "updated": now,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return profile


#: The user id an unauthenticated visitor is given when the deployment does
#: not answer anonymous requests as the operator. Its directory holds nothing
#: private: the public panels read league-wide data and one published entry.
PUBLIC_USER_ID = "public"


def resolve_identity(request: Request) -> Identity | None:
    """Who this request is, or None when it is the owner.

    Three answers, and the order is the whole of the rule.

    1. **A valid session cookie.** The signed cookie names a row in the auth
       database, and that row's ``is_operator`` column decides between the
       reserved owner id and the manager's own directory. The operator's
       Google account and the owner's files are the same person by
       construction, so there is one definition of what the operator can do.
    2. **No session, and the deployment answers anonymous requests as the
       operator.** That is ``FPL_EDGE_ANON_IS_OWNER=1``, and it is the default
       while no Google client is configured, because a deployment nobody can
       sign in to has no other workable answer. This returns None, which is
       what makes the owner's Mac, the CLI, every job and the whole test suite
       behave exactly as they do today.
    3. **No session, and sign-in exists.** A public visitor: their own
       directory, no private FPL read, and the published entry from
       ``PUBLIC_ENTRY_ID`` so the shared panels have a team to describe. The
       entry id comes from the deployment and never from the request, which is
       what keeps the public view one published team rather than a facility
       for reading any team.

    A header that selects a user was considered and rejected. A request header
    that picks an identity is an authentication bypass whether or not a flag
    guards it, and the flag would outlive the workstream. Tests drive a second
    user through ``app.dependency_overrides[current_user]``, which is
    FastAPI's own mechanism and ships no back door.
    """
    from fpl_edge.platform.auth import settings as auth_settings
    from fpl_edge.platform.auth import sessions as auth_sessions

    session = getattr(getattr(request, "state", None), "auth_session", None)
    if session is None and request is not None:
        # A route reached without the application dependency, or a caller
        # that built a request by hand. Reading the cookie here costs one
        # SQLite open and keeps this function's answer independent of wiring.
        session = auth_sessions.session_for_request(request)
    if session is not None:
        user = session.user
        return Identity(
            user_id=auth_sessions.user_id_for(user.sub,
                                              is_operator=user.is_operator),
            entry_id=user.entry_id,
            display_name=user.email,
        )
    if auth_settings.anon_is_owner():
        return None
    return Identity(user_id=PUBLIC_USER_ID,
                    entry_id=auth_settings.public_entry_id() or owner_entry_id())


def current_user(request: Request = None) -> UserContext:
    """The FastAPI dependency: one context per request, built once.

    Cached on ``request.state`` so two route handlers in the same request
    cannot disagree about who is calling.
    """
    state = getattr(request, "state", None)
    cached = getattr(state, "user_context", None)
    if isinstance(cached, UserContext):
        return cached
    identity = resolve_identity(request)
    ctx = owner_context() if identity is None else context_for(identity)
    if state is not None:
        try:
            state.user_context = ctx
        except Exception:  # noqa: BLE001,S110 - a cache miss is not a failed request
            pass
    return ctx
