# Fantasy Premier League MCP Server

This project provides a simple Model Context Protocol (MCP) server that
exposes a handful of tools for exploring Fantasy Premier League (FPL)
data. It follows the structure laid out in the tutorial blog, but
substitutes the original CSV/Parquet examples for live FPL API
endpoints. The goal is to make it easy to query player and team data
using natural language via Claude for Desktop or any other MCP client.

---

## Usage

To run the server locally:

1. **Run the server**

   ```bash
   python main.py
   ```

2. **Claude Desktop configuration**
   Add the following to your Claude configuration file (`claude_desktop_config.json` or equivalent), replacing `/path/to/fpl_server` with the absolute path to your local `fpl_server` directory:

   ```json
   {
     "mcpServers": {
       "fpl-server": {
         "command": "python3",
         "args": [
           "/path/to/fpl_server/main.py"
         ],
         "env": {
           "PYTHONPATH": "/path/to/fpl_server"
         }
       }
     }
   }
   ```

3. **Check in Claude Desktop**
   Restart Claude Desktop and verify that the `fpl-server` MCP server appears in the connected servers list.

## Folder Layout

```
fpl_server/
│
├── data/             # Local cache of FPL API responses
│
├── tools/            # MCP tool definitions
│   ├── __init__.py
│   ├── semantic_tools.py   # Data queries over the fpl-edge semantic layer (preferred)
│   ├── general_tools.py    # Live-API team summary
│   ├── team_tools.py       # Team picks tool (manager-specific)
│   ├── edge_tools.py       # fpl-edge idea inbox, tracking, weekly report
│   ├── dossier_tools.py    # Player dossier and news/tactical intel
│   ├── content_tools.py    # Creator content intelligence
│   └── ...                 # expert/video/transcript tools, prompts
│
├── utils/            # Reusable logic for live-API fetching
│   ├── __init__.py
│   └── fpl_data.py
│
├── server.py         # Defines the shared FastMCP server instance
├── main.py           # Entry point to run the server
└── README.md         # This file
```

### Key Concepts

* **Semantic-layer tools** (``tools/semantic_tools.py``) are the
  preferred vocabulary for data questions. The fpl-edge warehouse
  (a sibling checkout, located via ``FPL_EDGE_HOME``/``FPL_EDGE_DB``)
  carries six point-in-time table macros in the database file itself
  — ``sem_players``, ``sem_projections``, ``sem_projection_consensus``,
  ``sem_player_form``, ``sem_ownership``, ``sem_fixtures`` — and each
  tool here is a thin query over one of them: `player_projections`,
  `projection_disagreement`, `xpts_aggregate`, `player_form`,
  `fixture_difficulty` and `ownership_eo`. Every tool takes an
  optional ``as_of`` UTC instant and answers with what was knowable
  at that moment.

* **Live-API tools**: `utils/fpl_data.py` wraps the public FPL API
  with on-disk caching for the tools that genuinely need live data:
  `get_team_summary` (a club's recent W/D/L in
  ``general_tools.py``), the team picks and manager history tools
  (``team_tools.py``), and the expert/video tools.

* **FastMCP**: The server is built using `FastMCP` from the
  `mcp` SDK. Since external package installation is unavailable in
  this environment, the `mcp` library is vendored into the
  repository via a shallow clone of the official SDK. See
  `mcp_sdk/` at the project root.

To run the server locally, add the absolute path to this directory
in your Claude configuration and start the server using either
`python main.py` or, if you are using [`uv`](https://github.com/astral-sh/uv),
`uv run main.py`.  The Claude desktop configuration should specify
the `fpl_server` entry as shown in your JSON snippet.  Ensure you
have internet access so that the FPL API requests succeed.
