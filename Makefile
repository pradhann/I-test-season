.PHONY: help install test test-all lint ingest ingest-odds ingest-content intel rules-doc solve oracle weekly audit platform platform-test clean

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

platform:  ## Serve the decision platform on http://127.0.0.1:8321
	uv run python -m fpl_edge.cli.main platform serve --port 8321

platform-test:  ## Offline tests for the platform spine
	uv run pytest tests/unit/test_platform_*.py -q

clean:
	rm -f data/warehouse/*.duckdb data/warehouse/*.wal

.PHONY: deploy undeploy
deploy:  ## Install the nightly settlement as a launchd service
	mkdir -p data/warehouse/jobs ~/Library/LaunchAgents
	cp deploy/com.fpledge.postgw.plist ~/Library/LaunchAgents/
	launchctl unload ~/Library/LaunchAgents/com.fpledge.postgw.plist 2>/dev/null || true
	launchctl load ~/Library/LaunchAgents/com.fpledge.postgw.plist
	@echo "settlement: daily 03:00 local."
	@echo "remove with: make undeploy"

undeploy:  ## Remove the launchd service
	launchctl unload ~/Library/LaunchAgents/com.fpledge.postgw.plist 2>/dev/null || true
	rm -f ~/Library/LaunchAgents/com.fpledge.postgw.plist

.PHONY: deploy-dag undeploy-dag dag-tick dag-status
deploy-dag:  ## Install the deadline DAG as a launchd service (10-minute tick)
	mkdir -p data/warehouse/jobs ~/Library/LaunchAgents
	cp deploy/com.fpledge.dag.plist ~/Library/LaunchAgents/
	launchctl unload ~/Library/LaunchAgents/com.fpledge.dag.plist 2>/dev/null || true
	launchctl load ~/Library/LaunchAgents/com.fpledge.dag.plist
	@echo "dag: every 600s. T-30h / 02:00 UK / T-4h / T-90m off dim_event deadlines."
	@echo "next due times: make dag-tick"

undeploy-dag:  ## Remove the deadline DAG service
	launchctl unload ~/Library/LaunchAgents/com.fpledge.dag.plist 2>/dev/null || true
	rm -f ~/Library/LaunchAgents/com.fpledge.dag.plist

dag-tick:  ## Run one DAG tick by hand (idempotent; will not double-send)
	uv run python -m fpl_edge.jobs.deadline_dag --once

dag-status:  ## What the DAG has fired, newest first
	@launchctl list | grep com.fpledge.dag || echo "com.fpledge.dag not loaded"
	uv run python -c "from fpl_edge.store import Warehouse; \
	  print(Warehouse.read_copy().sql('SELECT task, gw, due_utc, outcome, detail FROM dag_firing ORDER BY due_utc DESC LIMIT 20').to_string())"
