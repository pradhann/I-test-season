"""Who this server is answering for, and where its data lives.

A stdio MCP session has no HTTP request, so there is nothing per-call to read
an identity from: the process is started by one desktop client on behalf of one
person. The context is therefore resolved once at startup and handed to every
tool that needs an entry id, a data root or a display name.

THE ONE SEAM

:func:`user_context` is the only function in this package that decides where a
context comes from, and it asks ``fpl_edge.platform.users.owner_context`` for
one. That function is the single place in the repo that resolves the operator's
entry id, from the id saved on the Account tab, then ``FPL_ENTRY_ID``, then the
committed default. This package holds no second answer and no copy of the
config singleton, so a stdio session and an HTTP request read the same
``UserContext``.

When workstream E lands sessions, an MCP session that carries an identity
resolves through ``users.context_for`` instead, and this is the one function
that changes.

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
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the runtime import is function-local, inside user_context
    from fpl_edge.platform.users import UserContext

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


@lru_cache(maxsize=1)
def user_context() -> UserContext:
    """The context this process answers for, resolved once at startup.

    THE SEAM. The import is function-local because ``fpl_edge.platform`` sits
    below this package and a module-scope edge back into it would close the
    cycle that ``tests/unit/test_mcp_tool_contract.py`` guards.

    Cached because a stdio session has one user for its whole life, and
    because resolving it twice would let two tool calls in one session
    disagree about whose team they are reading.
    """
    from fpl_edge.platform.users import owner_context

    return owner_context()


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
