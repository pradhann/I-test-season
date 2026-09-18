"""Who this server is answering for, and where its data lives.

A stdio MCP session has no HTTP request, so there is nothing per-call to read
an identity from: the process is started by one desktop client on behalf of one
person. The context is therefore resolved once at startup and handed to every
tool that needs an entry id, a data root or a display name.

THE ONE SEAM, AND HOW IT MOVES

``user_context()`` is the only function in this package that decides where a
context comes from. Agent D2 is building ``fpl_edge/platform/user_context.py``
with ``UserContext`` and ``owner_context()`` (docs/platform/MULTI_USER.md
section 2). Until that module exists, :func:`user_context` falls back to the
local :class:`OwnerContext`, which reads the same two facts out of
``fpl_edge.config.USER`` that the engine reads today.

Switching to D2's module is a one-line change: delete the ``except
ImportError`` arm of :func:`_platform_owner_context` once the import resolves.
Nothing else in ``fpl_edge/mcp/`` imports ``fpl_edge.config``, and nothing else
constructs a context.

WHAT DOES NOT COME FROM THE CALLER

``entry_id`` never appears in a tool's input schema. The panels that need one
(``squad_overview``, ``dashboard_brief``, ``planner_grid``) receive it from
here, so an MCP caller cannot ask this server for somebody else's squad by
passing a number. That is rule 5 of the MCP spec and section 4 of the
multi-user design, and ``tests/unit/test_mcp_tool_contract.py`` enforces it
over every registered tool.

WHAT THIS MODULE HOLDS NONE OF

No key. It reads no ``ANTHROPIC_*`` variable, opens no FPL token file, and
logs no credential. The only secret material reachable from a context is
whatever the per-user store behind it decrypts on demand, and that never lands
in a payload.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

UTC = dt.UTC

#: The checkout root: three parents up from fpl_edge/mcp/context.py.
#: ``FPL_EDGE_HOME`` overrides it, which is the one variable the Claude desktop
#: entry sets and even that is optional.
_DEFAULT_HOME = Path(__file__).resolve().parents[2]


def engine_home() -> Path:
    """The checkout this server reads from."""
    configured = os.environ.get("FPL_EDGE_HOME", "").strip()
    return Path(configured).expanduser() if configured else _DEFAULT_HOME


def db_path() -> Path:
    """The warehouse file. ``FPL_EDGE_DB`` overrides it, for a copy or a test.

    One answer to a question four modules of the old toolbelt each answered
    slightly differently.
    """
    configured = os.environ.get("FPL_EDGE_DB", "").strip()
    if configured:
        return Path(configured).expanduser()
    return engine_home() / "data" / "warehouse" / "fpl.duckdb"


@dataclass(frozen=True, slots=True)
class OwnerContext:
    """The local stand-in for D2's ``UserContext``, same five fields.

    Field for field what ``docs/platform/MULTI_USER.md`` section 2 declares,
    minus the store handle and the token manager, which a stdio session has no
    way to populate and no tool here reads. When D2's module lands this class
    stops being constructed; :func:`user_context` returns theirs instead.
    """

    user_id: str
    entry_id: int
    display_name: str | None
    data_root: Path
    is_owner: bool

    def describe(self) -> dict[str, Any]:
        """What the server may say about the user. Never a secret."""
        return {
            "user_id": self.user_id,
            "entry_id": self.entry_id,
            "display_name": self.display_name,
            "is_owner": self.is_owner,
        }


def _platform_owner_context() -> Any | None:
    """D2's ``owner_context()``, or None while that module is still landing.

    THE SEAM. When ``fpl_edge/platform/user_context.py`` exists, this returns
    its context and the fallback below is dead code to be deleted.
    """
    try:
        from fpl_edge.platform.user_context import owner_context
    except ImportError:
        return None
    return owner_context()


def _fallback_owner_context() -> OwnerContext:
    """The owner, read from the engine's own config.

    ``fpl_edge.config.USER`` is the singleton the multi-user migration
    replaces. It is read here and nowhere else in this package, so there is
    one line to change rather than a search.
    """
    from fpl_edge.config import USER

    return OwnerContext(
        user_id="owner",
        entry_id=int(USER.entry_id),
        display_name=str(USER.team_name) or None,
        data_root=engine_home() / "data",
        is_owner=True,
    )


@lru_cache(maxsize=1)
def user_context() -> Any:
    """The context this process answers for, resolved once at startup.

    Cached because a stdio session has one user for its whole life, and
    because resolving it twice would let two tool calls in one session
    disagree about whose team they are reading.
    """
    return _platform_owner_context() or _fallback_owner_context()


def reset_context_cache() -> None:
    """Test hook. Production resolves the context once and keeps it."""
    user_context.cache_clear()


def entry_id() -> int:
    """The FPL entry this server reads. Never a tool parameter."""
    return int(user_context().entry_id)


def parse_as_of(value: str | None) -> dt.datetime | None:
    """Parse an ``as_of`` argument, or None when none was given.

    A naive instant is rejected rather than assumed to be UTC. The engine's
    rule registry is explicit that only the API's UTC deadlines are
    authoritative, and silently localising here would be the same class of bug
    as reading a deadline off the rules page.
    """
    if value is None or not str(value).strip():
        return None
    parsed = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(
            "as_of must carry a timezone, for example 2026-08-18T22:50:00Z. "
            "A naive instant is rejected rather than assumed to be UTC."
        )
    return parsed.astimezone(UTC)


def warehouse_missing() -> str | None:
    """The reason the warehouse cannot be read, or None when it can."""
    path = db_path()
    if not path.exists():
        return (
            f"no warehouse at {path}. Run an ingest in the engine checkout, "
            f"or point FPL_EDGE_DB at the file."
        )
    return None
