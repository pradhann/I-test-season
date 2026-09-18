"""The MCP server, as a second surface over the panels.

Deliberately empty. ``import fpl_edge.mcp`` must cost nothing, because
``fpl_edge/platform/chat_agent.py`` imports the server from inside two
functions to keep the toolbelt's second of import time off platform startup.
Everything lives in the submodules: ``server`` builds the one FastMCP
instance, ``context`` resolves the user, ``adapter`` is the only path to
``run_script``, and ``tools/`` registers.
"""
