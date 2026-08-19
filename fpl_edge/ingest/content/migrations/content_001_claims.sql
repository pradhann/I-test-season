-- Creator content: items, structured claims, resolved outcomes, earned weights.
--
-- This migration lives under fpl_edge/ingest/content/ rather than in
-- fpl_edge/store/schema.sql on purpose, following the precedent set by
-- fpl_edge/interfaces/migrations/. schema.sql is the shared contract that the
-- store, model and optimiser teams all read; adding content-owned tables there
-- would make every one of those teams' migrations conflict with ours. The
-- runner in fpl_edge/ingest/content/store.py applies this file idempotently
-- against the same DuckDB file, so there is one warehouse and one connection --
-- just another owner of DDL. Version strings are prefixed `content_` so they
-- can never collide with `001_idea_registry` in schema_migration.
--
-- These tables are deliberately NOT registered in store.PIT_KEYS.
--
-- PIT_KEYS is for facts about the world, read through Snapshot, keyed by
-- (entity, as_of) so the latest value at an instant can be selected. A claim is
-- not that shape. It is an immutable utterance: it was made once, at one
-- instant, and it never gets a newer version. Modelling it as (entity, as_of)
-- would imply a claim can be superseded, and the whole point of a track record
-- is that it cannot.
--
-- The point-in-time discipline is enforced instead by `published_at`, which is
-- the only column any reader is permitted to filter on. See
-- content.store.claims_visible_at, which is the sanctioned read path, and the
-- test in tests/unit/test_content_pit.py that proves a claim published after a
-- deadline is invisible to a snapshot taken at that deadline.

CREATE TABLE IF NOT EXISTS schema_migration (
    version     VARCHAR PRIMARY KEY,
    applied_utc TIMESTAMPTZ NOT NULL,
    sha256      VARCHAR NOT NULL
);

-- The registry as it was when we last ran, so a source that is later dropped or
-- whose access policy changes leaves a trace rather than vanishing.
CREATE TABLE IF NOT EXISTS content_source (
    source_key   VARCHAR PRIMARY KEY,
    creator      VARCHAR NOT NULL,
    kind         VARCHAR NOT NULL,   -- youtube | podcast | blog | forum | social
    url          VARCHAR NOT NULL,
    policy       VARCHAR NOT NULL,   -- open | oauth_only | forbidden
    note         VARCHAR,
    last_probe_utc    TIMESTAMPTZ,
    last_http_status  INTEGER,       -- the REAL code, including 403/404/500
    last_items        INTEGER,
    last_error        VARCHAR
);

-- One video, episode or article. `text` is stored so a claim can be re-derived
-- and audited without re-fetching content that may have been edited or deleted.
CREATE TABLE IF NOT EXISTS content_item (
    item_id      VARCHAR PRIMARY KEY,
    source_key   VARCHAR NOT NULL,
    creator      VARCHAR NOT NULL,
    kind         VARCHAR NOT NULL,
    title        VARCHAR NOT NULL,
    url          VARCHAR NOT NULL,
    -- The instant the creator made this public. THE load-bearing column of this
    -- entire package: every read filters on it, and a claim whose item was
    -- published after a deadline must never inform that deadline.
    published_at TIMESTAMPTZ NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL,
    text_source  VARCHAR NOT NULL,   -- description | transcript | article
    text         VARCHAR NOT NULL,
    text_sha256  VARCHAR NOT NULL
);

-- The deliverable. Immutable; never updated after insert.
CREATE TABLE IF NOT EXISTS content_claim (
    claim_id     VARCHAR PRIMARY KEY,
    item_id      VARCHAR NOT NULL,
    creator      VARCHAR NOT NULL,
    source_key   VARCHAR NOT NULL,
    -- Stable cross-season PlayerCode, never element_id. A creator's 2023-24
    -- claim about Rice and their 2025-26 claim about him must be the same
    -- player or the track record measures nothing.
    player_code  INTEGER NOT NULL,
    player_name  VARCHAR NOT NULL,   -- as matched, for audit
    surface_form VARCHAR NOT NULL,   -- as spoken/written, verbatim
    action       VARCHAR NOT NULL,   -- buy|sell|hold|captain|triple_captain|bench|avoid
    season       VARCHAR NOT NULL,
    gameweek     INTEGER NOT NULL,
    confidence   DOUBLE NOT NULL,    -- how firmly it was said, not how right it is
    rationale    VARCHAR NOT NULL,
    source_url   VARCHAR NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    gw_inferred  BOOLEAN NOT NULL    -- gameweek deduced from published_at
);

-- What happened. Written only once the gameweek has finalised, which is why the
-- table is separate: the claim is a fact about the past the moment it is made,
-- the outcome is not knowable until later.
CREATE TABLE IF NOT EXISTS claim_outcome (
    claim_id        VARCHAR PRIMARY KEY,
    creator         VARCHAR NOT NULL,
    season          VARCHAR NOT NULL,
    gameweek        INTEGER NOT NULL,
    player_code     INTEGER NOT NULL,
    action          VARCHAR NOT NULL,
    player_points   DOUBLE,
    benchmark       VARCHAR NOT NULL,  -- which comparator was used
    benchmark_points DOUBLE,
    hit             BOOLEAN,           -- NULL when the claim is unscoreable
    unscoreable     VARCHAR,           -- reason, when hit IS NULL
    resolved_utc    TIMESTAMPTZ NOT NULL
);

-- The earned weight. Recomputed from scratch each run and stamped with as_of,
-- so the weight a decision used is recoverable after the fact.
CREATE TABLE IF NOT EXISTS creator_score (
    creator          VARCHAR NOT NULL,
    scope            VARCHAR NOT NULL,  -- 'all' | one action name
    as_of            TIMESTAMPTZ NOT NULL,
    claims_total     INTEGER NOT NULL,
    claims_scored    INTEGER NOT NULL,
    hits             INTEGER NOT NULL,
    hit_rate         DOUBLE,
    wilson_lo95      DOUBLE,            -- lower bound; the honest number
    -- Weight the model is permitted to give this creator. Zero unless the
    -- lower bound clears 0.5, i.e. unless the creator has DEMONSTRATED an edge
    -- over the coin flip at this sample size. An unweighted consensus is the
    -- template with extra steps.
    weight           DOUBLE NOT NULL,
    first_claim_utc  TIMESTAMPTZ,
    last_claim_utc   TIMESTAMPTZ,
    PRIMARY KEY (creator, scope, as_of)
);
