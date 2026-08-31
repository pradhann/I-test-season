-- Understat player profiles, fetched on demand and cached append-only
-- (CHAT_ARCHITECTURE §6). Owned by fpl_edge/ingest/understat.py -- these
-- tables are NOT in store.PIT_KEYS on purpose (see projections/store.py for
-- the precedent and its reasons). as_of is the FETCH INSTANT; Understat
-- revises xG after re-processing, so a re-fetch is a new fact, never an
-- overwrite, and reads take the latest row per entity at or before an instant.

-- One player in one Understat match. match_id is UNDERSTAT'S match key: the
-- same real-world match seen by another source is a different entity, and no
-- join to our fixture ids happens at write time. Numbers are copied verbatim
-- from their model -- this is Understat's xG, never FPL points.
CREATE TABLE IF NOT EXISTS understat_player_match (
    understat_id INTEGER NOT NULL,
    code         INTEGER NOT NULL,      -- our PlayerCode, resolved at write
    season       VARCHAR NOT NULL,      -- OUR label, '2026-27'
    match_id     INTEGER NOT NULL,      -- understat's own match id
    date         DATE    NOT NULL,
    minutes      INTEGER NOT NULL,
    shots        INTEGER NOT NULL,
    goals        INTEGER NOT NULL,
    assists      INTEGER NOT NULL,
    key_passes   INTEGER NOT NULL,
    npg          INTEGER NOT NULL,      -- non-penalty goals
    xg           DOUBLE  NOT NULL,
    xa           DOUBLE  NOT NULL,
    npxg         DOUBLE  NOT NULL,
    position     VARCHAR,               -- understat's ('FW', 'Sub', ...); 'Sub' = did not start
    h_team       VARCHAR,
    a_team       VARCHAR,
    h_goals      INTEGER,
    a_goals      INTEGER,
    as_of        TIMESTAMPTZ NOT NULL
);

-- How a PlayerCode was placed on Understat, and on what basis -- so a
-- surprising attribution can be traced without re-running the resolver.
-- resolved_basis is 'exact' or 'containment'; there is no edit-distance basis
-- because there is no edit-distance path.
CREATE TABLE IF NOT EXISTS understat_player_map (
    code            INTEGER NOT NULL,
    understat_id    INTEGER NOT NULL,
    understat_name  VARCHAR NOT NULL,
    understat_team  VARCHAR,
    resolved_basis  VARCHAR NOT NULL,
    as_of           TIMESTAMPTZ NOT NULL
);
