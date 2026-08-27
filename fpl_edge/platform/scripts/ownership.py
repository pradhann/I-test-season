"""ownership_eo — the template and effective-ownership panel.

Effective ownership (EO) is the share of a cohort that *effectively* holds a
player once captaincy is counted: a player owned by 60% and captained by half
of them has 90% EO, so values above 100% are normal for premium captains.
Beside every EO metric this panel keeps FPL's own **marginal** ownership
(``selected_by_pct`` — no captaincy weighting) so the two are never confused.

Two data traps this script exists to not fall into (docs/platform/
data_audit.md Q6, both fired during the audit):

1. **Metric names.** The feed writes ``eo_predicted`` / ``eo_top10k`` /
   ``eo_elite`` — NOT the ``own_*`` names an old migration comment documents.
   A filter on the documented names returns zero rows silently.
2. **Season/gw split.** ``eo_top10k`` and ``eo_elite`` currently exist only
   under LiveFPL's last-resolved cohort — season 2025-26, GW38 — while
   ``eo_predicted`` is under the current season. A single "current season and
   gw" join silently drops two of the three metrics. Here, metrics from any
   *other* season are quarantined into ``last_season`` with their real
   season/gw stamped on them, and are never merged into the current rows.

Crawl data (``fact_manager_pick``) is used when it holds picks for the
requested season: the panel then adds observed cohort own%/EO% and states the
cohort size. When the pick tables are empty — the state at build time — the
columns stay null and ``cohort_note`` says exactly what is on file instead.

3. **The cohort denominator.** This script used to compute its own own%/EO%
   with a raw query over ``fact_manager_pick`` that had **no cohort filter**:
   its denominator was every crawled entry (1,508 in the live warehouse —
   top1k and elite blended), and it labelled the answer "elite". It now reads
   ``sem_elite_ownership``, the one place effective ownership is defined, and
   names the cohort it is reporting in ``cohort`` / ``cohort_note``. The
   ``elite_*`` row keys keep their names (the web view selects on them) and
   carry whatever cohort ``cohort`` names — ``elite`` by default.

THE FIELD LADDER (``fields`` + ``rows[].fields``)
-------------------------------------------------
The objective this engine optimises is P(top-1k), not expected points, and
(docs/platform/rank_objectives.md §0, §1)

    rank move ≈ Σ over players of (my multiplier − the field's EO) × points

so template holdings *cancel*. What a reader needs is therefore never one
ownership number: it is the **gap** between the field they are racing and the
game as a whole. This panel enumerates every field it can actually measure —
FPL's own marginal ownership, each external LiveFPL EO series, and each crawled
cohort — as ``fields``, and hangs the per-player measurements off
``rows[].fields[<key>]``. The UI picks which field to compare against which
baseline; the panel never picks for it and never mixes units.

Three honesty rules the ladder exists to keep:

* **Like is compared with like.** ``own`` (a head-count share) and ``eo``
  (a sum of FPL multipliers) are separate measures on every field. A field
  that cannot supply one leaves it ``null`` — never zero, never the other one.
* **Every percent names its denominator.** ``denominator`` on each field says
  in words what the number is a share *of*, and ``n`` gives the count where a
  count exists. FPL does not publish its entry count, so ``global`` carries
  ``n: null`` rather than a plausible-looking total.
* **A re-stamped feed is not a new gameweek.** LiveFPL republishes
  ``eo_top10k``/``eo_elite`` under the upcoming gw with byte-identical values
  (measured live: 600 of 600 codes unchanged from GW1 to GW2 on 2026-08-27).
  ``same_values_as_gw`` reports that — measured, not assumed — so the UI cannot
  caption last week's settled field as this week's forecast.

Note the naming collision the ladder has to survive: LiveFPL's ``eo_elite`` is
*LiveFPL's own* elite definition and is unrelated to this repo's crawled
``elite`` cohort. Both are carried; each field's ``label`` and ``provider``
keep them apart.

Cohort composition is disclosed for the same reason (``fields[].composition``):
the live elite cohort is 311 managers, and 49 of them are the owner's own
mini-league opponents. A cohort with a conflict of interest in it is still
usable — an undisclosed one is not.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import (
    POSITION_NAME,
    empty,
    latest_as_of,
    next_gw,
    q,
    season_param,
)

UTC = dt.timezone.utc

PARAMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        # Differentials: nobody's differential is owned by a third of the game.
        "diff_max_own": {"type": "number", "minimum": 0.5, "maximum": 100, "default": 15.0},
        # Which crawled cohort the elite_* columns report. Mutually exclusive
        # by construction (sem_manager_cohort); 'unclassified' is the crawl-bug
        # bucket — entries holding picks with no dim_manager row.
        "cohort": {"type": "string", "enum": ["elite", "top1k", "unclassified"],
                   "default": "elite"},
        # Squad coverage may touch the network (same path as squad_overview);
        # callers that must stay offline — tests — turn it off.
        "coverage": {"type": "boolean", "default": True},
    },
}

# One field's measurement of one player. Both measures are optional and
# independently nullable: a field that only publishes EO leaves `own` null
# rather than reusing the EO number, and the counts are present only for
# fields whose denominator is a real, countable set of managers.
_MEASURE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "own": {"type": ["number", "null"]},        # head-count share, percent
        "eo": {"type": ["number", "null"]},         # Σ multipliers / n, percent
        "cap": {"type": ["number", "null"]},        # captaincy share, percent
        "owned_by": {"type": ["integer", "null"]},
        "started_by": {"type": ["integer", "null"]},
        "benched_by": {"type": ["integer", "null"]},
        "captained_by": {"type": ["integer", "null"]},
    },
}

_ROW = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "team_code": {"type": ["integer", "null"]},
        "status": {"type": ["string", "null"]},
        "price": {"type": ["number", "null"]},
        "own_pct": {"type": ["number", "null"]},        # FPL marginal, percent
        "eo_pred_pct": {"type": ["number", "null"]},    # LiveFPL predicted EO, percent
        "elite_own_pct": {"type": ["number", "null"]},  # crawled cohort, percent
        "elite_eo_pct": {"type": ["number", "null"]},   # crawled cohort EO, percent
        "xpts": {"type": ["number", "null"]},           # consensus mean for xpts_gw
        "xpts_spread": {"type": ["number", "null"]},
        "n_sources": {"type": ["integer", "null"]},
        "in_squad": {"type": ["boolean", "null"]},      # null = squad unreadable
        # My own FPL multiplier as the squad read reported it: 0 bench,
        # 1 start, 2 captain, 3 triple captain. null when the squad is
        # unreadable OR readable without multipliers (a manually entered 15) --
        # the two are distinguished by `in_squad`, never guessed at here.
        "your_mult": {"type": ["integer", "null"]},
        "your_role": {"type": ["string", "null"]},      # captain|start|bench
        # key -> measurement, keys are the `fields[].key` of this same result.
        "fields": {"type": "object", "additionalProperties": _MEASURE},
    },
}

# One measurable field: who it is, what it can measure, and what its
# percentages are percentages OF.
_FIELD = {
    "type": "object",
    "additionalProperties": False,
    "required": ["key", "label", "kind", "measures", "denominator"],
    "properties": {
        "key": {"type": "string"},
        "label": {"type": "string"},
        "short": {"type": "string"},
        "kind": {"type": "string"},                 # fpl | external | cohort
        "role": {"type": "string"},                 # baseline | field
        "measures": {"type": "array", "items": {"type": "string"}},
        "denominator": {"type": "string"},
        "provider": {"type": ["string", "null"]},
        "metric": {"type": ["string", "null"]},
        "cohort": {"type": ["string", "null"]},
        "season": {"type": ["string", "null"]},
        "gw": {"type": ["integer", "null"]},
        "n": {"type": ["integer", "null"]},         # managers behind it, if countable
        "players": {"type": ["integer", "null"]},   # players it has a value for
        "as_of": {"type": ["string", "null"]},
        "live": {"type": "boolean"},
        # Measured, not assumed: the gw whose values this field's values are
        # byte-identical to (a re-stamped feed), or null.
        "same_values_as_gw": {"type": ["integer", "null"]},
        "note": {"type": ["string", "null"]},
        # Who is actually in a crawled cohort, by crawl source tag. Tags
        # overlap (one entry can be both elite_list and mini_league), so these
        # can sum above `n` -- `overlaps` says so rather than hiding it.
        "composition": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tag", "n"],
                "properties": {"tag": {"type": "string"},
                               "n": {"type": "integer"},
                               "label": {"type": ["string", "null"]}},
            },
        },
        "overlaps": {"type": ["boolean", "null"]},
    },
}

_STALE_ROW = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "eo_top10k_pct": {"type": ["number", "null"]},
        "eo_elite_pct": {"type": ["number", "null"]},
    },
}

# `required` keeps this branch disjoint from the registry's {empty, reason}
# shape: an honest empty has no `rows`, a real result always does.
RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows", "metrics_note", "cohort_note", "gws_covered"],
    "properties": {
        "season": {"type": "string"},
        "rows": {"type": "array", "items": _ROW},
        "differentials": {"type": "array", "items": _ROW},
        "last_season": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["season", "gw", "rows"],
            "properties": {
                "season": {"type": "string"},
                "gw": {"type": "integer"},
                "rows": {"type": "array", "items": _STALE_ROW},
            },
        },
        "metrics_note": {"type": "string"},
        "cohort_note": {"type": "string"},
        # Which cohort the elite_own_pct / elite_eo_pct columns describe, and
        # the denominator behind them. Null when no crawled squad was found.
        "cohort": {"type": "string"},
        "cohort_n": {"type": ["integer", "null"]},
        "cohort_gw": {"type": ["integer", "null"]},
        "gws_covered": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["metric", "season", "gw"],
                "properties": {
                    "metric": {"type": "string"},
                    "provider": {"type": ["string", "null"]},
                    "season": {"type": "string"},
                    "gw": {"type": "integer"},
                    "players": {"type": ["integer", "null"]},
                    "latest": {"type": ["string", "null"]},
                    "live": {"type": "boolean"},
                },
            },
        },
        "xpts_gw": {"type": ["integer", "null"]},
        "squad_note": {"type": ["string", "null"]},
        "as_of": {"type": ["string", "null"]},
        # The ladder of fields this warehouse can actually measure today.
        "fields": {"type": "array", "items": _FIELD},
        # What the squad read produced, so the UI can say how much of the
        # "my multiplier" side of the rank identity it is entitled to claim.
        "squad": {
            "type": "object",
            "additionalProperties": False,
            "required": ["readable"],
            "properties": {
                "readable": {"type": "boolean"},
                "source": {"type": ["string", "null"]},
                "gw": {"type": ["integer", "null"]},
                "n": {"type": ["integer", "null"]},
                "has_multipliers": {"type": "boolean"},
                "captain": {"type": ["string", "null"]},
                "note": {"type": ["string", "null"]},
            },
        },
    },
}


def _f(x, nd: int = 1) -> float | None:
    """NaN/None-safe rounded float — the JSON boundary for pandas values."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return round(v, nd)


def _i(x) -> int | None:
    if x is None or x != x:
        return None
    return int(x)


def _pct(x) -> float | None:
    """A cohort fraction as a percent, or None. 0.0 stays an honest 0.0."""
    if x is None or x != x:
        return None
    return round(float(x) * 100.0, 1)


def _tables_present(wh, names: tuple[str, ...]) -> set[str]:
    df = q(
        wh,
        "SELECT table_name FROM information_schema.tables WHERE table_name IN ("
        + ", ".join("?" for _ in names) + ")",
        names,
    )
    return set(df["table_name"]) if not df.empty else set()


def _squad_state(wh, season: str) -> tuple[dict[int, dict] | None, dict[str, Any]]:
    """The user's 15 with their FPL multipliers, or (None, why-not).

    Same read path as squad_overview (QuestionRouter._team_state): private API,
    then public picks, then the manually entered squad. Any failure — network
    down, nothing published yet — degrades to unreadable, never to a crash.

    The multiplier is the *my multiplier* term of the rank identity, so it is
    read rather than inferred, and it is reported ONLY when the read path
    actually carried one. A manually entered 15 has no armband and no bench
    order: those rows come back with ``mult: None``, which the UI renders as
    "owned, role unknown" — never as a silent 1×.
    """
    from fpl_edge.config import USER

    try:
        from fpl_edge.interfaces.qa import QuestionRouter

        router = QuestionRouter(wh, season=season, entry_id=int(USER.entry_id))
        state = router._team_state()
    except Exception as exc:  # noqa: BLE001 — a panel reports, it does not crash
        return None, {
            "readable": False, "has_multipliers": False,
            "note": f"squad unreadable ({type(exc).__name__}); coverage column blank",
        }
    if state is None or state.picks is None:
        return None, {
            "readable": False, "has_multipliers": False,
            "note": "no squad visible for your entry yet; coverage column blank",
        }

    roles: dict[int, dict] = {}
    for p in state.picks:
        raw = getattr(p, "multiplier", None)
        mult = None
        if isinstance(raw, (int, float)) and raw == raw:
            mult = int(raw)
        cap = bool(getattr(p, "is_captain", False) or False)
        starter = getattr(p, "is_starter", None)
        role = None
        if cap:
            role = "captain"
        elif isinstance(starter, bool):
            role = "start" if starter else "bench"
        elif mult is not None:
            role = "start" if mult >= 1 else "bench"
        roles[int(p.code)] = {"mult": mult, "role": role}

    source = getattr(state.provenance, "name", str(state.provenance))
    gw = getattr(state, "gw", None)
    cap_code = next((c for c, r in roles.items() if r["role"] == "captain"), None)
    meta = {
        "readable": True,
        "source": str(source),
        "gw": int(gw) if isinstance(gw, (int, float)) and gw == gw else None,
        "n": len(roles),
        "has_multipliers": any(r["mult"] is not None for r in roles.values()),
        "captain": None,          # filled in by the caller, which knows names
        "note": f"your squad read via {source}",
    }
    meta["_captain_code"] = cap_code
    return roles, meta


# --------------------------------------------------------------------------
# The field ladder: what can actually be measured, enumerated from the tables.
# --------------------------------------------------------------------------

#: Human labels for the external metric names the feed writes. LiveFPL's
#: "elite" is LIVEFPL's cohort, not this repo's crawled elite pool, and the
#: label has to say so or the two silently merge in the reader's head.
_EXTERNAL_META = {
    "eo_predicted": (
        "Whole game — predicted EO", "all FPL",
        "every FPL entry, as the provider models it for the upcoming deadline",
        None,
    ),
    "eo_top10k": (
        "LiveFPL top-10k EO", "top 10k",
        "the provider's top-10,000 sample — its definition, not a crawl of ours",
        ("Sampled and defined by the provider; this engine cannot audit its "
         "denominator."),
    ),
    "eo_elite": (
        "LiveFPL elite EO", "LiveFPL elite",
        ("the provider's own 'elite' sample — unrelated to the crawled elite "
         "cohort below"),
        ("Same word, different population: this is LiveFPL's elite, not the "
         "crawled elite pool."),
    ),
}

#: Which crawl source tag put an entry in the pool. The tags overlap, and the
#: mini-league one is a disclosure, not a footnote: those managers are the
#: owner's own opponents, so a cohort that leans on them is not an
#: independent read of the field.
_TAG_LABEL = {
    "elite_list": "curated elite list",
    "mini_league": "your own mini-league opponents",
    "winner": "past overall winners",
    "elite_named": "individually named managers",
    "expert": "public experts",
    "snowball": "found by snowballing others' leagues",
    "top1k": "sampled from the overall top-1k standings",
    "(no manager row)": "picks stored with no manager row (a crawl bug)",
}


def _external_repeats(wh, season: str) -> dict[str, int]:
    """metric -> the earlier gw whose values it is byte-identical to.

    LiveFPL re-stamps its settled top10k/elite series under the upcoming gw.
    Reporting that gw as if it were a forecast is a fabrication by labelling,
    so the duplication is measured here rather than assumed either way.
    """
    df = q(
        wh,
        """
        WITH r AS (
            SELECT metric, gw, code, value FROM (
                SELECT f.*, row_number() OVER (
                    PARTITION BY metric, gw, code ORDER BY as_of DESC) rn
                FROM fact_external_ownership f WHERE season = ?
            ) WHERE rn = 1
        ), mx AS (SELECT metric, max(gw) AS g FROM r GROUP BY 1),
        pv AS (
            SELECT r.metric, max(r.gw) AS g FROM r
            JOIN mx ON mx.metric = r.metric AND r.gw < mx.g GROUP BY 1
        )
        SELECT a.metric, pv.g AS prev_gw, count(*) AS n,
               sum(CASE WHEN a.value = b.value THEN 1 ELSE 0 END) AS same
        FROM r a
        JOIN mx ON mx.metric = a.metric AND a.gw = mx.g
        JOIN pv ON pv.metric = a.metric
        JOIN r b ON b.metric = a.metric AND b.code = a.code AND b.gw = pv.g
        GROUP BY 1, 2
        """,
        (season,),
    )
    out: dict[str, int] = {}
    if df.empty:
        return out
    for _, r in df.iterrows():
        n, same = _i(r["n"]) or 0, _i(r["same"]) or 0
        if n and n == same:
            out[str(r["metric"])] = int(r["prev_gw"])
    return out


def _cohort_composition(wh, season: str) -> dict[tuple[str, int], list[dict]]:
    """(cohort, gw) -> [{tag, n}], counted over entries with a stored squad.

    Counted with ``count(DISTINCT entry_id)`` per tag, so an entry listed
    under two winner years is one manager, not two — but an entry carrying two
    *different* tags appears under both, which is exactly the overlap the
    caller flags rather than hides.
    """
    df = q(
        wh,
        """
        WITH held AS (
            SELECT DISTINCT entry_id, gw FROM fact_manager_pick WHERE season = ?
        ), coh AS (
            SELECT entry_id, cohort, sources FROM sem_manager_cohort(now())
        ), j AS (
            SELECT h.entry_id, h.gw,
                   coalesce(c.cohort, 'unclassified') AS cohort,
                   coalesce(c.sources, '(no manager row)') AS sources
            FROM held h LEFT JOIN coh c ON c.entry_id = h.entry_id
        )
        SELECT cohort, gw,
               CASE WHEN src LIKE 'top1k%'       THEN 'top1k'
                    WHEN src LIKE 'winner%'      THEN 'winner'
                    WHEN src LIKE 'mini_league%' THEN 'mini_league'
                    WHEN src LIKE 'snowball%'    THEN 'snowball'
                    ELSE src END AS tag,
               count(DISTINCT entry_id) AS n
        FROM (SELECT entry_id, gw, cohort,
                     unnest(string_split(sources, '|')) AS src FROM j)
        GROUP BY 1, 2, 3 ORDER BY 4 DESC
        """,
        (season,),
    )
    out: dict[tuple[str, int], list[dict]] = {}
    if df.empty:
        return out
    for _, r in df.iterrows():
        tag = str(r["tag"])
        out.setdefault((str(r["cohort"]), int(r["gw"])), []).append(
            {"tag": tag, "n": int(r["n"]), "label": _TAG_LABEL.get(tag)})
    return out


def ownership_eo(
    wh,
    *,
    season: str,
    limit: int = 50,
    diff_max_own: float = 15.0,
    cohort: str = "elite",
    coverage: bool = True,
) -> dict[str, Any]:
    """Template and differentials from effective ownership plus consensus xPts.

    ``rows`` is the current template: ranked by the live EO metric for the
    requested season (falling back to FPL marginal ownership when no external
    feed covers it). ``differentials`` are low-ownership players with the best
    consensus xPts. ``last_season`` quarantines EO metrics recorded under a
    different season — never merged into the current table.
    """
    players = q(
        wh,
        """
        SELECT p.code, p.web_name, p.position, p.team, p.team_code, p.status,
               p.price, p.selected_by_pct
        FROM sem_players(now()) p WHERE p.season = ?
        """,
        (season,),
    )
    if players.empty:
        return empty(
            f"No {season} players in the warehouse, so neither a template nor "
            f"a differential can be named. Run `make ingest` first."
        )

    # -- what EO data exists, enumerated rather than assumed (trap 1 and 2) --
    cov = q(
        wh,
        """
        SELECT provider, metric, season, gw, count(DISTINCT code) AS players,
               max(as_of) AS latest
        FROM fact_external_ownership
        GROUP BY 1, 2, 3, 4 ORDER BY metric, season, gw
        """,
    )
    gws_covered: list[dict[str, Any]] = []
    if not cov.empty:
        for _, r in cov.iterrows():
            gws_covered.append({
                "metric": str(r["metric"]),
                "provider": str(r["provider"]),
                "season": str(r["season"]),
                "gw": int(r["gw"]),
                "players": _i(r["players"]),
                "latest": str(r["latest"]),
                "live": str(r["season"]) == season,
            })

    # -- live EO pivot: requested season only, each metric at its latest gw --
    # Kept long rather than pivoted to three named columns: the metric list is
    # whatever the feed happens to write, and a hard-coded pivot is exactly how
    # eo_top10k/eo_elite went live for this season without the panel noticing.
    # Each metric is pinned to ITS OWN latest gw and nothing older is carried
    # forward: a field is "this metric at gw G", so a code the feed dropped
    # goes null rather than silently keeping a value from a different week.
    eo = q(
        wh,
        """
        WITH r AS (
            SELECT metric, gw, code, value FROM (
                SELECT f.*, row_number() OVER (
                    PARTITION BY metric, gw, code ORDER BY as_of DESC) rn
                FROM fact_external_ownership f WHERE season = ?
            ) WHERE rn = 1
        ), mx AS (SELECT metric, max(gw) AS g FROM r GROUP BY 1)
        SELECT r.metric, r.gw, r.code, r.value
        FROM r JOIN mx ON mx.metric = r.metric AND r.gw = mx.g
        """,
        (season,),
    )
    external: dict[str, dict[int, float]] = {}
    external_gw: dict[str, int] = {}
    if not eo.empty:
        for _, r in eo.iterrows():
            v = r["value"]
            if v is None or v != v:
                continue
            metric = str(r["metric"])
            external.setdefault(metric, {})[int(r["code"])] = float(v)
            external_gw[metric] = int(r["gw"])
    eo_pred = external.get("eo_predicted", {})

    # -- consensus xPts for the next deadline (or the earliest gw on file) --
    gw = next_gw(wh, season)
    if gw is None:
        g = q(wh, "SELECT min(gw) AS g FROM sem_projection_consensus(now()) WHERE season = ?",
              (season,))
        gw = _i(g.iloc[0]["g"]) if not g.empty else None
    xp: dict[int, tuple] = {}
    if gw is not None:
        cons = q(
            wh,
            """
            SELECT code, xpts_mean, xpts_spread, n_sources
            FROM sem_projection_consensus(now()) WHERE season = ? AND gw = ?
            """,
            (season, gw),
        )
        if not cons.empty:
            xp = {int(r["code"]): (r["xpts_mean"], r["xpts_spread"], r["n_sources"])
                  for _, r in cons.iterrows()}

    # -- the crawled cohort: own%/EO% from the ONE definition ----------------
    # sem_elite_ownership is the canonical effective ownership (mean FPL
    # multiplier over the cohort's managers with a stored squad). This script
    # does not compute its own: the previous local query had no cohort filter
    # at all and reported a blended top1k+elite denominator as "elite".
    # EVERY cohort is read, not just the requested one: the UI compares fields
    # against each other, and a refetch per cohort would let the two halves of
    # a comparison drift to different as_of instants.
    elite: dict[int, tuple] = {}
    elite_cohort = 0
    elite_gw = None
    rival = _tables_present(wh, ("fact_manager_pick", "dim_manager", "fact_manager_season"))
    cohorts_present: list[dict[str, Any]] = []
    cohort_rows: dict[str, dict[int, dict]] = {}
    cohort_meta: dict[str, dict[str, Any]] = {}
    composition: dict[tuple[str, int], list[dict]] = {}
    if "fact_manager_pick" in rival:
        sizes = q(
            wh,
            """
            SELECT cohort, gw, any_value(n_managers) AS n_managers
            FROM sem_elite_ownership(now()) WHERE season = ?
            GROUP BY cohort, gw ORDER BY gw DESC, cohort
            """,
            (season,),
        )
        if not sizes.empty:
            cohorts_present = [
                {"cohort": str(r["cohort"]), "gw": int(r["gw"]),
                 "n": _i(r["n_managers"])}
                for _, r in sizes.iterrows()
            ]
        picks = q(
            wh,
            """
            WITH c AS (
                SELECT * FROM sem_elite_ownership(now()) WHERE season = ?
            ), mx AS (SELECT cohort, max(gw) AS g FROM c GROUP BY 1)
            SELECT c.cohort, c.code, c.gw, c.n_managers, c.own_pct, c.eo_pct,
                   c.captain_pct, c.owned_by, c.started_by, c.benched_by,
                   c.captained_by
            FROM c JOIN mx ON mx.cohort = c.cohort AND c.gw = mx.g
            WHERE c.code IS NOT NULL
            """,
            (season,),
        )
        if not picks.empty:
            for _, r in picks.iterrows():
                co = str(r["cohort"])
                cohort_rows.setdefault(co, {})[int(r["code"])] = {
                    "own": _f(r["own_pct"], 1),
                    "eo": _f(r["eo_pct"], 1),
                    "cap": _f(r["captain_pct"], 1),
                    "owned_by": _i(r["owned_by"]),
                    "started_by": _i(r["started_by"]),
                    "benched_by": _i(r["benched_by"]),
                    "captained_by": _i(r["captained_by"]),
                }
                cohort_meta[co] = {"n": _i(r["n_managers"]), "gw": int(r["gw"])}
                if co == cohort:
                    # The legacy elite_* keys keep their original 6dp-then-1dp
                    # rounding path, from this same read — one query, not two.
                    elite[int(r["code"])] = (_f(r["own_pct"], 6),
                                             _f(r["eo_pct"], 6))
            composition = _cohort_composition(wh, season)
        if cohort in cohort_meta:
            elite_cohort = cohort_meta[cohort]["n"] or 0
            elite_gw = cohort_meta[cohort]["gw"]

    # -- squad coverage: codes AND the multiplier behind each one --
    roles: dict[int, dict] | None = None
    if coverage:
        roles, squad_meta = _squad_state(wh, season)
    else:
        squad_meta = {"readable": False, "has_multipliers": False,
                      "note": "coverage disabled by caller"}
    squad_note = squad_meta.get("note")
    squad = set(roles) if roles is not None else None

    def row(r) -> dict[str, Any]:
        code = int(r["code"])
        pred = eo_pred.get(code)
        e = elite.get(code)
        x = xp.get(code)
        mine = roles.get(code) if roles is not None else None
        f: dict[str, dict] = {}
        own = _f(r["selected_by_pct"], 1)
        if own is not None:
            f["global"] = {"own": own}
        for metric, values in external.items():
            v = values.get(code)
            if v is not None:
                f[metric] = {"eo": _f(v * 100.0, 1)}
        for co, by_code in cohort_rows.items():
            m = by_code.get(code)
            if m is not None:
                f[f"cohort:{co}"] = m
        return {
            "code": code,
            "name": str(r["web_name"]) if r["web_name"] == r["web_name"] else str(code),
            "pos": POSITION_NAME.get(_i(r["position"]) or 0),
            "team": str(r["team"]) if r["team"] == r["team"] and r["team"] is not None else None,
            "team_code": _i(r["team_code"]),
            "status": str(r["status"]) if r["status"] == r["status"] and r["status"] is not None else None,
            "price": _f(r["price"], 1),
            "own_pct": own,
            "eo_pred_pct": _f(pred * 100.0, 1) if pred is not None else None,
            "elite_own_pct": _f(e[0], 1) if e else None,
            "elite_eo_pct": _f(e[1], 1) if e else None,
            "xpts": _f(x[0], 2) if x else None,
            "xpts_spread": _f(x[1], 2) if x else None,
            "n_sources": _i(x[2]) if x else None,
            "in_squad": (code in squad) if squad is not None else None,
            "your_mult": mine["mult"] if mine else None,
            "your_role": mine["role"] if mine else None,
            "fields": f,
        }

    all_rows = [row(r) for _, r in players.iterrows()]

    if squad_meta.get("readable"):
        cap_code = squad_meta.pop("_captain_code", None)
        squad_meta["captain"] = next(
            (c["name"] for c in all_rows if c["code"] == cap_code), None)
    squad_meta.pop("_captain_code", None)

    have_live_eo = bool(eo_pred)
    key_live = (lambda c: (c["eo_pred_pct"] is None, -(c["eo_pred_pct"] or 0),
                           -(c["own_pct"] or 0)))
    key_marginal = (lambda c: -(c["own_pct"] or 0))
    template = sorted(all_rows, key=key_live if have_live_eo else key_marginal)[:limit]

    diffs = sorted(
        (c for c in all_rows
         if c["xpts"] is not None and (c["own_pct"] or 0) <= diff_max_own),
        key=lambda c: (-(c["xpts"] or 0), c["own_pct"] or 0),
    )[:limit]

    # -- other-season EO: quarantined, stamped with its real season and gw --
    last_season = None
    stale = q(
        wh,
        """
        WITH latest AS (
            SELECT season, gw, metric, code, value FROM (
                SELECT f.*, row_number() OVER (
                    PARTITION BY season, gw, metric, code ORDER BY as_of DESC) rn
                FROM fact_external_ownership f
                WHERE season <> ?
                  AND season = (SELECT max(season) FROM fact_external_ownership
                                WHERE season <> ?)
            ) WHERE rn = 1
        )
        SELECT l.season, l.gw, l.code,
               any_value(p.web_name) AS web_name, any_value(p.position) AS position,
               any_value(p.team) AS team,
               max(CASE WHEN l.metric = 'eo_top10k' THEN l.value END) AS eo_top10k,
               max(CASE WHEN l.metric = 'eo_elite'  THEN l.value END) AS eo_elite
        FROM latest l
        LEFT JOIN sem_players(now()) p ON p.season = l.season AND p.code = l.code
        GROUP BY l.season, l.gw, l.code
        ORDER BY coalesce(max(CASE WHEN l.metric = 'eo_top10k' THEN l.value END),
                          max(CASE WHEN l.metric = 'eo_elite'  THEN l.value END),
                          0) DESC
        LIMIT ?
        """,
        (season, season, limit),
    )
    if not stale.empty:
        s_season = str(stale.iloc[0]["season"])
        s_gw = int(stale.iloc[0]["gw"])
        last_season = {
            "season": s_season,
            "gw": s_gw,
            "rows": [{
                "code": int(r["code"]),
                "name": str(r["web_name"]) if r["web_name"] == r["web_name"]
                        and r["web_name"] is not None else str(int(r["code"])),
                "pos": POSITION_NAME.get(_i(r["position"]) or 0),
                "team": str(r["team"]) if r["team"] == r["team"]
                        and r["team"] is not None else None,
                "eo_top10k_pct": _pct(r["eo_top10k"]),
                "eo_elite_pct": _pct(r["eo_elite"]),
            } for _, r in stale.iterrows()],
        }

    # -- honest notes, built from what was actually found --
    live_bits, stale_bits = [], []
    for c in gws_covered:
        label = f"{c['metric']} ({c['provider']}, {c['season']} GW{c['gw']}, {c['players']} players)"
        (live_bits if c["live"] else stale_bits).append(label)
    parts = []
    if live_bits:
        parts.append("Live EO: " + "; ".join(live_bits)
                     + ". Values are cohort fractions × captaincy — over 100% is normal.")
    else:
        parts.append(f"No external EO feed covers {season}; the template is "
                     f"ranked by FPL marginal ownership instead.")
    if stale_bits:
        parts.append("Stale (other season, shown only as \"last season's final "
                     "template\", never merged into current): " + "; ".join(stale_bits) + ".")
    parts.append("own_pct is FPL's marginal selected-by % — no captaincy weighting.")
    metrics_note = " ".join(parts)

    others = "; ".join(
        f"{c['cohort']} n={c['n']} (GW{c['gw']})"
        for c in cohorts_present if c["cohort"] != cohort
    )
    if elite_cohort:
        cohort_note = (
            f"{cohort} own%/EO% observed from {elite_cohort} crawled managers' "
            f"locked GW{elite_gw} squads (fact_manager_pick, via "
            f"sem_elite_ownership). EO is the mean FPL multiplier those "
            f"{elite_cohort} managers applied — 0 benched, 1 started, 2 "
            f"captain, 3 triple captain — so it is not ownership and can "
            f"exceed 100%. Cohorts are mutually exclusive: an entry sampled by "
            f"both crawls counts as top1k only, never in both denominators."
            + (f" Also on file: {others}." if others else "")
        )
    else:
        n_mgr = n_seasons = 0
        if "dim_manager" in rival:
            d = q(wh, "SELECT count(DISTINCT entry_id) AS n FROM dim_manager")
            n_mgr = int(d.iloc[0]["n"]) if not d.empty else 0
        if "fact_manager_season" in rival:
            d = q(wh, "SELECT count(*) AS n FROM fact_manager_season")
            n_seasons = int(d.iloc[0]["n"]) if not d.empty else 0
        cohort_note = (
            f"No {cohort} picks stored for {season} yet"
            + (f" ({n_mgr} crawled managers with {n_seasons} past-season records "
               f"on file, but fact_manager_pick is empty" if n_mgr else "")
            + ("); " if n_mgr else "; ")
            + f"{cohort} own%/EO% columns stay blank until the picks crawl runs."
            + (f" Other cohorts on file: {others}." if others else "")
        )

    # -- the field ladder: one descriptor per thing that can actually be
    #    measured, so the UI never has to hard-code a field or a denominator --
    as_of = latest_as_of(wh, "fact_player_state", season)
    repeats = _external_repeats(wh, season) if gws_covered else {}
    cov_at_gw = {
        (c["metric"], c["gw"]): c for c in gws_covered if c["live"]
    }

    def n_with(key: str, measure: str) -> int:
        return sum(1 for c in all_rows
                   if (c["fields"].get(key) or {}).get(measure) is not None)

    fields: list[dict[str, Any]] = [{
        "key": "global",
        "label": "Whole game — FPL ownership",
        "short": "all FPL",
        "kind": "fpl",
        "role": "baseline",
        "measures": ["own"],
        "denominator": "every FPL entry — FPL publishes the share, not the "
                       "entry count, so no manager count is claimed here",
        "provider": "fpl",
        "metric": "selected_by_pct",
        "cohort": None,
        "season": season,
        "gw": None,
        "n": None,
        "players": n_with("global", "own"),
        "as_of": as_of,
        "live": True,
        "same_values_as_gw": None,
        "note": "Marginal ownership — no captaincy weighting. It is not an EO, "
                "so it never shares an axis with one.",
        "composition": None,
        "overlaps": None,
    }]

    for metric in ("eo_predicted", "eo_top10k", "eo_elite",
                   *sorted(k for k in external if k not in _EXTERNAL_META)):
        if metric not in external:
            continue
        g = external_gw.get(metric)
        c = cov_at_gw.get((metric, g), {})
        label, short, denom, note = _EXTERNAL_META.get(
            metric, (metric, metric, f"the provider's {metric} population", None))
        fields.append({
            "key": metric,
            "label": label,
            "short": short,
            "kind": "external",
            "role": "baseline" if metric == "eo_predicted" else "field",
            "measures": ["eo"],
            "denominator": denom,
            "provider": c.get("provider"),
            "metric": metric,
            "cohort": None,
            "season": season,
            "gw": g,
            "n": None,
            "players": n_with(metric, "eo"),
            "as_of": c.get("latest"),
            "live": True,
            "same_values_as_gw": repeats.get(metric),
            "note": note,
            "composition": None,
            "overlaps": None,
        })

    _COHORT_LABEL = {
        "elite": ("Crawled elite pool", "elite"),
        "top1k": ("Crawled top-1k sample", "top 1k"),
        "unclassified": ("Unclassified crawled squads", "unclassified"),
    }
    for co in ("elite", "top1k", "unclassified",
               *sorted(k for k in cohort_meta if k not in _COHORT_LABEL)):
        meta = cohort_meta.get(co)
        if meta is None:
            continue
        n, g = meta["n"], meta["gw"]
        label, short = _COHORT_LABEL.get(co, (f"Crawled {co}", co))
        comp = composition.get((co, g))
        denom = (
            f"the {n} managers in the {co} crawl pool with a stored GW{g} "
            f"squad" if co != "unclassified" else
            f"the {n} crawled entries with a stored GW{g} squad and no "
            f"dim_manager row to classify them"
        )
        fields.append({
            "key": f"cohort:{co}",
            "label": f"{label} ({n})",
            "short": short,
            "kind": "cohort",
            "role": "field",
            "measures": ["own", "eo"],
            "denominator": denom,
            "provider": "fact_manager_pick crawl",
            "metric": None,
            "cohort": co,
            "season": season,
            "gw": g,
            "n": n,
            "players": n_with(f"cohort:{co}", "own"),
            "as_of": as_of,
            "live": True,
            "same_values_as_gw": None,
            "note": "Observed squads, not a model: EO is the mean FPL "
                    "multiplier these managers actually applied. A small "
                    "denominator makes every share coarse — one manager is "
                    + (f"{100.0 / n:.2f} percentage points." if n else "one row."),
            "composition": comp,
            "overlaps": bool(comp) and sum(x["n"] for x in comp) > (n or 0),
        })

    return {
        "season": season,
        "rows": template,
        "differentials": diffs,
        "last_season": last_season,
        "metrics_note": metrics_note,
        "cohort_note": cohort_note,
        "cohort": cohort,
        "cohort_n": elite_cohort or None,
        "cohort_gw": elite_gw,
        "gws_covered": gws_covered,
        "fields": fields,
        "squad": squad_meta,
        "xpts_gw": gw,
        "squad_note": squad_note,
        "as_of": as_of,
    }


register_script(
    name="ownership_eo",
    fn=ownership_eo,
    params_schema=PARAMS_SCHEMA,
    result_schema=RESULT_SCHEMA,
    title="Template & effective ownership",
    description="What the field owns: marginal ownership beside every "
                "external effective-ownership metric, template and "
                "differential views.",
)
