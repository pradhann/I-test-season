-- What the analysis call actually cost, and what actually ran it.
--
-- content_analysis already has a `model` column, and it is half the primary
-- key. That column is the model the code REQUESTED: until the pin landed,
-- `claude -p` was invoked with no --model flag at all, so the request itself
-- was "whatever this CLI defaults to" and the stored id was an assertion. All
-- 799 rows in the warehouse on 2026-09-18 read claude-opus-5 because the
-- constant said so, not because anything measured it.
--
-- So the measurement gets its own column rather than overwriting the key.
-- `model` stays the requested id (validated, joinable, deduplicating); the
-- three columns below carry what the backend reported about the call.
--
-- All three are nullable and every existing row backfills to NULL, which is
-- the honest value: nobody recorded these facts when those rows were written
-- and nothing can recover them now. NULL means unknown here. It never means
-- zero, because "this call spent nothing" and "nobody told us what this call
-- spent" are different facts and a token budget has to tell them apart.

-- The id the CLI's `modelUsage` map or the SDK's `response.model` named.
-- Differs from `model` exactly when the backend ignored or overrode the
-- request, which is the case this column exists to make visible.
ALTER TABLE content_analysis ADD COLUMN IF NOT EXISTS model_reported VARCHAR;

-- Every input token the call reported: fresh, cache-creation and cache-read
-- summed. One number, one meaning. The split is not kept because the CLI's
-- own harness dominates the cached half (measured: 2 fresh input tokens
-- against 31,199 cached on a two-word prompt) and a per-row breakdown of an
-- overhead that is identical on every call buys nothing.
ALTER TABLE content_analysis ADD COLUMN IF NOT EXISTS tokens_in BIGINT;

-- Output tokens the call reported.
ALTER TABLE content_analysis ADD COLUMN IF NOT EXISTS tokens_out BIGINT;
