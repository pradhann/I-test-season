#!/usr/bin/env bash
# Land the odds backfill whenever the single-writer lock frees.
# Six concurrent model teams contend for the warehouse; this is why the first
# odds ingestion silently failed to persist.
set -u
cd "$(dirname "$0")/.."
for attempt in $(seq 1 60); do
  if uv run python scripts/ingest_odds.py --history 2022-23 2023-24 2024-25 2025-26 >/tmp/odds_hist.log 2>&1; then
    echo "history OK on attempt $attempt"
    uv run python scripts/ingest_odds.py --fixtures >>/tmp/odds_hist.log 2>&1 \
      && echo "fixtures OK" || echo "fixtures failed (may be published closer to the deadline)"
    exit 0
  fi
  sleep 20
done
echo "gave up after 60 attempts"; tail -5 /tmp/odds_hist.log; exit 1
