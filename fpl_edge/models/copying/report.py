"""The run that produces the numbers, from the warehouse, with nothing invented.

    uv run python -m fpl_edge.models.copying.report

Reads what the crawl actually ingested and emits the analysis: field-size
estimates, normal scores, the fitted skill model, the persistence measurement,
the shortlist, the cohort effect-size table, and an explicit statement of which
feature tiers the warehouse can currently support.

The last of those is not a formality. Most of the strategy features this package
defines need squad data, squad data needs a gameweek to have finished, and
before GW1 there is none. A report that quietly omitted the empty sections would
read as though the analysis had been done and found nothing, which is a very
different claim from the analysis being impossible today.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from fpl_edge.config import USER
from fpl_edge.ingest.rivals import roster, schema
from fpl_edge.ingest.rivals.elite_list import NAMES as ELITE_NAMES
from fpl_edge.models.copying import effects, features, skill
from fpl_edge.store import Warehouse

#: Cohort labels, assigned from the ``source`` recorded at crawl time plus the
#: winner verification. A manager can qualify for several; the first match in
#: this order wins, most-specific first.
COHORT_ORDER = ("winner", "repeat_top10k", "elite_list", "expert", "mini_league", "snowball")


def _load(wh: Warehouse) -> dict[str, pd.DataFrame]:
    present = schema.rival_tables_present(wh)
    out: dict[str, pd.DataFrame] = {}
    for table in ("dim_manager", "fact_manager_season", "fact_manager_gw",
                  "fact_manager_pick", "fact_manager_transfer", "fact_manager_chip"):
        out[table] = wh.sql(f"SELECT * FROM {table}") if table in present else pd.DataFrame()
    return out


def _latest_per_entity(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Collapse the point-in-time table to its most recent row per entity.

    The crawl is append-only, so re-running it adds a second ``as_of`` for every
    manager. Averaging across those would count each manager twice and shrink
    every standard error by sqrt(2) for no reason at all.
    """
    if df.empty:
        return df
    return (df.sort_values("as_of").groupby(keys, as_index=False).last())


def assign_cohorts(
    managers: pd.DataFrame,
    seasons: pd.DataFrame,
    confirmed_winners: set[int],
    *,
    top10k_seasons_required: int = 2,
) -> pd.DataFrame:
    """Label each manager with the cohort they belong to.

    ``repeat_top10k`` is the cohort the whole exercise is really about, and it is
    defined on the record rather than on reputation: at least
    ``top10k_seasons_required`` separate seasons finishing inside the top 10,000.
    Two is deliberately a low bar -- with a field of six million, one top-10k
    finish happens to roughly six hundred people a year by chance, but two
    independent ones happen to well under one person a year by chance, so the
    second finish is where the evidence actually starts.
    """
    top10k = (
        seasons[seasons["overall_rank"] <= 10_000]
        .groupby("entry_id")["season"].nunique()
    )
    rows = []
    for m in managers.itertuples():
        eid = int(m.entry_id)
        source = str(m.source or "")
        if eid in confirmed_winners:
            cohort = "winner"
        elif int(top10k.get(eid, 0)) >= top10k_seasons_required:
            cohort = "repeat_top10k"
        elif source.startswith("elite_list"):
            cohort = "elite_list"
        elif source.startswith("expert"):
            cohort = "expert"
        elif source.startswith("mini_league"):
            cohort = "mini_league"
        else:
            cohort = "snowball"
        rows.append({"entry_id": eid, "cohort": cohort, "source": source,
                     "name": m.player_name, "entry_name": m.entry_name,
                     "years_active": m.years_active})
    return pd.DataFrame(rows)


def build(db_path: str | None = None, *, cut_season: str = "2023/24") -> dict[str, Any]:
    """Run the whole analysis and return it as plain data."""
    with (Warehouse(read_only=True) if db_path is None
          else Warehouse(db_path, read_only=True)) as wh:
        tables = _load(wh)

    managers = _latest_per_entity(tables["dim_manager"], ["entry_id"])
    seasons = _latest_per_entity(tables["fact_manager_season"], ["entry_id", "season"])
    gws = _latest_per_entity(tables["fact_manager_gw"], ["entry_id", "season", "gw"])
    picks = tables["fact_manager_pick"]
    chips = _latest_per_entity(tables["fact_manager_chip"], ["entry_id", "season", "gw"])

    out: dict[str, Any] = {
        "counts": {
            "managers_in_warehouse": int(len(managers)),
            "manager_season_rows": int(len(seasons)),
            "managers_with_history": int(seasons["entry_id"].nunique()) if not seasons.empty else 0,
            "gameweek_rows": int(len(gws)),
            "pick_rows": int(len(picks)),
        },
        "tiers_available": features.available_tiers(seasons, gws, picks),
    }
    if seasons.empty:
        out["error"] = "no manager season history in the warehouse; run the crawl first"
        return out

    field_sizes = skill.estimate_field_sizes(seasons)
    panel = skill.to_normal_scores(seasons, field_sizes)
    model = skill.fit_skill(panel)
    scores = skill.score_managers(panel, model)
    persist = skill.persistence(panel, model, cut_season=cut_season)

    out["field_sizes"] = field_sizes.to_dict("records")
    out["skill_model"] = {
        "pool_mean_z": model.mu,
        "sigma2_within": model.sigma2_within,
        "tau2_between": model.tau2_between,
        "icc": model.icc,
        "seasons_for_reliability_0.8": model.seasons_for_reliability(),
        "n_managers": model.n_managers,
        "n_manager_seasons": model.n_seasons_total,
        "n_managers_multi_season": model.n_managers_multi,
    }
    out["persistence"] = {
        "lag1_pairs": persist.lag1_pairs,
        "lag1_pearson": persist.lag1_pearson,
        "lag1_spearman": persist.lag1_spearman,
        "icc": persist.icc,
        "walk_forward": persist.walk_forward,
        "verdict": persist.verdict(),
    }

    names = dict(ELITE_NAMES)
    names.update({int(m.entry_id): m.player_name for m in managers.itertuples()
                  if m.player_name})
    short = skill.shortlist(scores, panel, min_seasons=4, top_n=25, names=names)
    out["shortlist"] = short.to_dict("records")

    # Winner confirmation is recomputed from the histories already in the
    # warehouse rather than re-hitting the API: the entry's own `past` block is
    # the same evidence verify_winner_ids uses, and it is already local.
    winners_confirmed: set[int] = set()
    claimed = {eid: season for season, _n, eid, _s in roster.WINNERS if eid is not None}
    for eid, season in claimed.items():
        row = seasons[(seasons["entry_id"] == eid) & (seasons["season"] == season)]
        if not row.empty and int(row.iloc[0]["overall_rank"]) == 1:
            winners_confirmed.add(int(eid))
    out["winner_verification"] = _winner_table(seasons, claimed)

    cohorts = assign_cohorts(managers, seasons, winners_confirmed)
    merged = scores.merge(cohorts, on="entry_id", how="left")
    merged["cohort"] = merged["cohort"].fillna("snowball")
    # How the manager entered the pool, independent of their record. Comparing
    # ON this is the only non-circular cohort analysis available from seasonal
    # data: `repeat_top10k` is DEFINED by rank, so testing it against
    # rank-derived features rediscovers its own definition.
    merged["source_cohort"] = (
        merged["source"].fillna("snowball").str.split(":").str[0]
    )
    # The 'field' cohort is a definitional reference point, not a sample: an
    # average FPL manager sits at z = 0 by construction, since z is measured in
    # SDs of the whole field. It is reported as such and never fed to a t-test
    # against a sample, which would compare a distribution to a constant.
    out["cohort_sizes"] = merged["cohort"].value_counts().to_dict()
    out["cohort_summary"] = _cohort_summary(merged, panel)

    season_features = ["z_mean", "theta_hat", "n_seasons", "z_sd", "z_best"]

    # Two tables, and the distinction between them is the whole methodological
    # point. The record table compares cohorts DEFINED BY RANK on features
    # DERIVED FROM RANK; every large effect in it is a tautology and it is
    # emitted only so the circularity is visible rather than inferred.
    record_table = effects.compare_cohorts(merged, "cohort", season_features)
    out["effect_sizes_circular_do_not_cite"] = (
        record_table.to_dict("records") if not record_table.empty else []
    )
    out["circularity_note"] = (
        "cohort 'repeat_top10k' is defined as >=2 top-10k seasons, and every "
        "feature here is a function of the same rank sequence. Large effects in "
        "this table restate the cohort definition and are not findings."
    )

    # The honest table: cohorts defined by HOW a manager entered the pool
    # (published expert, all-time list, the user's mini-leagues, snowball),
    # which is independent of the rank features being compared.
    source_table = effects.compare_cohorts(merged, "source_cohort", season_features)
    out["effect_sizes"] = source_table.to_dict("records") if not source_table.empty else []
    out["surviving_effects"] = (
        effects.surviving(source_table).to_dict("records") if not source_table.empty else []
    )
    out["power"] = {
        f"{a}_vs_{b}": effects.power_note(
            int((merged["source_cohort"] == a).sum()),
            int((merged["source_cohort"] == b).sum()),
        )
        for a, b in [("expert", "snowball"), ("elite_list", "snowball"),
                     ("mini_league", "snowball"), ("winner", "snowball")]
    }

    strat = skill.persistence_by_stratum(panel, merged.set_index("entry_id")["cohort"])
    out["persistence_by_stratum"] = strat.to_dict("records")

    out["user"] = skill.compare_to(scores, USER.entry_id)
    if not panel[panel["entry_id"] == USER.entry_id].empty:
        u = panel[panel["entry_id"] == USER.entry_id].sort_values("season")
        out["user"]["seasons"] = [
            {"season": r.season, "rank": int(r.overall_rank), "z": float(r.z)}
            for r in u.itertuples()
        ]

    if not gws.empty:
        out["gameweek_features"] = features.gameweek_features(gws, chips).to_dict("records")
    if not chips.empty:
        out["chip_timing"] = features.chip_timing(chips).to_dict("records")

    return out


def _winner_table(seasons: pd.DataFrame, claimed: dict[int, str]) -> list[dict[str, Any]]:
    rows = []
    for season, name, eid, source in roster.WINNERS:
        if eid is None:
            rows.append({"season": season, "name": name, "entry_id": None,
                         "status": "no_published_id", "reported_rank": None,
                         "source": source})
            continue
        row = seasons[(seasons["entry_id"] == eid) & (seasons["season"] == season)]
        have_any = not seasons[seasons["entry_id"] == eid].empty
        if row.empty:
            status = ("season_absent_from_history" if have_any
                      else "entry_has_no_past_seasons")
            rank = None
        else:
            rank = int(row.iloc[0]["overall_rank"])
            status = "confirmed" if rank == 1 else "contradicted"
        rows.append({"season": season, "name": name, "entry_id": eid,
                     "status": status, "reported_rank": rank, "source": source})
    return rows


def _cohort_summary(merged: pd.DataFrame, panel: pd.DataFrame) -> list[dict[str, Any]]:
    top10k = panel[panel["overall_rank"] <= 10_000].groupby("entry_id")["season"].nunique()
    rows = []
    for cohort, grp in merged.groupby("cohort"):
        rows.append({
            "cohort": cohort,
            "n_managers": int(len(grp)),
            "median_seasons": float(grp["n_seasons"].median()),
            "mean_z": float(grp["z_mean"].mean()),
            "median_z": float(grp["z_mean"].median()),
            "mean_theta_hat": float(grp["theta_hat"].mean()),
            "mean_expected_percentile": float(grp["expected_percentile"].mean()),
            "mean_top10k_seasons": float(
                grp["entry_id"].map(top10k).fillna(0).mean()
            ),
        })
    return sorted(rows, key=lambda r: -r["mean_theta_hat"])


def main() -> None:
    print(json.dumps(build(), indent=2, default=str))


if __name__ == "__main__":
    main()
