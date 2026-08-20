"""Backtest clean-sheet derivations against realised results, 2022-25.

Offline by design: reads the football-data.co.uk season files already archived
under ``data/raw/odds_football_data`` by ``scripts/ingest_odds.py`` (closing
market-average prices plus full-time scores) and never touches the network.

Methods scored, all starting from the Shin-de-vigged closing consensus:

* ``poisson_h2h``     -- bivariate inversion of the 1X2 triple only, rho = 0.
* ``poisson_h2h_ou``  -- 1X2 plus Over/Under 2.5, rho = 0.
* ``dc_rho``          -- 1X2 plus O/U 2.5 with the Dixon-Coles low-score
  dependence. rho is fitted on 2022-23 and held fixed for 2023-24 and
  2024-25, so those two seasons are genuinely out-of-sample.

How rho is fitted -- two objectives, deliberately both reported
---------------------------------------------------------------
The obvious fit is a grid search minimising clean-sheet Brier on the fit
season. Run on a grid wide enough to contain an interior optimum, that fit
*fails to identify rho*, and the failure is the finding: see the
``rho`` verdict in docs/platform/odds_derivation.md and the numbers in
``cs_rho_grid.csv``. So the grid is scored on two objectives at once:

* **Brier** on realised clean sheets -- what the derivation is actually for.
* **Scoreline log-likelihood** -- the joint probability the tau-corrected
  matrix assigns to the realised (FTHG, FTAG). rho is a parameter of the
  *joint* low-score corner, so this is where it is identified at all; the
  lambdas are re-inverted at each rho so every candidate still reproduces
  the same 1X2 + totals quotes.

Whichever objective picks rho, the resulting method is then scored
out-of-sample on 2023-24 and 2024-25 against the rho = 0 inversion, and the
comparison decides which method the derivation layer should use.

The correct-score method cannot be backtested here: football-data carries no
correct-score history and The Odds API's historical snapshots are paid-only.
Its live-vs-inversion disagreement is quantified on GW1 2026-27 instead; see
docs/platform/odds_derivation.md.

Outputs (committed under docs/platform/):

* ``cs_brier.csv``       -- Brier score, base-rate Brier, bias per method x season.
* ``cs_calibration.csv`` -- 10-bin reliability curve per method, pooled over
  the two out-of-sample seasons.
* ``cs_rho_grid.csv``    -- the rho profile: clean-sheet Brier and scoreline
  log-likelihood at every grid point, which is the evidence behind the rho
  verdict.

Usage::

    uv run python scripts/backtest_clean_sheets.py
"""

from __future__ import annotations

import glob
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from fpl_edge.ingest.odds import devig
from fpl_edge.ingest.odds_derived import invert_match_odds
from fpl_edge.models.team_goals.scoreline import clean_sheet_probs, score_matrix

RAW_DIR = Path("data/raw/odds_football_data")
OUT_DIR = Path("docs/platform")

FIT_SEASON = "2022-23"
EVAL_SEASONS = ["2023-24", "2024-25"]

#: Deliberately wider than the Dixon-Coles literature (their published rho for
#: English league data sits near -0.03 to -0.13). An earlier, narrower grid
#: [-0.16, +0.04] returned its own right-hand endpoint, which is the classic
#: symptom of a boundary "optimum" on a flat surface rather than a fit. This
#: grid is wide enough that an interior optimum, if one exists, must show up
#: as one -- and tau stays positive across it at realistic lambdas.
RHO_GRID = [round(float(r), 2) for r in np.arange(-0.30, 0.3001, 0.02)]


def _season_code(season: str) -> str:
    return season[2:4] + season[5:7]


def load_season(season: str) -> pd.DataFrame:
    """Newest archived E0 file for a season, with closing consensus + result."""
    pattern = str(RAW_DIR / f"mmz4281_{_season_code(season)}_E0.csv_*.csv")
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise FileNotFoundError(
            f"no archived file for {season}; run scripts/ingest_odds.py "
            f"--history {season} first"
        )
    df = pd.read_csv(io.StringIO(Path(paths[-1]).read_text(encoding="latin-1")))
    df.columns = [str(c).lstrip("﻿").strip() for c in df.columns]
    need = ["FTHG", "FTAG", "AvgCH", "AvgCD", "AvgCA", "AvgC>2.5", "AvgC<2.5"]
    df = df.dropna(subset=[c for c in need if c in df.columns])
    df["season"] = season
    return df


def match_probs(row: pd.Series) -> tuple[np.ndarray, float]:
    """(de-vigged 1X2, de-vigged P(over 2.5)) from the closing market average."""
    p1x2 = devig([row["AvgCH"], row["AvgCD"], row["AvgCA"]], "shin")
    p_over = float(devig([row["AvgC>2.5"], row["AvgC<2.5"]], "shin")[0])
    return p1x2, p_over


MAX_GOALS = 10


def run_method(df: pd.DataFrame, *, use_ou: bool, rho: float) -> tuple[pd.DataFrame, float]:
    """Score one derivation over one season.

    Returns two clean-sheet prediction rows per match (one per side) and the
    total log-likelihood the same score matrices assign to the realised
    full-time scorelines. Both come from a single inversion pass because they
    must describe the *same* matrix: scoring a clean-sheet number against one
    matrix and a likelihood against another would make the two objectives
    incomparable, which is the whole question here.
    """
    out: list[dict[str, object]] = []
    loglik = 0.0
    for _, r in df.iterrows():
        p1x2, p_over = match_probs(r)
        try:
            inv = invert_match_odds(
                float(p1x2[0]), float(p1x2[1]), float(p1x2[2]),
                p_over if use_ou else None, rho=rho,
            )
        except (ValueError, RuntimeError):
            continue
        mat = score_matrix(inv.rates, MAX_GOALS)
        cs_h, cs_a = clean_sheet_probs(mat)
        out.append({"season": r["season"], "side": "home",
                    "pred": cs_h, "real": int(r["FTAG"] == 0)})
        out.append({"season": r["season"], "side": "away",
                    "pred": cs_a, "real": int(r["FTHG"] == 0)})
        h, a = int(r["FTHG"]), int(r["FTAG"])
        if h <= MAX_GOALS and a <= MAX_GOALS:
            loglik += float(np.log(max(mat[h, a], 1e-12)))
    return pd.DataFrame(out), loglik


def cs_predictions(df: pd.DataFrame, *, use_ou: bool, rho: float) -> pd.DataFrame:
    """Clean-sheet predictions only; see :func:`run_method`."""
    return run_method(df, use_ou=use_ou, rho=rho)[0]


def brier(p: pd.DataFrame) -> float:
    return float(((p["pred"] - p["real"]) ** 2).mean())


def rho_profile(fit_df: pd.DataFrame) -> pd.DataFrame:
    """Both objectives at every grid point, on the fit season.

    One row per rho: the clean-sheet Brier and the scoreline log-likelihood of
    the inversion re-solved at that rho. Written out as ``cs_rho_grid.csv`` so
    the flatness claim in the write-up can be checked rather than believed.
    """
    rows = []
    for rho in RHO_GRID:
        preds, ll = run_method(fit_df, use_ou=True, rho=rho)
        rows.append({
            "rho": rho,
            "n_pred": len(preds),
            "brier": round(brier(preds), 6),
            "loglik": round(ll, 4),
            "mean_pred": round(float(preds["pred"].mean()), 5),
        })
    return pd.DataFrame(rows)


def _argbest(grid: pd.DataFrame, column: str, *, maximise: bool) -> float:
    idx = grid[column].idxmax() if maximise else grid[column].idxmin()
    return float(grid.loc[idx, "rho"])


def calibration(preds: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(preds["pred"], edges) - 1, 0, bins - 1)
    rows = []
    for b in range(bins):
        sel = preds[idx == b]
        if sel.empty:
            continue
        rows.append({
            "bin_lo": round(edges[b], 2), "bin_hi": round(edges[b + 1], 2),
            "n": len(sel),
            "mean_pred": round(float(sel["pred"].mean()), 4),
            "realized_rate": round(float(sel["real"].mean()), 4),
        })
    return pd.DataFrame(rows)


def main() -> int:
    frames = {s: load_season(s) for s in [FIT_SEASON, *EVAL_SEASONS]}
    for s, f in frames.items():
        print(f"  {s}: {len(f)} matches with closing consensus + result")

    grid = rho_profile(frames[FIT_SEASON])
    rho_brier = _argbest(grid, "brier", maximise=False)
    rho_mle = _argbest(grid, "loglik", maximise=True)
    b_span = float(grid["brier"].max() - grid["brier"].min())
    ll_span = float(grid["loglik"].max() - grid["loglik"].min())
    ll_at_zero = float(grid.loc[grid["rho"] == 0.0, "loglik"].iloc[0])
    b_at_zero = float(grid.loc[grid["rho"] == 0.0, "brier"].iloc[0])

    print(f"\n  rho profile on {FIT_SEASON} "
          f"({RHO_GRID[0]:+.2f} .. {RHO_GRID[-1]:+.2f}, {len(RHO_GRID)} points):")
    for _, r in grid.iterrows():
        marks = []
        if r["rho"] == rho_brier:
            marks.append("min Brier")
        if r["rho"] == rho_mle:
            marks.append("max loglik")
        if r["rho"] == 0.0:
            marks.append("independent")
        tag = ("  <-- " + ", ".join(marks)) if marks else ""
        print(f"    rho={r['rho']:+.2f}  brier={r['brier']:.6f}  "
              f"loglik={r['loglik']:+.3f}{tag}")
    print(f"\n  Brier span across the whole grid : {b_span:.6f} "
          f"(vs {b_at_zero:.6f} at rho=0)")
    print(f"  loglik span across the whole grid: {ll_span:.3f} "
          f"(vs {ll_at_zero:.3f} at rho=0); "
          f"LR vs rho=0 at the MLE = {2 * (grid['loglik'].max() - ll_at_zero):.3f} "
          "on 1 df")
    interior = RHO_GRID[0] < rho_mle < RHO_GRID[-1]
    print(f"  MLE rho={rho_mle:+.2f} is "
          f"{'interior' if interior else 'ON THE GRID BOUNDARY'}; "
          f"min-Brier rho={rho_brier:+.2f}")

    methods = {
        "poisson_h2h": {"use_ou": False, "rho": 0.0},
        "poisson_h2h_ou": {"use_ou": True, "rho": 0.0},
        f"dc_mle_rho={rho_mle:+.2f}": {"use_ou": True, "rho": rho_mle},
    }
    if rho_brier != rho_mle:
        methods[f"dc_brier_rho={rho_brier:+.2f}"] = {"use_ou": True, "rho": rho_brier}

    brier_rows, calib_frames = [], []
    all_seasons = [FIT_SEASON, *EVAL_SEASONS]
    for name, kw in methods.items():
        pooled_eval, pooled_ll = [], 0.0
        for s in all_seasons:
            preds, ll = run_method(frames[s], **kw)
            base = float(preds["real"].mean())
            brier_rows.append({
                "method": name, "season": s,
                "in_sample": s == FIT_SEASON and name.startswith("dc_"),
                "n": len(preds),
                "brier": round(brier(preds), 5),
                "brier_base_rate": round(float(((base - preds["real"]) ** 2).mean()), 5),
                "mean_pred": round(float(preds["pred"].mean()), 4),
                "realized_rate": round(base, 4),
                "scoreline_loglik": round(ll, 3),
            })
            if s in EVAL_SEASONS:
                pooled_eval.append(preds)
                pooled_ll += ll
        pooled = pd.concat(pooled_eval, ignore_index=True)
        brier_rows.append({
            "method": name, "season": "2023-25 pooled", "in_sample": False,
            "n": len(pooled),
            "brier": round(brier(pooled), 5),
            "brier_base_rate": round(
                float(((pooled["real"].mean() - pooled["real"]) ** 2).mean()), 5),
            "mean_pred": round(float(pooled["pred"].mean()), 4),
            "realized_rate": round(float(pooled["real"].mean()), 4),
            "scoreline_loglik": round(pooled_ll, 3),
        })
        cal = calibration(pooled)
        cal.insert(0, "method", name)
        calib_frames.append(cal)

    brier_df = pd.DataFrame(brier_rows)
    calib_df = pd.concat(calib_frames, ignore_index=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    brier_df.to_csv(OUT_DIR / "cs_brier.csv", index=False)
    calib_df.to_csv(OUT_DIR / "cs_calibration.csv", index=False)
    grid.to_csv(OUT_DIR / "cs_rho_grid.csv", index=False)

    print("\n  Brier by method x season (base-rate Brier in brackets):")
    for _, r in brier_df.iterrows():
        tag = " [in-sample fit]" if r["in_sample"] else ""
        print(f"    {r['method']:<20} {r['season']:<14} n={r['n']:>5} "
              f"brier={r['brier']:.5f} ({r['brier_base_rate']:.5f}) "
              f"pred={r['mean_pred']:.4f} real={r['realized_rate']:.4f} "
              f"ll={r['scoreline_loglik']:+.1f}{tag}")
    print(f"\n  wrote {OUT_DIR / 'cs_brier.csv'}, "
          f"{OUT_DIR / 'cs_calibration.csv'} and {OUT_DIR / 'cs_rho_grid.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
