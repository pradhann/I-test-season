.PHONY: help install test lint ingest rules-doc weekly clean audit resolve-gw

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

rules-doc:  ## Regenerate docs/rules.md from the rule registry
	uv run python scripts/render_rules_doc.py

audit:  ## Run the leakage / adversarial audit suite
	uv run pytest tests/audit -q

weekly:  ## Produce the decision report for the upcoming deadline
	uv run python -m fpl_edge.cli.main weekly

resolve-gw:  ## Grade open theses against finalised gameweeks, update the scoreboard, commit
	uv run python -m fpl_edge.cli.main theses resolve

clean:
	rm -f data/warehouse/*.duckdb data/warehouse/*.wal
