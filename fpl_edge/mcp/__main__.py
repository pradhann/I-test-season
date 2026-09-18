"""Run the MCP server over stdio.

    uv run python -m fpl_edge.mcp
    uv run fpl-mcp

Claude desktop launches it as the project venv's interpreter with
``-m fpl_edge.mcp``, from an arbitrary working directory and a minimal
environment. ``fpl_edge`` is installed editable in that venv, so the module
resolves from any directory; ``uv run`` would have to find a project from
wherever the client happened to start it.

``FPL_EDGE_HOME`` is the only variable that changes anything here, and even it
is optional: the context defaults to the checkout this file lives in. No
``ANTHROPIC_`` variable is read, no FPL token file is opened, and no
credential is logged.

THE GUARD IS LOAD-BEARING

Everything below runs only under ``if __name__ == "__main__"``. The audit
suite walks every module under ``fpl_edge/`` and imports it, and a module that
starts a stdio server at import time would hang that walk rather than fail it.
``tests/unit/test_subprocess_entry_points.py`` records the inverse failure: a
module with no guard at all imports, defines its main and exits zero having
done nothing, which a scheduler reads as success.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fpl-mcp",
        description=(
            "The FPL edge MCP server: one toolbelt over the same panels the "
            "web UI reads. Runs on stdio and blocks until the client closes."
        ),
    )
    parser.add_argument(
        "--list-tools", action="store_true",
        help="print every registered tool name and exit, without serving.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help=(
            "print the resolved warehouse path, checkout and user context, "
            "then exit. Non-zero when the warehouse cannot be read."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build the server and serve stdio, or answer a flag and exit.

    Returns a process exit code rather than calling ``sys.exit`` itself, so a
    test can call it.
    """
    args = build_parser().parse_args(argv)

    # Imported inside main so that --help costs nothing: building the server
    # imports twelve tool modules and every panel behind them.
    from fpl_edge.mcp import context
    from fpl_edge.mcp.server import SERVER_NAME, mcp, tool_names

    if args.list_tools:
        names = tool_names()
        print(f"{SERVER_NAME}: {len(names)} tools")
        for name in names:
            print(name)
        return 0

    if args.check:
        ctx = context.user_context()
        print(f"server      {SERVER_NAME}")
        print(f"checkout    {context.engine_home()}")
        print(f"warehouse   {context.db_path()}")
        print(f"user        {ctx.user_id}, entry {ctx.entry_id}")
        print(f"tools       {len(tool_names())}")
        missing = context.warehouse_missing()
        if missing:
            print(f"PROBLEM     {missing}")
            return 2
        return 0

    mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
