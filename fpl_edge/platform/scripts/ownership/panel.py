"""``ownership_eo``: the assembler, and the panel registration.

This module owns the order the phases run in and the one dict literal that is
the payload; it computes nothing itself."""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import empty, q
from fpl_edge.platform.scripts.ownership.fields import (
    _field_ladder,
    _livefpl_instant,
    _notes,
    _selectable_sets,
    _two_fields,
)
from fpl_edge.platform.scripts.ownership.load import (
    _cohort_own_eo,
    _consensus_xpts,
    _eo_inventory,
    _eo_pivot,
    _other_season_eo,
    _squad_coverage,
    _sub_cohorts,
)
from fpl_edge.platform.scripts.ownership.schema import PARAMS_SCHEMA, RESULT_SCHEMA
from fpl_edge.platform.scripts.ownership.tools import _tool_momentum, _tool_squad_diff, _tool_whatif


def ownership_eo(
    wh,
    *,
    season: str,
    limit: int = 50,
    diff_max_own: float = 15.0,
    cohort: str = "elite",
    coverage: bool = True,
    segments: list[str] | None = None,
    ctx=None,
) -> dict[str, Any]:
    """Template and differentials from effective ownership plus consensus xPts.

    ``ctx`` is the requesting user. Everything on this panel is shared
    warehouse data except two things that are about one manager: the coverage
    column, which marks the rows that manager owns, and the "this selection
    includes you" line. Both read the context, and both degrade to their
    existing blank states when it has no readable squad.

    ``rows`` is the current template: ranked by the live EO metric for the
    requested season (falling back to FPL marginal ownership when no external
    feed covers it). ``differentials`` are low-ownership players with the best
    consensus xPts. ``last_season`` quarantines EO metrics recorded under a
    different season — never merged into the current table.

    ``segments`` chooses which crawl sets compose the field. ``segments`` (the
    result key) lists every set on offer with its own n and its trust flag,
    ``selection`` says what the choice resolved to and what its percentages are
    a share of, and ``fields[selected]`` / ``rows[].fields.selected`` carry the
    measurements over the union. ``diff``, ``whatif`` and ``momentum`` are
    three views of those same measurements — see the module docstring.
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

    gws_covered = _eo_inventory(wh, season)
    external, external_gw, eo_pred = _eo_pivot(wh, season)
    gw, xp = _consensus_xpts(wh, season)
    (elite, elite_cohort, elite_gw, cohort_rows, cohort_meta, cohorts_present,
     composition, rival) = _cohort_own_eo(wh, season, cohort)
    (seg_inventory, requested, resolved, known, unknown, sel_by_code, sel_by_gw,
     sel_gw, sel_n, sel_slot, pick_gw) = _sub_cohorts(wh, season, segments, rival)
    (all_rows, template, diffs, rows_ranked_by, squad, squad_meta,
     squad_note) = _squad_coverage(
        wh, season=season, players=players, limit=limit,
        diff_max_own=diff_max_own, coverage=coverage, elite=elite,
        eo_pred=eo_pred, external=external, cohort_rows=cohort_rows,
        sel_by_code=sel_by_code, sel_n=sel_n, xp=xp, ctx=ctx)
    last_season = _other_season_eo(wh, season, limit)
    metrics_note, cohort_note = _notes(
        wh, season=season, cohort=cohort, cohorts_present=cohorts_present,
        elite_cohort=elite_cohort, elite_gw=elite_gw, gws_covered=gws_covered,
        rival=rival)
    fields, as_of, cov_at_gw, n_with = _field_ladder(
        wh, season=season, all_rows=all_rows, cohort_meta=cohort_meta,
        composition=composition, external=external, external_gw=external_gw,
        gws_covered=gws_covered)
    segment_rows, selection, includes_you, sel_denominator = _selectable_sets(
        wh, season=season, as_of=as_of, fields=fields, known=known,
        n_with=n_with, pick_gw=pick_gw, requested=requested, resolved=resolved,
        seg_inventory=seg_inventory, sel_gw=sel_gw, sel_n=sel_n,
        sel_slot=sel_slot, squad_meta=squad_meta, unknown=unknown, user=ctx)
    diff_rows, by_code_row, squad_codes = _tool_squad_diff(
        all_rows=all_rows, limit=limit, sel_by_code=sel_by_code, sel_n=sel_n,
        squad=squad, squad_meta=squad_meta, template=template)
    whatif = _tool_whatif(
        all_rows=all_rows, includes_you=includes_you, sel_by_code=sel_by_code,
        sel_denominator=sel_denominator, sel_gw=sel_gw, sel_n=sel_n)
    momentum = _tool_momentum(
        wh, season=season, by_code_row=by_code_row, gw=gw,
        sel_by_gw=sel_by_gw, squad_codes=squad_codes, template=template)
    field_distinction = _two_fields(
        cohort=cohort, composition=composition, elite_cohort=elite_cohort,
        elite_gw=elite_gw, resolved=resolved, segment_rows=segment_rows,
        sel_gw=sel_gw, sel_n=sel_n)
    eo_pred_captured = _livefpl_instant(cov_at_gw, external_gw)

    return {
        "season": season,
        "rows": template,
        "rows_ranked_by": rows_ranked_by,
        "differentials": diffs,
        "segments": segment_rows,
        "selection": selection,
        "diff": diff_rows,
        "whatif": whatif,
        "momentum": momentum,
        "last_season": last_season,
        "metrics_note": metrics_note,
        "cohort_note": cohort_note,
        "cohort": cohort,
        "cohort_n": elite_cohort or None,
        "cohort_gw": elite_gw,
        "gws_covered": gws_covered,
        "fields": fields,
        "field_distinction": field_distinction,
        "eo_pred_captured": eo_pred_captured,
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
