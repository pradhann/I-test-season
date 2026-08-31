"""
Configure the FPL MCP server instance.

Creates the shared ``FastMCP`` server named ``fpl_mcp`` and imports the tool
modules so their decorated functions register with it.

``mcp`` is a declared dependency of this repository, so it is imported like any
other library. There is no vendored SDK and no ``sys.path`` surgery: the
toolbelt runs on the same interpreter as the engine it serves.

Do not run this module directly -- use ``uv run python -m fpl_mcp``.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


# Create the shared MCP server instance.
mcp = FastMCP("fpl_mcp")


# Import tools so that their decorators and prompts register functions
# with the server.  Use absolute imports rather than package-relative
# ones so that the code works when run from the project root.
# pylint: disable=unused-import
# Import tool modules so their decorated functions register with the server.
from fpl_mcp.tools import team_tools  # noqa: F401
from fpl_mcp.tools import general_tools  # noqa: F401
from fpl_mcp.tools import prompts  # noqa: F401
from fpl_mcp.tools import video_tools  # noqa: F401
from fpl_mcp.tools import transcript_tools  # noqa: F401
from fpl_mcp.tools import expert_tools  # noqa: F401

# fpl-edge decision engine: the idea inbox, the review and the weekly report.
# Imported last and guarded inside the module itself, so a missing or broken
# engine checkout degrades those tools to an explanatory message rather than
# preventing this server from starting at all.
from fpl_mcp.tools import edge_tools  # noqa: F401
from fpl_mcp.tools import viz_tools  # noqa: F401 - python_viz (CHAT_ARCHITECTURE §4)

# The semantic layer: six PIT-parameterised warehouse macros (sem_players,
# sem_projections, sem_projection_consensus, sem_player_form, sem_ownership,
# sem_fixtures) exposed as thin data-query tools. Imported after edge_tools
# because it reuses its engine-locating helpers; guarded the same way.
from fpl_mcp.tools import semantic_tools  # noqa: F401

# fpl-edge player dossier and news/tactical intel: player_dossier, player_intel
# and set_piece_changes. Guarded the same way as edge_tools -- a missing or
# broken engine checkout degrades these tools to an explanatory message rather
# than preventing this server from starting.
from fpl_mcp.tools import dossier_tools  # noqa: F401

# fpl-edge creator content intelligence: structured claims from podcasts, blogs
# and YouTube, deduplicated into a consensus map weighted by each creator's
# MEASURED hit rate. Guarded the same way as edge_tools.
from fpl_mcp.tools import content_tools  # noqa: F401

# The chat toolbelt for the Argus-style agent: free SQL through the engine's
# guarded_query, server-side charts (CHART_SAVED / [chart:<id>] contract with
# the chat pane), the real transfer recommendation, saved parameterised
# analyses (git-committed in the engine repo), the watchlist and named-manager
# lookup. Reuses edge_tools' engine location and semantic_tools' player
# resolution; guarded the same way.
from fpl_mcp.tools import chat_tools  # noqa: F401

# Authentication and transfer tools
# Authentication and transfer tools have been removed.  They were
# experimental and are no longer part of this project.  To avoid
# import errors, we do not import them here.

# The imported modules register their tools and prompts via
# decorators.  No further action is required here.
