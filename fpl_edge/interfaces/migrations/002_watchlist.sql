-- Watchlist: players the user has said they want to keep an eye on.
--
-- Deliberately NOT an idea. An idea is a falsifiable thesis with a comparator
-- and a settlement window; "keep an eye on Palmer" has neither and forcing it
-- through the thesis machinery would either invent a claim the user never made
-- or void on schedule. A watchlist item is a standing reminder: append it,
-- surface it in every pre-deadline digest, resolve it when the user moves on.
--
-- Same DDL-ownership rule as 001: interface-owned table, applied idempotently
-- by the runner in fpl_edge/interfaces/registry.py, recorded in
-- schema_migration. Not in store.PIT_KEYS -- like ideas, this is a record of
-- what the user wanted, not a fact about the world.
--
-- Append/resolve semantics: rows are never deleted. Removing a player marks
-- the row resolved with a timestamp, so "when did I stop caring about X" stays
-- answerable.

CREATE TABLE IF NOT EXISTS watchlist (
    item_id      VARCHAR PRIMARY KEY,
    created_utc  TIMESTAMPTZ NOT NULL,
    season       VARCHAR NOT NULL,
    code         INTEGER NOT NULL,     -- stable PlayerCode, never element_id
    player_name  VARCHAR NOT NULL,     -- display name at add time
    note         VARCHAR,              -- UNTRUSTED user text, stored verbatim
    source       VARCHAR NOT NULL,     -- 'mcp' | 'cli' | 'telegram' | 'test'
    resolved     BOOLEAN NOT NULL,
    resolved_utc TIMESTAMPTZ
);
