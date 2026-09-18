"""The one FastMCP instance, and the explicit imports that populate it.

The server name is ``fpl-server`` and it does not change.
``fpl_edge/platform/chat_agent.py`` builds its allowlist as
``mcp__fpl-server__{name}``, and the owner's saved Claude desktop
conversations refer to those qualified names.

Registration is by explicit import, never filesystem discovery: adding a tool
should be a visible one-line diff a reviewer can see, which is the rule the
panel registry already applies to panel scripts.

Twelve modules, 35 tools. Twenty-eight are read adapters over a registered panel or the guarded query,
five are writes through a named store module, and three (``query``,
``run_analysis``, ``python_viz``) carry over the paths they already had.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

#: The name the chat agent's allowlist and the desktop config both use.
SERVER_NAME = "fpl-server"

mcp = FastMCP(SERVER_NAME)

# Import order carries no meaning. Every module registers through the same
# decorator and none of them import each other, and ``prompts`` reads the
# registered names from inside the prompt body rather than at import time, so
# it does not have to come last.
from fpl_edge.mcp import prompts  # noqa: F401
from fpl_edge.mcp.tools import (
    analysis,  # noqa: F401
    creators,  # noqa: F401
    dossier,  # noqa: F401
    fixtures,  # noqa: F401
    ideas,  # noqa: F401
    manager,  # noqa: F401
    ownership,  # noqa: F401
    pipelines,  # noqa: F401
    projections,  # noqa: F401
    solve,  # noqa: F401
    squad,  # noqa: F401
    watchlist,  # noqa: F401
)


def tool_names() -> list[str]:
    """Every registered tool name, sorted.

    Reads the tool manager rather than a hand-kept list, so the parity test
    and the console script both see what the server actually offers.
    """
    return sorted(t.name for t in mcp._tool_manager.list_tools())
