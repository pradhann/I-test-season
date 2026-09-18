"""The fields on offer, their denominators, and the notes that qualify them.

A field is one set of managers with one measure. This module builds the
ladder, resolves the selectable segments against what was actually crawled,
and tells the two sets the word "field" names apart."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fpl_edge.platform.scripts.common import latest_as_of, q
from fpl_edge.platform.scripts.ownership.schema import (
    _SEGMENT_CASE,
    _SEGMENT_META,
    DEFAULT_SEGMENTS,
    _f,
    _i,
)

# --------------------------------------------------------------------------
# The field ladder: what can actually be measured, enumerated from the tables.
# --------------------------------------------------------------------------

#: Human labels for the external metric names the feed writes. LiveFPL's
#: "elite" is LIVEFPL's cohort, not this repo's crawled elite pool, and the
#: label has to say so or the two silently merge in the reader's head.
_EXTERNAL_META = {
    "eo_predicted": (
        "Whole game, predicted EO", "all FPL",
        "every FPL entry, as the provider models it for the upcoming deadline",
        None,
    ),
    "eo_top10k": (
        "LiveFPL top-10k EO", "top 10k",
        "the provider's top-10,000 sample, on its definition, not a crawl "
        "of ours",
        ("Sampled and defined by the provider; this engine cannot audit its "
         "denominator."),
    ),
    "eo_elite": (
        "LiveFPL elite EO", "LiveFPL elite",
        ("the provider's own 'elite' sample, unrelated to the crawled elite "
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
        f"""
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
        SELECT cohort, gw, {_SEGMENT_CASE} AS tag,
               count(DISTINCT entry_id) AS n
        FROM (SELECT entry_id, gw, cohort,
                     unnest(string_split(sources, '|')) AS source FROM j)
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


def _segment_inventory(wh, season: str, gw: int | None) -> list[dict[str, Any]]:
    """Every selectable set: its pool size, its measurable size, its cohorts.

    ``n_squad`` is the only count that may ever become a denominator — a
    manager the crawl has no squad for cannot be part of a share of squads.
    ``n_pool`` is carried beside it so the gap is visible rather than implied:
    the expert pool is 20 managers and 0 of them have a stored GW1 squad, and a
    UI that only saw "expert (20)" would offer a checkbox that measures nobody.
    """
    if gw is None:
        return []
    df = q(
        wh,
        f"""
        WITH seg AS (
            SELECT DISTINCT entry_id, {_SEGMENT_CASE} AS segment
            FROM dim_manager WHERE as_of <= now()
        ), held AS (
            SELECT DISTINCT entry_id FROM fact_manager_pick
            WHERE season = ? AND gw = ?
        ), coh AS (
            SELECT entry_id, cohort FROM sem_manager_cohort(now())
        )
        SELECT s.segment,
               count(DISTINCT s.entry_id) AS n_pool,
               count(DISTINCT h.entry_id) AS n_squad,
               string_agg(DISTINCT c.cohort, ',' ORDER BY c.cohort) AS cohorts
        FROM seg s
        LEFT JOIN held h ON h.entry_id = s.entry_id
        LEFT JOIN coh  c ON c.entry_id = s.entry_id
        GROUP BY 1 ORDER BY 3 DESC, 2 DESC, 1
        """,
        (season, gw),
    )
    out: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        raw = r["cohorts"]
        out.append({
            "segment": str(r["segment"]),
            "n_pool": _i(r["n_pool"]),
            "n_squad": _i(r["n_squad"]),
            "cohorts": sorted(str(raw).split(",")) if raw == raw and raw else None,
        })
    return out


def _segment_ownership(wh, season: str, segments: list[str]) -> dict[int, dict]:
    """gw -> {n_managers, unresolved_entries, by_code}, over the segment UNION.

    The same three formulas as ``sem_elite_ownership`` / ``sem_segment_
    ownership`` (store/views.sql) over a caller-chosen population. The panel
    carries this inline rather than calling the macro for one reason, recorded
    on ``_SEGMENT_CASE``: read copies are opened read-only, so views.sql is not
    reapplied and a warehouse file older than the macro would not have it. The
    macro remains the canonical definition and
    ``test_the_panels_segment_union_equals_the_semantic_layer_macro`` pins this
    query's numbers to it column by column.

    THE DENOMINATOR IS ``count(DISTINCT entry_id)``. The segments overlap — on
    the live warehouse elite_list(250) + winner(12) + elite_named(8) is 270 set
    memberships over 262 distinct managers — so a sum of set sizes would be a
    denominator that no one is in.

    THE TRIPLE-CAPTAIN TERM IS SPLIT OUT. ``eo`` keeps every multiplier the
    managers actually applied, chips included, because that is what the
    identity subtracts. But a multiplier of 3 is a chip that cannot repeat, so
    the part of ``eo`` it contributes is measured separately as ``eo_tc_pp``
    (= 100 × the count of 3x picks / n) and ``eo_ex_tc`` is what is left. On
    GW3 2026-27 Haaland is 238.2 EO, of which 42.0 is 110 triple captains;
    ``eo_ex_tc`` is 196.2, which is own 98.1 + captain 98.1 exactly. Only the
    triple captain is separable here: a bench boost gives a benched player a
    multiplier of 1 and there is no chip column to tell that from a start.
    """
    if not segments:
        return {}
    holes = ", ".join("?" for _ in segments)
    df = q(
        wh,
        f"""
        WITH sel AS (
            SELECT DISTINCT entry_id FROM (
                SELECT entry_id, {_SEGMENT_CASE} AS segment
                FROM dim_manager WHERE as_of <= now()
            ) WHERE segment IN ({holes})
        ), mp AS (
            SELECT * FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY entry_id, season, gw, element_id
                    ORDER BY as_of DESC) rn
                FROM fact_manager_pick WHERE as_of <= now() AND season = ?
            ) WHERE rn = 1
        ), base AS (
            SELECT mp.* FROM mp JOIN sel ON sel.entry_id = mp.entry_id
        ), n AS (
            SELECT gw, count(DISTINCT entry_id) AS n_managers
            FROM base GROUP BY gw
        ), dp AS (
            SELECT * FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY season, element_id ORDER BY as_of DESC) rn
                FROM dim_player WHERE as_of <= now()
            ) WHERE rn = 1
        )
        SELECT b.gw, dp.code, n.n_managers,
               count(DISTINCT b.entry_id) AS owned_by,
               count(DISTINCT CASE WHEN coalesce(b.multiplier, 0) >= 1
                                   THEN b.entry_id END) AS started_by,
               count(DISTINCT CASE WHEN coalesce(b.multiplier, 0) = 0
                                   THEN b.entry_id END) AS benched_by,
               count(DISTINCT CASE WHEN b.is_captain THEN b.entry_id END)
                   AS captained_by,
               count(DISTINCT CASE WHEN coalesce(b.multiplier, 0) >= 3
                                   THEN b.entry_id END) AS tripled_by,
               100.0 * count(DISTINCT b.entry_id) / n.n_managers AS own_pct,
               100.0 * count(DISTINCT CASE WHEN b.is_captain THEN b.entry_id END)
                     / n.n_managers AS captain_pct,
               100.0 * sum(coalesce(b.multiplier, 0)) / n.n_managers AS eo_pct,
               100.0 * sum(greatest(coalesce(b.multiplier, 0) - 2, 0))
                     / n.n_managers AS eo_tc_pp
        FROM base b
        JOIN n ON n.gw = b.gw
        LEFT JOIN dp ON dp.season = b.season AND dp.element_id = b.element_id
        GROUP BY b.gw, dp.code, n.n_managers
        """,
        (*segments, season),
    )
    out: dict[int, dict] = {}
    if df.empty:
        return out
    for _, r in df.iterrows():
        g = int(r["gw"])
        slot = out.setdefault(
            g, {"n_managers": _i(r["n_managers"]), "unresolved_entries": 0,
                "by_code": {}})
        code = _i(r["code"])
        if code is None:
            # A pick whose element resolved to no player code: a hole in the
            # crawl, counted and reported rather than dropped at the join.
            slot["unresolved_entries"] = _i(r["owned_by"]) or 0
            continue
        eo = _f(r["eo_pct"], 1)
        tc_pp = _f(r["eo_tc_pp"], 1)
        slot["by_code"][code] = {
            "own": _f(r["own_pct"], 1),
            "eo": eo,
            "cap": _f(r["captain_pct"], 1),
            # The chip term, named rather than buried inside eo.
            "eo_tc_pp": tc_pp,
            "eo_ex_tc": (round(eo - tc_pp, 1)
                         if eo is not None and tc_pp is not None else None),
            "owned_by": _i(r["owned_by"]),
            "started_by": _i(r["started_by"]),
            "benched_by": _i(r["benched_by"]),
            "captained_by": _i(r["captained_by"]),
            "tripled_by": _i(r["tripled_by"]),
        }
    return out


def _selection_includes(wh, segments: list[str], entry_id: int | None) -> bool | None:
    """Is the owner's own entry inside the field he is being measured against?

    Not a footnote. If he is in it, his own transfer moves the denominator by
    1/n, so the what-if simulator's "the field does not move when I move"
    assumption is false and the panel has to say so.
    """
    if not segments or entry_id is None:
        return None
    holes = ", ".join("?" for _ in segments)
    df = q(
        wh,
        f"""
        SELECT count(*) AS n FROM (
            SELECT entry_id, {_SEGMENT_CASE} AS segment
            FROM dim_manager WHERE as_of <= now() AND entry_id = ?
        ) WHERE segment IN ({holes})
        """,
        (entry_id, *segments),
    )
    if df.empty:
        return False
    return (_i(df.iloc[0]["n"]) or 0) > 0


def _notes(
    wh,
    *,
    season: str,
    cohort: str,
    cohorts_present: list[dict[str, Any]],
    elite_cohort: int,
    elite_gw: int | None,
    gws_covered: list[dict[str, Any]],
    rival: bool,
) -> tuple[str, str]:
    """The two notes, built from what was actually found rather than asserted."""
    # -- honest notes, built from what was actually found --
    live_bits, stale_bits = [], []
    for c in gws_covered:
        label = f"{c['metric']} ({c['provider']}, {c['season']} GW{c['gw']}, {c['players']} players)"
        (live_bits if c["live"] else stale_bits).append(label)
    parts = []
    if live_bits:
        parts.append("Live EO: " + "; ".join(live_bits)
                     + ". Values are cohort fractions × captaincy, so over "
                       "100% is normal.")
    else:
        parts.append(f"No external EO feed covers {season}; the template is "
                     f"ranked by FPL marginal ownership instead.")
    if stale_bits:
        parts.append("Stale (other season, shown only as \"last season's final "
                     "template\", never merged into current): " + "; ".join(stale_bits) + ".")
    parts.append("own_pct is FPL's marginal selected-by %, with no "
                 "captaincy weighting.")
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
            f"{elite_cohort} managers applied: 0 benched, 1 started, 2 "
            f"captain, 3 triple captain. It is not ownership and can exceed "
            f"100%. A triple-captain week lifts it by a whole unit per "
            f"manager who played the chip, and that unit cannot repeat. "
            f"Cohorts are mutually exclusive: an entry sampled by "
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

    return metrics_note, cohort_note


def _field_ladder(
    wh,
    *,
    season: str,
    all_rows: list[dict[str, Any]],
    cohort_meta: dict[str, dict[str, Any]],
    composition: dict[tuple[str, int], list[dict]],
    external: dict[str, dict[int, float]],
    external_gw: dict[str, int],
    gws_covered: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]], str | None, dict[tuple[str, int], dict],
    Callable[[str, str], int]]:
    """Every field on offer, in ladder order, each with its own denominator.

    ``n_with`` counts the players carrying a measure for one field. It closes
    over ``all_rows`` and is the only value crossing to ``_selectable_sets``
    that is a function rather than data; passing it keeps that builder from
    needing ``all_rows`` at all."""
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
        "label": "Whole game, FPL ownership",
        "short": "all FPL",
        "kind": "fpl",
        "role": "baseline",
        "measures": ["own"],
        "denominator": "every FPL entry. FPL publishes the share, not the "
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
        "note": "Marginal ownership, with no captaincy weighting. It is not "
                "an EO, so it never shares an axis with one.",
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
        # The conflict of interest, labelled ON the field: the crawled pool
        # can contain the owner's own mini-league opponents — a set the
        # default SELECTION deliberately excludes.
        ml_n = next((int(x["n"]) for x in comp or []
                     if x.get("tag") == "mini_league"), None)
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
                    "multiplier these managers actually applied, chips "
                    "included, so a triple-captain week reads high and cannot "
                    "repeat. A small denominator makes every share coarse: "
                    "one manager is "
                    + (f"{100.0 / n:.2f} percentage points." if n else "one row.")
                    + (f" Includes {ml_n} of the owner's own mini-league "
                       f"opponents, a set the default selection excludes and "
                       f"this measured cohort does not." if ml_n else ""),
            "composition": comp,
            "overlaps": bool(comp) and sum(x["n"] for x in comp) > (n or 0),
            "mini_league_n": ml_n,
        })

    return fields, as_of, cov_at_gw, n_with


def _selectable_sets(
    wh,
    *,
    season: str,
    as_of: str | None,
    fields: list[dict[str, Any]],
    known: set[str],
    n_with: Callable[[str, str], int],
    pick_gw: int | None,
    requested: list[str],
    resolved: list[str],
    seg_inventory: list[dict[str, Any]],
    sel_gw: int | None,
    sel_n: int | None,
    sel_slot: dict[str, Any] | None,
    squad_meta: dict[str, Any],
    user=None,
    unknown: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], bool | None, str]:
    """The segments on offer as first-class descriptors, and what the choice resolved to."""
    # -- the selectable sets, as first-class descriptors ---------------------
    seg_selected = set(resolved)
    # Every set the UI could offer: what the crawl actually holds, plus any
    # set that is selected or in the default but currently has no members. A
    # selected set with no checkbox to unselect it would be a dead end.
    offerable = list(seg_inventory) + [
        {"segment": k, "n_pool": 0, "n_squad": 0, "cohorts": None}
        for k in list(DEFAULT_SEGMENTS) + resolved
        if k not in known and k not in {x["segment"] for x in seg_inventory}
    ]
    seen_seg: set[str] = set()
    segment_rows: list[dict[str, Any]] = []
    for s in offerable:
        key = s["segment"]
        if key in seen_seg:
            continue
        seen_seg.add(key)
        label, trusted, untrusted, caveat = _SEGMENT_META.get(
            key, (key, True, None,
                  "An unrecognised crawl source. It is offered because it "
                  "exists, but nothing is known here about how it was built."))
        segment_rows.append({
            "key": key,
            "label": label,
            "n": s["n_squad"],
            "n_pool": s["n_pool"],
            "gw": pick_gw,
            "trusted": trusted,
            "untrusted_reason": untrusted,
            "caveat": caveat,
            "in_default": key in DEFAULT_SEGMENTS,
            "selected": key in seg_selected,
            "cohorts": s["cohorts"],
        })

    sum_of_sets = sum((s["n"] or 0) for s in segment_rows if s["selected"]) or None
    overlap = (sum_of_sets - sel_n) if (sum_of_sets and sel_n is not None) else None
    try:
        my_entry = int(user.entry_id) if user is not None else None
    except Exception:  # noqa: BLE001 — a panel reports, it does not crash
        my_entry = None
    includes_you = _selection_includes(wh, resolved, my_entry)
    untrusted_selected = [s["key"] for s in segment_rows
                          if s["selected"] and not s["trusted"]]

    if sel_n:
        sel_denominator = (
            f"the {sel_n} DISTINCT managers in the union of "
            f"{', '.join(resolved)} with a stored GW{sel_gw} squad"
            + (f", not {sum_of_sets}: the sets overlap and {overlap} manager"
               f"{'s' if overlap != 1 else ''} carry more than one tag"
               if overlap else "")
        )
    else:
        sel_denominator = (
            "no denominator: the selected sets contain no manager with a "
            "stored squad for this season, so nothing is a share of anything"
        )

    sel_note_bits: list[str] = []
    if unknown:
        sel_note_bits.append(
            f"Requested but unknown to the crawl, so not in the field: "
            f"{', '.join(unknown)}.")
    if untrusted_selected:
        sel_note_bits.append(
            f"An UNTRUSTWORTHY set is selected ({', '.join(untrusted_selected)}); "
            f"see segments[].untrusted_reason before reading any number here.")
    if includes_you:
        sel_note_bits.append(
            "Your own entry is inside this field, so you are part of the "
            "denominator you are measuring yourself against and a transfer of "
            "yours moves the field by 1/n.")
    squad_gw = squad_meta.get("gw")
    if sel_gw is not None and isinstance(squad_gw, int) and squad_gw != sel_gw:
        sel_note_bits.append(
            f"Your squad was read for GW{squad_gw} but the field's squads are "
            f"stored for GW{sel_gw}: the diff compares two different "
            f"gameweeks and the gap is not purely a difference of opinion.")
    if not resolved:
        sel_note_bits.append(
            "No set is selected, so there is no field: the diff and the "
            "what-if simulator have nothing to compare against.")

    selection = {
        "segments": resolved,
        "requested": requested,
        "unknown": unknown,
        "default": list(DEFAULT_SEGMENTS),
        # Order-insensitive: the same three sets in another order is still
        # the default field, and a UI should not have to preserve order to
        # be told so.
        "is_default": sorted(requested) == sorted(DEFAULT_SEGMENTS),
        "n": sel_n,
        "n_sum_of_sets": sum_of_sets,
        "overlap": overlap,
        "overlaps": bool(overlap) if overlap is not None else None,
        "gw": sel_gw,
        "season": season,
        "denominator": sel_denominator,
        "includes_you": includes_you,
        "untrusted_selected": untrusted_selected,
        "unresolved_pick_entries": (sel_slot or {}).get("unresolved_entries"),
        "note": " ".join(sel_note_bits) or None,
    }

    if sel_n:
        sel_comp = [{"tag": s["key"], "n": s["n"], "label": s["label"]}
                    for s in segment_rows if s["selected"]]
        fields.append({
            "key": "selected",
            "label": "Selected field (" + " + ".join(resolved) + f", {sel_n})",
            "short": "selected",
            "kind": "segments",
            "role": "field",
            "measures": ["own", "eo"],
            "denominator": sel_denominator,
            "provider": "fact_manager_pick crawl",
            "metric": None,
            "cohort": None,
            "season": season,
            "gw": sel_gw,
            "n": sel_n,
            "players": n_with("selected", "own"),
            "as_of": as_of,
            "live": True,
            "same_values_as_gw": None,
            "note": "Observed squads over the union of the selected sets. "
                    "One manager is "
                    + (f"{100.0 / sel_n:.2f} percentage points." if sel_n
                       else "one row.")
                    + (" An untrustworthy set is included; see segments[]."
                       if untrusted_selected else ""),
            "composition": sel_comp,
            "overlaps": bool(overlap) if overlap is not None else None,
            "segments": resolved,
            "mini_league_n": next(
                (s["n"] for s in segment_rows
                 if s["key"] == "mini_league" and s["selected"]), None),
        })

    return segment_rows, selection, includes_you, sel_denominator


def _two_fields(
    *,
    cohort: str,
    composition: dict[tuple[str, int], list[dict]],
    elite_cohort: int,
    elite_gw: int | None,
    resolved: list[str],
    segment_rows: list[dict[str, Any]],
    sel_gw: int | None,
    sel_n: int | None,
) -> dict[str, Any]:
    """The two sets the one word 'field' names, told apart explicitly."""
    # -- the two "fields" under one word, told apart explicitly --------------
    # R2's finding: the measured cohort (elite_* columns, the charts) and the
    # segment selection (the WHO-IS-IN-IT card, diff, whatif) are DIFFERENT
    # populations both answering to "the field". This block names both, with
    # their n and their mini-league content, so the UI links them visibly and
    # no sentence attaches one population's trend to the other's level.
    ml_measured = None
    if elite_gw is not None:
        ml_measured = next(
            (int(x["n"]) for x in composition.get((cohort, elite_gw)) or []
             if x.get("tag") == "mini_league"), None)
    field_distinction = {
        "measured_cohort": {
            "key": f"cohort:{cohort}",
            "n": elite_cohort or None,
            "gw": elite_gw,
            "includes_mini_league": (bool(ml_measured)
                                     if elite_cohort else None),
            "mini_league_n": ml_measured,
        },
        "selection": {
            "key": "selected",
            "n": sel_n,
            "gw": sel_gw,
            "includes_mini_league": "mini_league" in resolved,
            "mini_league_n": next(
                (s["n"] for s in segment_rows
                 if s["key"] == "mini_league" and s["selected"]), None),
        },
        "note": (
            f"Two populations answer to the word \"field\" in this payload: "
            f"the measured cohort (cohort:{cohort}, the elite_* columns and "
            f"rows[].fields) and the segment selection (rows[].fields"
            f"[\"selected\"], diff, whatif). They are different sets of "
            f"managers with different denominators; a level from one and a "
            f"trend from the other must never share a sentence."
        ),
    }

    return field_distinction


def _livefpl_instant(
    cov_at_gw: dict[tuple[str, int], dict], external_gw: dict[str, int]
) -> dict[str, Any] | None:
    """When LiveFPL's predicted EO was captured, read at the point of use."""
    # -- LiveFPL predicted-EO capture instant, at point of use ---------------
    eo_pred_captured = None
    g_pred = external_gw.get("eo_predicted")
    if g_pred is not None:
        c_pred = cov_at_gw.get(("eo_predicted", g_pred), {})
        eo_pred_captured = {"as_of": c_pred.get("latest"), "gw": g_pred}

    return eo_pred_captured
