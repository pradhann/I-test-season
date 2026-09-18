# `fpl_edge/mcp/`: the toolbelt

One MCP server, 35 tools, over the same registered panels the web UI reads. A
read tool is an adapter: it resolves a name, runs a panel through
`registry.run_script`, and returns the envelope (`result`, `provenance`,
`budget`, and `gap` when the panel served a reason instead of data). No tool
holds its own SQL, its own HTTP client, or its own answer to where the
warehouse is.

The design, the tool-by-tool mapping and the rules every tool follows are in
`docs/platform/MCP.md`. This file is what the owner needs to run it.

## Running it

```bash
uv run python -m fpl_edge.mcp        # stdio, blocks until the client closes
uv run fpl-mcp                       # the console script, same thing
uv run fpl-mcp --list-tools          # print the registered names and exit
```

`--list-tools` is the quickest check that a change did not take the server
down: it imports every tool module, so an unresolvable type annotation fails
here rather than in the middle of a chat turn.

## Claude desktop

Paste this into
`~/Library/Application Support/Claude/claude_desktop_config.json`, replacing
any earlier `fpl-server` block, and restart Claude desktop:

```json
{
  "mcpServers": {
    "fpl-server": {
      "command": "/Users/nripeshpradhan/Documents/Github/i-test-season/.venv/bin/python",
      "args": ["-m", "fpl_edge.mcp"],
      "env": {
        "FPL_EDGE_HOME": "/Users/nripeshpradhan/Documents/Github/i-test-season"
      }
    }
  }
}
```

Three details are load-bearing.

**The server name stays `fpl-server`.** `fpl_edge/platform/chat_agent.py`
builds its allowlist as `mcp__fpl-server__{name}`, and saved Claude desktop
conversations refer to tools by those qualified names. Renaming the block
silently drops every tool from the chat's allowlist.

**The command is the project venv's interpreter, not `uv`.** Claude desktop
launches the process with an arbitrary working directory and a minimal
environment. `uv run` would have to resolve a project from that directory. The
venv python has `fpl_edge` installed editable, so `-m fpl_edge.mcp` resolves
from anywhere.

**`FPL_EDGE_HOME` is the only variable set, and even it is optional.**
`context.py` defaults to the checkout this package lives in. There is no
`ANTHROPIC_*` variable, no FPL token, no `PYTHONPATH`. The server holds no key:
it reads a warehouse and writes through the engine's own interfaces.

`FPL_EDGE_DB` overrides the warehouse path, for a copy or a test. When the file
is missing, every tool says so with the path it looked at instead of returning
an empty result.

## Who it answers for

`context.user_context()` asks `fpl_edge.platform.users.owner_context()` once at
startup and caches it for the life of the process. `entry_id` is never a tool
parameter, so an MCP caller cannot ask this server for somebody else's squad by
passing a number; the panels that need one are handed it from here.

## Adding a tool

Write the adapter in the module its domain belongs to, register it with
`@mcp.tool()`, and add its name and panel to the table in `docs/platform/MCP.md`
section 3.2. The contract test in `tests/unit/test_mcp_tool_contract.py` fails
on a tool that is registered and undeclared, on a read tool naming a panel the
registry does not have, on a tool module that imports the network or the ingest
layer, and on any tool that takes an `entry_id`.
