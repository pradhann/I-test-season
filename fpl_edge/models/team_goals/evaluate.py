"""Walk-forward out-of-sample evaluation. The only thing that settles anything.

Protocol
--------
For every gameweek of every evaluation season, take a snapshot at that
gameweek's deadline, refit every model on what is visible at that instant, and
predict that gameweek's fixtures. Nothing is predicted twice, nothing is
predicted with knowledge of itself, and the models are refitted 38 times a
season rather than once, because a model that is only accurate when fitted on
the whole season is not a model you can use in August.

Metrics
-------
``log_loss`` on the 1X2 outcome, ``rps_outcome`` on the ordered 1X2 space,
``rps_goal_diff`` on the ordered goal-difference space (the scoreline-level
version), and ``brier_cs`` on clean sheets, evaluated per team-match because a
fixture produces two clean-sheet forecasts and both price defenders.

Fair comparison
---------------
The market model only predicts fixtures somebody priced. Comparing its metrics
over its own covered subset against another model's metrics over every fixture
would be meaningless, so the headline table is computed over the intersection of
fixtures *all* models predicted, and coverage is reported beside it. Full
coverage numbers for the non-market models are reported as a separate scope.

Run: ``uv run python -m fpl_edge.models.team_goals.evaluate``

STATUS: EVALUATION HARNESS, run via `python -m` (see docs/models/); not in the production import closure and not expected to be. It exists to mint the committed evidence CSVs, not to serve requests.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fpl_edge.models.team_goals import metrics as M
from fpl_edge.models.team_goals.base import BaseGoalModel
from fpl_edge.models.team_goals.baselines import HomeAdvantageOnlyModel, LastSeasonTableModel
from fpl_edge.models.team_goals.blend import BlendedGoalModel
from fpl_edge.models.team_goals.data import (
    InsufficientHistoryError,
    promoted_team_codes,
    read_finished_matches,
    teams_in_season,
)
from fpl_edge.models.team_goals.dixon_coles import DixonColesModel
from fpl_edge.models.team_goals.market import MarketImpliedModel
from fpl_edge.models.team_goals.odds import FrameOddsProvider, SnapshotOddsProvider
from fpl_edge.models.team_goals.scoreline import (
    clean_sheet_probs,
    goal_difference_probs,
    outcome_probs,
)
from fpl_edge.models.team_goals.synthetic import build_warehouse, load_league
from fpl_edge.rules import rules
from fpl_edge.store import Warehouse
from fpl_edge.types import GwId, Season

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "team_goals"
DOCS_DIR = REPO_ROOT / "docs" / "models"
REAL_DB = REPO_ROOT / "data" / "warehouse" / "fpl.duckdb"

EVAL_SEASONS = ("2023-24", "2024-25", "2025-26")
#: Where a prior can still be heard over the data. By GW20 a promoted club has
#: 20 matches on record and the prior is nearly irrelevant; the interesting
#: question is what the model says in August, which is where the engine has to
#: commit a squad.
EARLY_SEASON_GWS = 6
VALIDATION_SEASON = "2022-23"
HALF_LIFE_GRID = (90.0, 150.0, 240.0, 400.0, 700.0, 1200.0)

MAX_GOALS = 8
#: Goal-difference support for the scoreline RPS: -8 .. +8.
GD_OFFSET = MAX_GOALS
GD_CATEGORIES = 2 * MAX_GOALS + 1

MODEL_ORDER = (
    "home_advantage_only",
    "last_season_table",
    "market_implied",
    "dixon_coles_no_promoted_prior",
    "dixon_coles",
    "blend_dc_market",
)


# -- prediction --------------------------------------------------------------

def deadlines(warehouse: Warehouse, season: str) -> list[tuple[int, dt.datetime]]:
    """Gameweek deadlines, from ``dim_event`` where it exists.

    Historical seasons in the warehouse carry a fixture schedule but no
    ``dim_event`` rows, so for those the deadline is reconstructed from the
    published schedule using the verified rule
    ``deadlines.offset_before_first_kickoff_minutes``. Reading the schedule is
    not leakage -- fixture dates are public months ahead, and only ``gw`` and
    ``kickoff_utc`` are taken from it -- and the offset comes from the rule
    registry rather than a magic number, so if the league changes it this
    follows rather than silently drifting.
    """
    ref = dt.datetime(int(season[:4]), 8, 5, tzinfo=dt.UTC)
    ev = warehouse.snapshot_at(ref).table("dim_event", where="season = ?", params=[season])
    if not ev.empty:
        return [
            (int(r.gw), pd.Timestamp(r.deadline_utc).to_pydatetime())
            for r in ev.sort_values("gw").itertuples(index=False)
        ]
    far_future = dt.datetime(2100, 1, 1, tzinfo=dt.UTC)
    fx = warehouse.snapshot_at(far_future).table(
        "fact_fixture", where="season = ?", params=[season]
    )[["gw", "kickoff_utc"]]
    fx = fx[fx["gw"].notna() & fx["kickoff_utc"].notna()]
    if fx.empty:
        raise InsufficientHistoryError(f"no gameweek calendar or schedule for {season}")
    offset = dt.timedelta(
        minutes=int(rules().get("deadlines.offset_before_first_kickoff_minutes"))
    )
    first = fx.groupby("gw")["kickoff_utc"].min().sort_index()
    return [(int(gw), pd.Timestamp(k).to_pydatetime() - offset) for gw, k in first.items()]


def walk_forward(
    warehouse: Warehouse,
    build_models: Callable[[], dict[str, BaseGoalModel]],
    seasons: tuple[str, ...],
    *,
    max_gw: int | None = None,
) -> pd.DataFrame:
    """Refit and predict every gameweek of every requested season."""
    rows: list[dict[str, object]] = []
    for season in seasons:
        models = build_models()
        for gw, deadline in deadlines(warehouse, season):
            if max_gw is not None and gw > max_gw:
                break
            snap = warehouse.snapshot_at(deadline)
            for name, model in models.items():
                try:
                    frame = model.predict(snap, Season(season), [GwId(gw)])
                except InsufficientHistoryError:
                    continue
                if frame.empty:
                    continue
                home_rows = frame[frame["is_home"]]
                for r in home_rows.itertuples(index=False):
                    fid = int(r.fixture_id)
                    mat = model.score_matrix(fid, MAX_GOALS)
                    ph, pdw, pa = outcome_probs(mat)
                    cs_h, cs_a = clean_sheet_probs(mat)
                    _, gd_probs = goal_difference_probs(mat)
                    rows.append(
                        {
                            "model": name,
                            "season": season,
                            "gw": gw,
                            "fixture_id": fid,
                            "home_team_code": int(r.team_code),
                            "away_team_code": int(r.opponent_code),
                            "p_home": ph,
                            "p_draw": pdw,
                            "p_away": pa,
                            "p_cs_home": cs_h,
                            "p_cs_away": cs_a,
                            "xg_home": float(r.exp_goals_for),
                            "xg_away": float(r.exp_goals_against),
                            "gd_probs": gd_probs,
                        }
                    )
    return pd.DataFrame(rows)


def attach_results(preds: pd.DataFrame, results: pd.DataFrame) -> pd.DataFrame:
    """Join realised scorelines. Harness-level read; no model sees this frame."""
    res = results[["season", "fixture_id", "home_score", "away_score"]]
    out = preds.merge(res, on=["season", "fixture_id"], how="inner", validate="many_to_one")
    diff = out["home_score"] - out["away_score"]
    out["outcome"] = np.where(diff > 0, 0, np.where(diff == 0, 1, 2))
    out["gd_index"] = np.clip(diff, -MAX_GOALS, MAX_GOALS) + GD_OFFSET
    out["cs_home"] = (out["away_score"] == 0).astype(float)
    out["cs_away"] = (out["home_score"] == 0).astype(float)
    return out


# -- scoring -----------------------------------------------------------------


def score_frame(df: pd.DataFrame) -> dict[str, float]:
    probs = df[["p_home", "p_draw", "p_away"]].to_numpy(float)
    probs = probs / probs.sum(axis=1, keepdims=True)
    out = df["outcome"].to_numpy(int)
    cs_p = np.concatenate([df["p_cs_home"].to_numpy(float), df["p_cs_away"].to_numpy(float)])
    cs_y = np.concatenate([df["cs_home"].to_numpy(float), df["cs_away"].to_numpy(float)])
    rec = {
        "n_fixtures": float(len(df)),
        "log_loss": M.log_loss(probs, out),
        "rps_outcome": M.rps(probs, out),
        "brier_cs": M.brier(cs_p, cs_y),
        "mean_p_cs": float(cs_p.mean()),
        "base_rate_cs": float(cs_y.mean()),
    }
    if "gd_probs" in df.columns:
        gd = np.stack(df["gd_probs"].to_numpy())
        rec["rps_goal_diff"] = M.rps(gd, df["gd_index"].to_numpy(int))
    return rec


def summarise(scored: pd.DataFrame, *, scope: str) -> pd.DataFrame:
    rows = []
    for model in MODEL_ORDER:
        grp = scored[scored["model"] == model]
        if grp.empty:
            continue
        rec: dict[str, object] = {"scope": scope, "model": model}
        rec.update(score_frame(grp))
        rows.append(rec)
    return pd.DataFrame(rows)


def common_coverage(scored: pd.DataFrame) -> pd.DataFrame:
    """Restrict to fixtures every model predicted, so the table compares like with like."""
    counts = scored.groupby(["season", "fixture_id"])["model"].nunique()
    n_models = scored["model"].nunique()
    keep = set(counts[counts == n_models].index)
    mask = [
        (s, f) in keep for s, f in zip(scored["season"], scored["fixture_id"], strict=True)
    ]
    return scored[np.array(mask)]


def promoted_slice(scored: pd.DataFrame, results: pd.DataFrame) -> pd.DataFrame:
    """Only fixtures involving a club with no prior top-flight match on record."""
    keep_rows = []
    for season in sorted(scored["season"].unique()):
        target = teams_in_season(results, season)
        promoted = promoted_team_codes(results, target, season=season)
        sub = scored[scored["season"] == season]
        keep_rows.append(
            sub[
                sub["home_team_code"].isin(promoted) | sub["away_team_code"].isin(promoted)
            ]
        )
    return pd.concat(keep_rows, ignore_index=True) if keep_rows else scored.iloc[:0]


def paired_bootstrap_deltas(
    scored: pd.DataFrame,
    *,
    reference: str,
    n_boot: int = 2000,
    seed: int = 20260818,
) -> pd.DataFrame:
    """Paired bootstrap over fixtures for each model's metric delta vs a reference.

    Paired, because every model forecasts the same fixtures and the fixtures are
    what is random; an unpaired comparison would drown a real 0.02-nat
    difference in between-fixture variance. Resampling fixtures (not team-match
    rows) keeps the two clean-sheet forecasts from one fixture together, since
    they are not independent draws.
    """
    rng = np.random.default_rng(seed)
    wide = scored.pivot_table(
        index=["season", "fixture_id"],
        columns="model",
        values=["p_home", "p_draw", "p_away", "p_cs_home", "p_cs_away"],
        aggfunc="first",
    )
    truth = (
        scored.drop_duplicates(["season", "fixture_id"])
        .set_index(["season", "fixture_id"])[["outcome", "cs_home", "cs_away"]]
        .loc[wide.index]
    )
    models = [m for m in MODEL_ORDER if m in set(scored["model"])]
    n = len(wide)
    idx_boot = rng.integers(0, n, size=(n_boot, n))

    def stats(model: str, rows: np.ndarray) -> tuple[float, float]:
        probs = np.column_stack(
            [wide[("p_home", model)].to_numpy(), wide[("p_draw", model)].to_numpy(),
             wide[("p_away", model)].to_numpy()]
        )[rows]
        probs = probs / probs.sum(axis=1, keepdims=True)
        ll = M.log_loss(probs, truth["outcome"].to_numpy(int)[rows])
        cs_p = np.concatenate(
            [wide[("p_cs_home", model)].to_numpy()[rows],
             wide[("p_cs_away", model)].to_numpy()[rows]]
        )
        cs_y = np.concatenate(
            [truth["cs_home"].to_numpy(float)[rows], truth["cs_away"].to_numpy(float)[rows]]
        )
        return ll, M.brier(cs_p, cs_y)

    base_ll, base_cs = stats(reference, np.arange(n))
    rows = []
    for model in models:
        if model == reference:
            continue
        obs_ll, obs_cs = stats(model, np.arange(n))
        d_ll = np.empty(n_boot)
        d_cs = np.empty(n_boot)
        for b in range(n_boot):
            sel = idx_boot[b]
            m_ll, m_cs = stats(model, sel)
            r_ll, r_cs = stats(reference, sel)
            d_ll[b] = m_ll - r_ll
            d_cs[b] = m_cs - r_cs
        rows.append(
            {
                "model": model,
                "reference": reference,
                "d_log_loss": obs_ll - base_ll,
                "d_log_loss_lo95": float(np.quantile(d_ll, 0.025)),
                "d_log_loss_hi95": float(np.quantile(d_ll, 0.975)),
                "d_brier_cs": obs_cs - base_cs,
                "d_brier_cs_lo95": float(np.quantile(d_cs, 0.025)),
                "d_brier_cs_hi95": float(np.quantile(d_cs, 0.975)),
                "n_fixtures": n,
            }
        )
    return pd.DataFrame(rows)


def clean_sheet_calibration(scored: pd.DataFrame, *, n_bins: int = 10) -> pd.DataFrame:
    rows = []
    for model in MODEL_ORDER:
        grp = scored[scored["model"] == model]
        if grp.empty:
            continue
        p = np.concatenate([grp["p_cs_home"].to_numpy(float), grp["p_cs_away"].to_numpy(float)])
        y = np.concatenate([grp["cs_home"].to_numpy(float), grp["cs_away"].to_numpy(float)])
        for rec in M.calibration_table(p, y, n_bins=n_bins):
            rows.append({"model": model, **rec})
    return pd.DataFrame(rows)


# -- model construction ------------------------------------------------------


def model_factory(
    odds: pd.DataFrame, routes: pd.DataFrame, half_life: float
) -> Callable[[], dict[str, BaseGoalModel]]:
    def build() -> dict[str, BaseGoalModel]:
        return {
            "home_advantage_only": HomeAdvantageOnlyModel(),
            "last_season_table": LastSeasonTableModel(),
            "dixon_coles_no_promoted_prior": DixonColesModel(
                half_life_days=half_life, routes=routes, use_promoted_prior=False
            ),
            "dixon_coles": DixonColesModel(half_life_days=half_life, routes=routes),
            "market_implied": MarketImpliedModel(FrameOddsProvider(odds)),
            "blend_dc_market": BlendedGoalModel(
                DixonColesModel(half_life_days=half_life, routes=routes),
                MarketImpliedModel(FrameOddsProvider(odds)),
                market_weight=0.5,
            ),
        }

    return build


def all_results(warehouse: Warehouse) -> pd.DataFrame:
    """Every finished match, for the harness only."""
    far_future = dt.datetime(2100, 1, 1, tzinfo=dt.UTC)
    return read_finished_matches(warehouse.snapshot_at(far_future))


def tune_half_life(
    warehouse: Warehouse, routes: pd.DataFrame, *, season: str = VALIDATION_SEASON
) -> pd.DataFrame:
    """Grid-search the decay half-life on a season strictly before the eval window."""
    results = all_results(warehouse)
    rows = []
    for hl in HALF_LIFE_GRID:
        def build(hl: float = hl) -> dict[str, BaseGoalModel]:
            return {"dixon_coles": DixonColesModel(half_life_days=hl, routes=routes)}

        scored = attach_results(walk_forward(warehouse, build, (season,)), results)
        rec: dict[str, object] = {"half_life_days": hl}
        rec.update(score_frame(scored))
        rows.append(rec)
    return pd.DataFrame(rows)


# -- entry points ------------------------------------------------------------


def run_synthetic(
    out_dir: Path = DOCS_DIR, *, fixtures_dir: Path = FIXTURES_DIR
) -> dict[str, Any]:
    league = load_league(fixtures_dir)
    tmp = Path(tempfile.mkdtemp(prefix="team_goals_eval_"))
    wh = build_warehouse(league, tmp / "eval.duckdb")
    results = all_results(wh)

    hl_table = tune_half_life(wh, league.routes)
    best_hl = float(hl_table.sort_values("log_loss").iloc[0]["half_life_days"])

    preds = walk_forward(wh, model_factory(league.odds, league.routes, best_hl), EVAL_SEASONS)
    scored = attach_results(preds, results)

    common = common_coverage(scored)
    early = common[common["gw"] <= EARLY_SEASON_GWS]
    tables = [
        summarise(common, scope="common_coverage"),
        summarise(scored, scope="own_coverage"),
        summarise(promoted_slice(common, results), scope="promoted_fixtures"),
        summarise(early, scope=f"gw1_{EARLY_SEASON_GWS}"),
        summarise(
            promoted_slice(early, results), scope=f"promoted_fixtures_gw1_{EARLY_SEASON_GWS}"
        ),
    ]
    metrics = pd.concat(tables, ignore_index=True)
    calib = clean_sheet_calibration(common)
    promoted = promoted_slice(common, results)
    deltas = pd.concat(
        [
            paired_bootstrap_deltas(common, reference="market_implied").assign(
                scope="common_coverage"
            ),
            paired_bootstrap_deltas(common, reference="dixon_coles").assign(
                scope="common_coverage"
            ),
            paired_bootstrap_deltas(promoted, reference="dixon_coles").assign(
                scope="promoted_fixtures"
            ),
        ],
        ignore_index=True,
    )
    deltas = deltas[["scope", *[c for c in deltas.columns if c != "scope"]]]

    out_dir.mkdir(parents=True, exist_ok=True)
    metrics.round(6).to_csv(out_dir / "team_goals_metrics.csv", index=False)
    calib.round(6).to_csv(out_dir / "team_goals_calibration.csv", index=False)
    hl_table.round(6).to_csv(out_dir / "team_goals_half_life.csv", index=False)
    deltas.round(6).to_csv(out_dir / "team_goals_deltas.csv", index=False)

    def _n(model: str) -> int:
        sub = scored[scored["model"] == model]
        return len(sub.drop_duplicates(["season", "fixture_id"]))

    n_total, n_market = _n("dixon_coles"), _n("market_implied")
    return {
        "best_half_life_days": best_hl,
        "half_life_table": hl_table,
        "metrics": metrics,
        "calibration": calib,
        "deltas": deltas,
        "market_coverage": n_market / n_total if n_total else float("nan"),
        "n_fixtures": n_total,
        "warehouse": tmp / "eval.duckdb",
    }


def run_real(db_path: Path = REAL_DB, *, season: str | None = None) -> dict[str, Any]:
    """Attempt the real warehouse. Reports what is missing rather than faking it."""
    report: dict[str, Any] = {"db_path": str(db_path), "exists": db_path.exists()}
    if not db_path.exists():
        return report
    wh = Warehouse(db_path, read_only=True)
    now = dt.datetime.now(dt.UTC)
    snap = wh.snapshot_at(now)
    fx = snap.table("fact_fixture")
    report["seasons_in_warehouse"] = sorted(fx["season"].unique()) if not fx.empty else []
    finished = fx[fx["home_score"].notna()] if not fx.empty else fx
    report["finished_matches_visible"] = len(finished)
    odds = snap.table("fact_odds")
    report["odds_rows_visible"] = len(odds)

    target = season or (max(fx["season"].unique()) if not fx.empty else None)
    report["target_season"] = target
    if target is None:
        return report
    try:
        gw = snap.next_gw(target)
    except KeyError:
        gw = None
    report["next_gw"] = gw

    dc = DixonColesModel()
    try:
        frame = dc.predict(snap, Season(target), [GwId(gw)]) if gw else pd.DataFrame()
        report["dixon_coles"] = "ok"
        report["dixon_coles_rows"] = len(frame)
    except InsufficientHistoryError as exc:
        report["dixon_coles"] = f"blocked: {exc}"
        report["dixon_coles_rows"] = 0

    market = MarketImpliedModel(SnapshotOddsProvider(snap))
    try:
        frame = (
            market.predict(snap, Season(target), [GwId(gw)]) if gw else pd.DataFrame()
        )
        report["market_implied_rows"] = len(frame)
        report["market_coverage"] = market.last_coverage
    except (InsufficientHistoryError, ValueError, KeyError) as exc:  # pragma: no cover
        report["market_implied_rows"] = 0
        report["market_error"] = repr(exc)
    wh.close()
    return report


REAL_EVAL_SEASONS = ("2023-24", "2024-25", "2025-26")


def run_real_eval(
    db_path: Path = REAL_DB,
    *,
    seasons: tuple[str, ...] = REAL_EVAL_SEASONS,
    half_life: float = 400.0,
    out_dir: Path = DOCS_DIR,
) -> dict[str, Any]:
    """Walk-forward on the real warehouse, with whatever data has actually landed.

    Two honest limitations, both reported rather than papered over:

    * There are no odds in ``fact_odds``, so the market baseline -- the one
      baseline that matters most -- cannot be measured on real data at all.
      Its synthetic result stands as an indication, not a measurement.
    * The earliest evaluation season has only one prior season visible, which
      is not enough to observe three promotions and fit the promoted prior. For
      that season the model falls back to the documented assumed prior; from the
      next season on the prior is fitted. ``prior_source_by_season`` records it.
    """
    wh = Warehouse(db_path, read_only=True)
    results = all_results(wh)
    available = sorted(results["season"].unique())
    seasons = tuple(s for s in seasons if s in available)

    def build() -> dict[str, BaseGoalModel]:
        return {
            "home_advantage_only": HomeAdvantageOnlyModel(),
            "last_season_table": LastSeasonTableModel(),
            "dixon_coles_no_promoted_prior": DixonColesModel(
                half_life_days=half_life, allow_fallback_prior=True, use_promoted_prior=False
            ),
            "dixon_coles": DixonColesModel(half_life_days=half_life, allow_fallback_prior=True),
        }

    scored = attach_results(walk_forward(wh, build, seasons), results)
    tables = [summarise(scored, scope="all_seasons")]
    for season in seasons:
        tables.append(summarise(scored[scored["season"] == season], scope=season))
    tables.append(summarise(promoted_slice(scored, results), scope="promoted_fixtures"))
    tables.append(summarise(scored[scored["gw"] <= EARLY_SEASON_GWS], scope=f"gw1_{EARLY_SEASON_GWS}"))
    metrics = pd.concat(tables, ignore_index=True)

    prior_source: dict[str, str] = {}
    for season in seasons:
        deadline = deadlines(wh, season)[0][1]
        model = DixonColesModel(half_life_days=half_life, allow_fallback_prior=True)
        fit = model.fit(wh.snapshot_at(deadline), Season(season))
        prior_source[season] = (
            f"{fit.prior.source} (clubs={fit.prior.n_clubs}, "
            f"attack={fit.prior.attack_mean:+.3f}, defence={fit.prior.defence_mean:+.3f})"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    metrics.round(6).to_csv(out_dir / "team_goals_metrics_real.csv", index=False)
    clean_sheet_calibration(scored).round(6).to_csv(
        out_dir / "team_goals_calibration_real.csv", index=False
    )
    deltas = paired_bootstrap_deltas(scored, reference="dixon_coles")
    deltas.round(6).to_csv(out_dir / "team_goals_deltas_real.csv", index=False)
    wh.close()
    return {
        "seasons": seasons,
        "metrics": metrics,
        "deltas": deltas,
        "prior_source_by_season": prior_source,
        "n_fixtures": len(scored.drop_duplicates(["season", "fixture_id"])),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--real", action="store_true", help="probe the real warehouse instead")
    ap.add_argument(
        "--real-eval", action="store_true", help="walk-forward on the real warehouse"
    )
    ap.add_argument("--out", type=Path, default=DOCS_DIR)
    args = ap.parse_args(argv)

    if args.real:
        print(json.dumps(run_real(), indent=2, default=str))
        return 0

    if args.real_eval:
        res = run_real_eval(out_dir=args.out)
        pd.set_option("display.width", 200)
        print("promoted prior actually used, per evaluation season:")
        for season, src in res["prior_source_by_season"].items():
            print(f"  {season}: {src}")
        print(
            f"\nreal-data walk-forward, seasons {', '.join(res['seasons'])}, "
            f"{res['n_fixtures']} fixtures. NO ODDS IN THE WAREHOUSE: the market "
            f"baseline is absent from this table."
        )
        cols = ["scope", "model", "n_fixtures", "log_loss", "rps_outcome", "rps_goal_diff",
                "brier_cs", "mean_p_cs", "base_rate_cs"]
        print(res["metrics"][cols].round(5).to_string(index=False))
        print("\npaired bootstrap vs dixon_coles (negative = better):")
        print(res["deltas"].round(5).to_string(index=False))
        return 0

    res = run_synthetic(args.out)
    pd.set_option("display.width", 160)
    print(f"\nhalf-life sweep on validation season {VALIDATION_SEASON}:")
    print(res["half_life_table"][["half_life_days", "log_loss", "rps_outcome", "brier_cs"]]
          .round(5).to_string(index=False))
    print(f"\nselected half-life: {res['best_half_life_days']:.0f} days")
    print(f"market coverage over eval seasons: {res['market_coverage']:.3f} "
          f"of {res['n_fixtures']} fixtures")
    cols = ["scope", "model", "n_fixtures", "log_loss", "rps_outcome", "rps_goal_diff",
            "brier_cs", "mean_p_cs", "base_rate_cs"]
    print("\nwalk-forward out-of-sample, seasons " + ", ".join(EVAL_SEASONS) + ":")
    print(res["metrics"][cols].round(5).to_string(index=False))
    print("\npaired bootstrap deltas on common coverage (negative = better than reference):")
    print(res["deltas"].round(5).to_string(index=False))
    print(f"\nwrote {args.out / 'team_goals_metrics.csv'}")
    print(f"wrote {args.out / 'team_goals_calibration.csv'}")
    print(f"wrote {args.out / 'team_goals_half_life.csv'}")
    print(f"wrote {args.out / 'team_goals_deltas.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
