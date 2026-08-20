-- Deadline-DAG bookkeeping tables.
--
-- This migration deliberately does NOT live in fpl_edge/store/schema.sql. That
-- file is the shared contract every team reads; the DAG owns its own DDL and
-- applies it idempotently through the same schema_migration runner pattern as
-- fpl_edge/ingest/projections/store.py. One warehouse, several owners of DDL.
--
-- NOTE: the platform_delivery outbox table is created by
-- fpl_edge/jobs/outbox.py (CREATE TABLE IF NOT EXISTS), not here, so that the
-- SPINE team's fpl_edge/platform delivery module can take ownership of that
-- table without a migration collision.

-- One row per (task, gameweek, due instant). The row is the idempotency guard:
-- a firing is claimed by inserting the row BEFORE the task runs, so a restart,
-- an overlapping manual tick, or a double launchd dispatch can never
-- double-send. due_utc is deterministic (deadline minus offset, or the 02:00
-- Europe/London instant), so every process computes the same key.
--
-- outcome:
--   'running'       claimed, task in flight (a crash strands this; by design it
--                   is interrupted-not-retried, mirroring Argus)
--   'delivered'     task ran and enqueued at least one outbox message
--   'quiet'         task ran, deterministic trigger did not fire
--   'skipped_stale' due more than the stale window ago (machine asleep);
--                   recorded, never burst-fired
--   'no_source'     the task's data source does not exist yet (honest gap)
--   'error'         task raised; detail carries the traceback tail
CREATE TABLE IF NOT EXISTS dag_firing (
    task      VARCHAR NOT NULL,
    season    VARCHAR NOT NULL,
    gw        INTEGER NOT NULL,
    due_utc   TIMESTAMPTZ NOT NULL,
    fired_utc TIMESTAMPTZ NOT NULL,
    outcome   VARCHAR NOT NULL,
    detail    VARCHAR,
    PRIMARY KEY (task, season, gw, due_utc)
);

-- Observation series stored on EVERY radar run, quiet ones included (the Argus
-- observation-tuning pattern): exactly the numbers a threshold needs. The
-- price-radar trigger threshold is a first guess; this table is how it gets
-- tuned against what actually preceded price changes.
CREATE TABLE IF NOT EXISTS dag_observation (
    task         VARCHAR NOT NULL,
    observed_utc TIMESTAMPTZ NOT NULL,
    season       VARCHAR NOT NULL,
    code         INTEGER NOT NULL,
    metric       VARCHAR NOT NULL,
    value        DOUBLE,
    PRIMARY KEY (task, observed_utc, season, code, metric)
);
