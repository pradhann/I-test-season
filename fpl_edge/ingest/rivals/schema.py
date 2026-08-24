"""Warehouse tables for other people's teams, added without touching schema.sql.

``fpl_edge/store/schema.sql`` is owned elsewhere and describes the *game*:
players, fixtures, events, results. What a rival manager did is a different kind
of fact -- it is an observation of a person, not of the competition -- and it
arrives on a different schedule from a different set of endpoints. Keeping it in
its own additive migration means this package can be deleted wholesale without
leaving orphan columns in the core schema, and that a warehouse built before
this package existed upgrades in place rather than needing a rebuild.

Point-in-time discipline is inherited, not relaxed. ``as_of`` on every row is the
instant the fact became **publicly observable**, which for manager picks is the
gameweek deadline (FPL makes a squad public the moment it locks), not the
instant we happened to crawl it. That distinction is the difference between a
backtest that copies the elite and a backtest that copies the elite *after
seeing their scores*.

Registration into ``PIT_KEYS``
-----------------------------
``Warehouse.append`` and ``Snapshot.table`` both refuse tables they do not know
about. ``PIT_KEYS`` is a module-level registry, so this module extends it at
import time rather than editing the core file. The entity key for each table is
the tuple that identifies one logical thing, excluding ``as_of`` -- the same
contract the core tables follow, so the "latest row per entity at or before T"
read works on these tables unchanged.
"""

from __future__ import annotations

from fpl_edge.store.warehouse import PIT_KEYS, Warehouse

#: Entity keys for the rival tables, in the shape ``PIT_KEYS`` expects.
RIVAL_PIT_KEYS: dict[str, tuple[str, ...]] = {
    "dim_manager": ("entry_id",),
    "dim_manager_league": ("entry_id", "league_id"),
    "fact_manager_season": ("entry_id", "season"),
    "fact_manager_gw": ("entry_id", "season", "gw"),
    "fact_manager_pick": ("entry_id", "season", "gw", "element_id"),
    "fact_manager_transfer": ("entry_id", "season", "gw", "element_in", "element_out"),
    "fact_manager_chip": ("entry_id", "season", "gw"),
}

PIT_KEYS.update(RIVAL_PIT_KEYS)


#: NOTE: these tables are ALSO declared in fpl_edge/store/schema.sql. DuckDB
#: binds a table macro at CREATE time, so the sem_manager_* macros in
#: views.sql (run on every writable open) need the tables to exist at
#: warehouse birth -- the same arrangement the projection tables use. This
#: module remains the documentation of record and the evolution authority:
#: migrate() ALTER-adds any column declared here that the base is missing.
DDL = """
-- Who a manager is. `source` records WHY they are in the pool, because a skill
-- score computed over a pool selected on past performance is biased, and the
-- only defence is being able to reproduce the selection rule later.
CREATE TABLE IF NOT EXISTS dim_manager (
    entry_id            BIGINT   NOT NULL,
    player_name         VARCHAR,
    entry_name          VARCHAR,
    region              VARCHAR,
    years_active        INTEGER,
    favourite_team_id   INTEGER,
    started_event       INTEGER,
    source              VARCHAR  NOT NULL,   -- 'expert' | 'mini_league:<id>' | 'snowball:<id>'
    as_of               TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, as_of)
);

-- League memberships, which is how the crawl snowballs from a handful of known
-- names to a pool: elite managers cluster in the same invitational leagues.
CREATE TABLE IF NOT EXISTS dim_manager_league (
    entry_id     BIGINT  NOT NULL,
    league_id    BIGINT  NOT NULL,
    league_name  VARCHAR,
    league_type  VARCHAR,               -- 's' = system/auto, 'x' = invitational
    scoring      VARCHAR,
    as_of        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, league_id, as_of)
);

-- One finished season per row. This is the ONLY long-run skill evidence the API
-- exposes: there is no public archive of a past season's picks, so a manager's
-- record is a sequence of final ranks and nothing more granular.
CREATE TABLE IF NOT EXISTS fact_manager_season (
    entry_id        BIGINT  NOT NULL,
    season          VARCHAR NOT NULL,   -- FPL's own label, e.g. '2024/25'
    total_points    INTEGER,
    overall_rank    BIGINT,
    -- FPL's own rounded percentile. The TEXT column is not redundant: the
    -- number of decimals FPL printed IS the rounding unit ('46' means the true
    -- value is in [45.5, 46.5), '1.0' means [0.95, 1.05)), and that unit is what
    -- brackets the season's field size. Parsing to a float first throws the
    -- precision away and turns a tight bracket into a ten-times-wider one.
    rank_percentage      DOUBLE,
    rank_percentage_text VARCHAR,
    as_of           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, season, as_of)
);

-- Per-gameweek record for the CURRENT season. Carries the two fields that make
-- hit-taking measurable (event_transfers, event_transfers_cost) and the two
-- that make squad value trackable.
CREATE TABLE IF NOT EXISTS fact_manager_gw (
    entry_id              BIGINT  NOT NULL,
    season                VARCHAR NOT NULL,
    gw                    INTEGER NOT NULL,
    points                INTEGER,
    total_points          INTEGER,
    overall_rank          BIGINT,
    bank_tenths           INTEGER,
    value_tenths          INTEGER,
    event_transfers       INTEGER,
    event_transfers_cost  INTEGER,
    points_on_bench       INTEGER,
    as_of                 TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, season, gw, as_of)
);

-- A squad, as it locked at the deadline. `multiplier` is the FPL multiplier
-- (0 benched, 1 started, 2 captain, 3 triple captain) and is what effective
-- ownership is actually built from -- storing a boolean 'owned' here would
-- throw away the captaincy signal this package exists to measure.
CREATE TABLE IF NOT EXISTS fact_manager_pick (
    entry_id       BIGINT  NOT NULL,
    season         VARCHAR NOT NULL,
    gw             INTEGER NOT NULL,
    element_id     INTEGER NOT NULL,   -- per-season id, joins to dim_player for `code`
    slot           INTEGER,            -- 1..15, bench order for 12..15
    multiplier     INTEGER,
    is_captain     BOOLEAN,
    is_vice_captain BOOLEAN,
    as_of          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, season, gw, element_id, as_of)
);

CREATE TABLE IF NOT EXISTS fact_manager_transfer (
    entry_id          BIGINT  NOT NULL,
    season            VARCHAR NOT NULL,
    gw                INTEGER NOT NULL,
    element_in        INTEGER NOT NULL,
    element_in_cost   INTEGER,
    element_out       INTEGER NOT NULL,
    element_out_cost  INTEGER,
    time_utc          TIMESTAMPTZ,
    as_of             TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, season, gw, element_in, element_out, as_of)
);

-- One chip per manager per gameweek (the rules forbid two), so the gameweek is
-- part of the entity key and a second chip in the same week is a contradiction
-- the warehouse will reject rather than silently keep one of.
CREATE TABLE IF NOT EXISTS fact_manager_chip (
    entry_id  BIGINT  NOT NULL,
    season    VARCHAR NOT NULL,
    gw        INTEGER NOT NULL,
    chip      VARCHAR NOT NULL,        -- 'wildcard' | 'freehit' | 'bboost' | '3xc'
    as_of     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entry_id, season, gw, as_of)
);
"""


def migrate(wh: Warehouse) -> None:
    """Create the rival tables if they are absent. Idempotent and additive.

    Safe to call on every ingest run, and safe to call on a warehouse that
    already has them: nothing here drops, renames or rewrites anything, so a
    reader on an older build of this package still sees a superset of what it
    expects.
    """
    for statement in _statements(DDL):
        wh.sql(statement)

    # CREATE TABLE IF NOT EXISTS never adds a column to a table that already
    # exists, so a warehouse created by an older build of this module keeps
    # silently missing whatever was added since -- which surfaces as a binder
    # error mid-append, after the network budget has already been spent.
    # Diff the declared columns against the live table and ALTER-add the gap.
    import re

    for block in re.finditer(
        r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\)\s*;", DDL, re.S
    ):
        table, body = block.group(1), block.group(2)
        declared: dict[str, str] = {}
        for raw_line in body.splitlines():
            line = raw_line.split("--")[0].strip().rstrip(",")
            m = re.match(r"^(\w+)\s+([A-Z][A-Z0-9_() ]*?)(?:\s+NOT NULL)?$", line)
            if m and m.group(1).upper() not in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK"):
                declared[m.group(1)] = m.group(2).strip()
        existing = {
            r[0] for r in wh.sql(
                "SELECT column_name FROM information_schema.columns "
                f"WHERE table_name = '{table}'"
            ).itertuples(index=False)
        }
        for name, sqltype in declared.items():
            if name not in existing:
                wh.sql(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}")


def _statements(ddl: str) -> list[str]:
    """Split DDL into executable statements, ignoring `--` comments.

    Naively splitting on ";" is wrong the moment a comment contains one, and it
    fails as a parser error three frames away from the typo that caused it.
    Comments are stripped before splitting and the original text is executed, so
    the documentation stays in the file and out of the parser's way.
    """
    out: list[str] = []
    buf: list[str] = []
    for line in ddl.splitlines():
        code = line.split("--", 1)[0]
        buf.append(line)
        if ";" in code:
            out.append("\n".join(buf))
            buf = []
    if any(ln.split("--", 1)[0].strip() for ln in buf):
        out.append("\n".join(buf))
    return out


def rival_tables_present(wh: Warehouse) -> set[str]:
    """Which rival tables the connected database actually has.

    Used by readers, which may be opened read-only and therefore cannot run the
    migration themselves. Reporting an empty analysis is a better failure than a
    ``CatalogException`` from three call frames down.
    """
    rows = wh.sql(
        "SELECT table_name FROM information_schema.tables WHERE table_name IN "
        "(" + ", ".join(f"'{t}'" for t in RIVAL_PIT_KEYS) + ")"
    )
    return set(rows["table_name"]) if not rows.empty else set()
