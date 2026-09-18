.PHONY: help install test test-all lint ingest ingest-odds ingest-content intel rules-doc solve oracle weekly audit platform platform-test clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Create the venv and install the package with dev extras
	uv venv --python 3.11
	uv pip install -e ".[dev]"

test:  ## Run the offline test suite (no network)
	FPL_EDGE_GUARD_LIVE_DB=strict uv run pytest -q

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

# --------------------------------------------------------------------------
# Scheduling moved to the server. DEPLOYMENT.md §2.1 and §2.4: the deadline
# DAG and the settlement chain now run as one in-process asyncio task inside
# the deployed service, because DuckDB permits one writer per file and Railway
# attaches a volume to one service. The two scheduling plists are retired and
# `undeploy` / `undeploy-dag` are kept so the owner can unload whatever is
# still installed on this Mac. The Mac's one remaining scheduled job is the
# ASR worker, which owns no warehouse and writes over HTTP.
# --------------------------------------------------------------------------

.PHONY: undeploy undeploy-dag
undeploy:  ## Remove the retired settlement launchd service from this Mac
	launchctl unload ~/Library/LaunchAgents/com.fpledge.postgw.plist 2>/dev/null || true
	rm -f ~/Library/LaunchAgents/com.fpledge.postgw.plist
	@echo "settlement runs on the server now, as the post_gw_settlement registry task."

undeploy-dag:  ## Remove the retired deadline-DAG launchd service from this Mac
	launchctl unload ~/Library/LaunchAgents/com.fpledge.dag.plist 2>/dev/null || true
	rm -f ~/Library/LaunchAgents/com.fpledge.dag.plist
	@echo "the tick runs on the server now, in fpl_edge/platform/scheduler.py."

.PHONY: deploy-transcribe undeploy-transcribe transcribe-once
deploy-transcribe:  ## Install the Mac ASR worker as a launchd service (nightly 12:00 UTC)
	@test -n "$(FPL_EDGE_BASE_URL)" || \
	  (echo "set FPL_EDGE_BASE_URL to the deployed service, e.g."; \
	   echo "  make deploy-transcribe FPL_EDGE_BASE_URL=https://fpl-edge.up.railway.app"; \
	   exit 1)
	@security find-generic-password -s fpl-edge-transcript-push -w >/dev/null 2>&1 || \
	  (echo "no keychain item fpl-edge-transcript-push. Add the bearer token with:"; \
	   echo "  security add-generic-password -s fpl-edge-transcript-push -a \"$$USER\" -w"; \
	   exit 1)
	mkdir -p ~/Library/LaunchAgents ~/Library/Logs/fpledge
	sed "s|__BASE_URL__|$(FPL_EDGE_BASE_URL)|g" deploy/com.fpledge.transcribe.plist \
	  > ~/Library/LaunchAgents/com.fpledge.transcribe.plist
	launchctl unload ~/Library/LaunchAgents/com.fpledge.transcribe.plist 2>/dev/null || true
	launchctl load ~/Library/LaunchAgents/com.fpledge.transcribe.plist
	@echo "ASR worker: nightly 12:00 UTC against $(FPL_EDGE_BASE_URL)."

undeploy-transcribe:  ## Remove the Mac ASR worker service
	launchctl unload ~/Library/LaunchAgents/com.fpledge.transcribe.plist 2>/dev/null || true
	rm -f ~/Library/LaunchAgents/com.fpledge.transcribe.plist

transcribe-once:  ## Run one ASR worker pass by hand against the deployed service
	@test -n "$(FPL_EDGE_BASE_URL)" || \
	  (echo "set FPL_EDGE_BASE_URL to the deployed service"; exit 1)
	uv run python scripts/mac_transcribe_worker.py \
	  --base-url "$(FPL_EDGE_BASE_URL)" --once

.PHONY: deploy-check
deploy-check:  ## Build the image and prove it boots and serves on an empty volume
	uv run python scripts/deploy_check.py

.PHONY: dag-tick dag-status
dag-tick:  ## Run one DAG tick by hand (idempotent; will not double-send)
	uv run python -m fpl_edge.jobs.deadline_dag --once

dag-status:  ## What the DAG has fired, newest first (reads the LOCAL warehouse)
	uv run python -c "from fpl_edge.store import Warehouse; \
	  print(Warehouse.read_copy().sql('SELECT task, gw, due_utc, outcome, detail FROM dag_firing ORDER BY due_utc DESC LIMIT 20').to_string())"
