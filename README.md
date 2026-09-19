# fpl-edge

A rank-utility optimizing Fantasy Premier League decision engine: a
point-in-time DuckDB warehouse, a set of models over it, a solver that plans a
horizon in both expected points and P(top 10k), and a web platform that serves
the result as read-only panels.

## Start here

```bash
make install                 # create the venv and install with dev extras
make test                    # the offline suite
make platform                # serve on http://127.0.0.1:8321
```

`MVP.md` is the operator's page: the three commands that are the product and
what each one prints.

## Documentation

| Page | What it answers |
|---|---|
| [docs/platform/LAYOUT.md](docs/platform/LAYOUT.md) | Where every module lives, what it is for, and which package may import which. The table is generated from the tree. |
| [docs/platform/DEPLOYMENT.md](docs/platform/DEPLOYMENT.md) | How the server ships: the image, the volume, the environment, the open questions. |
| [docs/platform/MCP.md](docs/platform/MCP.md) | The MCP toolbelt: the tools, the envelope every one of them returns, and how a client connects. |
| [docs/platform/PIPELINES.md](docs/platform/PIPELINES.md) | What runs on a schedule, what it writes, what goes stale when it stops, and how to run one step by hand. |
| [docs/platform/DESIGN_PRINCIPLES.md](docs/platform/DESIGN_PRINCIPLES.md) | The rules the surfaces are held to: payload-led UI, numbers named for what they are, absence shown as absence. |

Further reading lives in the same directory:
[ARCHITECTURE.md](docs/platform/ARCHITECTURE.md) for what the system does,
[ARCHITECTURE_REVIEW.md](docs/platform/ARCHITECTURE_REVIEW.md) for the frozen
target layout the tree was refactored towards, and
[AUTH.md](docs/platform/AUTH.md) for the identity model.

## Layout in one screen

```
fpl_edge/
  store/    rules/                    the warehouse and the FPL rulebook
  models/   sim/   opt/   rank/       models, field simulator, solver, rank utility
  ingest/                             the outside world: API, odds, projections, content
  interfaces/                         CLI reports, dossier, ideas, Telegram
  platform/                           the web server and its panels
  pipelines/                          the task registry, contracts, health, runner
  jobs/     mcp/                      scheduled chains and the MCP toolbelt
  cli/                                the `fpl` entry point
scripts/                              one-off and generator entry points
tests/                                unit, integration, and the leakage audit suite
data/warehouse/                       fpl.duckdb and the committed artefacts
web/                                  the served front end
```

Imports run one way down that list. The rules, the named exceptions, and the
full per-module table are in
[docs/platform/LAYOUT.md](docs/platform/LAYOUT.md).
