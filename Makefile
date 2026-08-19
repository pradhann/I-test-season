.PHONY: help install test test-all lint ingest ingest-odds ingest-content intel rules-doc solve oracle weekly audit clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Create the venv and install the package with dev extras
	uv venv --python 3.11
	uv pip install -e ".[dev]"

test:  ## Run the offline test suite (no network)
	uv run pytest -q

test-all:  ## Include network-marked tests
	uv run pytest -q -m ""

lint:  ## Ruff + mypy
	uv run ruff check fpl_edge tests
	uv run mypy fpl_edge --ignore-missing-imports

ingest:  ## Pull the live FPL API into the warehouse
	uv run python scripts/ingest_live.py

ingest-odds:  ## Bookmaker odds: history + fixtures + Odds API player props
	uv run python scripts/ingest_odds.py --fixtures
	uv run python scripts/ingest_odds.py --odds-api --max-credits 30

ingest-content:  ## Creator content: fetch, extract claims, score track records
	uv run python -m fpl_edge.ingest.content.pipeline ingest --backfill-days 14
	uv run python -m fpl_edge.ingest.content.pipeline score

intel:  ## Set-piece duty, OOP flags, availability news
	uv run python -m fpl_edge.intel.cli collect

rules-doc:  ## Regenerate docs/rules.md from the rule registry
	uv run python scripts/render_rules_doc.py

solve:  ## Solve the squad plan for the upcoming deadline (persists artefact)
	uv run python scripts/gw1_squad.py

oracle:  ## Oracle verdicts blending model, market, ownership
	uv run python scripts/oracle_gw1.py

weekly: ingest  ## Full decision report for the upcoming deadline, from cold
	uv run python -m fpl_edge.cli.main weekly

audit:  ## Run the leakage / adversarial audit suite
	uv run pytest tests/audit -q

clean:
	rm -f data/warehouse/*.duckdb data/warehouse/*.wal
