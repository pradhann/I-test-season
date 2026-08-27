"""
Entry point for running the FPL MCP server.

Run it as a module from the repository root::

    uv run python -m fpl_mcp

The toolbelt and the engine share one interpreter and one checkout, so
there is no path shim and no second environment to keep in sync. An MCP
client config points at that same command. The server blocks until the
client terminates it.
"""

from __future__ import annotations

from fpl_mcp.server import mcp


if __name__ == "__main__":
    print("Starting FPL MCP server...")
    mcp.run()
