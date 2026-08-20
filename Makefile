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
deploy:  ## Install the Telegram bot + nightly settlement as launchd services
	mkdir -p data/warehouse/jobs ~/Library/LaunchAgents
	cp deploy/com.fpledge.telegram.plist ~/Library/LaunchAgents/
	cp deploy/com.fpledge.postgw.plist ~/Library/LaunchAgents/
	launchctl unload ~/Library/LaunchAgents/com.fpledge.telegram.plist 2>/dev/null || true
	launchctl unload ~/Library/LaunchAgents/com.fpledge.postgw.plist 2>/dev/null || true
	launchctl load ~/Library/LaunchAgents/com.fpledge.telegram.plist
	launchctl load ~/Library/LaunchAgents/com.fpledge.postgw.plist
	@echo "bot: always on (KeepAlive). settlement: daily 03:00 local."
	@echo "remove with: make undeploy"

undeploy:  ## Remove the launchd services
	launchctl unload ~/Library/LaunchAgents/com.fpledge.telegram.plist 2>/dev/null || true
	launchctl unload ~/Library/LaunchAgents/com.fpledge.postgw.plist 2>/dev/null || true
	rm -f ~/Library/LaunchAgents/com.fpledge.telegram.plist ~/Library/LaunchAgents/com.fpledge.postgw.plist
