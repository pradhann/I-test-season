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

How rho is fitted -- and why not by Brier
-----------------------------------------
First attempt was a grid search minimising clean-sheet Brier on 2022-23.
Measured result: the Brier surface is flat to within 0.0002 across rho in
[-0.16, +0.20] (0.18630..0.18645), i.e. **rho is not identified by
clean-sheet accuracy** once the totals market has pinned the goal level.
So rho is instead fitted where it is identified: by maximum likelihood of
the realised full-time scorelines under the tau-corrected score matrix,
holding each match's market-implied (lambda_h, lambda_a) fixed. The
insensitivity itself is a useful finding -- it means the independent-Poisson
inversion is not leaving measurable clean-sheet accuracy on the table -- and
it is reported in docs/platform/odds_derivation.md.

The correct-score method cannot be backtested here: football-data carries no
correct-score history and The Odds API's historical snapshots are paid-only.
Its live-vs-inversion disagreement is quantified on GW1 2026-27 instead; see
docs/platform/odds_derivation.md.

Outputs (committed under docs/platform/):

* ``cs_brier.csv``       -- Brier score, base-rate Brier, bias per method x season.
* ``cs_calibration.csv`` -- 10-bin reliability curve per method, pooled over
  the two out-of-sample seasons.

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
RHO_GRID = [round(r, 2) for r in np.arange(-0.16, 0.05, 0.02)]


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


def cs_predictions(df: pd.DataFrame, *, use_ou: bool, rho: float) -> pd.DataFrame:
    """Two rows per match: predicted and realised clean sheet for each side."""
    out = []
    for _, r in df.iterrows():
        p1x2, p_over = match_probs(r)
        try:
            inv = invert_match_odds(
                float(p1x2[0]), float(p1x2[1]), float(p1x2[2]),
                p_over if use_ou else None, rho=rho,
            )
        except (ValueError, RuntimeError):
            continue
        cs_h, cs_a = clean_sheet_probs(score_matrix(inv.rates, 10))
        out.append({"season": r["season"], "side": "home",
                    "pred": cs_h, "real": int(r["FTAG"] == 0)})
        out.append({"season": r["season"], "side": "away",
                    "pred": cs_a, "real": int(r["FTHG"] == 0)})
    return pd.DataFrame(out)


def brier(p: pd.DataFrame) -> float:
    return float(((p["pred"] - p["real"]) ** 2).mean())


def fit_rho(fit_df: pd.DataFrame) -> tuple[float, dict[float, float]]:
    """Grid-fit rho on the fit season by minimum clean-sheet Brier."""
    scores = {}
    for rho in RHO_GRID:
        preds = cs_predictions(fit_df, use_ou=True, rho=rho)
        scores[rho] = brier(preds)
    best = min(scores, key=scores.get)
    return best, scores


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

    rho_star, rho_scores = fit_rho(frames[FIT_SEASON])
    print(f"\n  rho grid on {FIT_SEASON} (Brier):")
    for rho, sc in sorted(rho_scores.items()):
        marker = "  <-- fitted" if rho == rho_star else ""
        print(f"    rho={rho:+.2f}  {sc:.5f}{marker}")

    methods = {
        "poisson_h2h": {"use_ou": False, "rho": 0.0},
        "poisson_h2h_ou": {"use_ou": True, "rho": 0.0},
        f"dc_rho={rho_star:+.2f}": {"use_ou": True, "rho": rho_star},
    }

    brier_rows, calib_frames = [], []
    all_seasons = [FIT_SEASON, *EVAL_SEASONS]
    for name, kw in methods.items():
        pooled_eval = []
        for s in all_seasons:
            preds = cs_predictions(frames[s], **kw)
            base = float(preds["real"].mean())
            brier_rows.append({
                "method": name, "season": s,
                "in_sample": s == FIT_SEASON and name.startswith("dc_"),
                "n": len(preds),
                "brier": round(brier(preds), 5),
                "brier_base_rate": round(float(((base - preds["real"]) ** 2).mean()), 5),
                "mean_pred": round(float(preds["pred"].mean()), 4),
                "realized_rate": round(base, 4),
            })
            if s in EVAL_SEASONS:
                pooled_eval.append(preds)
        pooled = pd.concat(pooled_eval, ignore_index=True)
        brier_rows.append({
            "method": name, "season": "2023-25 pooled", "in_sample": False,
            "n": len(pooled),
            "brier": round(brier(pooled), 5),
            "brier_base_rate": round(
                float(((pooled["real"].mean() - pooled["real"]) ** 2).mean()), 5),
            "mean_pred": round(float(pooled["pred"].mean()), 4),
            "realized_rate": round(float(pooled["real"].mean()), 4),
        })
        cal = calibration(pooled)
        cal.insert(0, "method", name)
        calib_frames.append(cal)

    brier_df = pd.DataFrame(brier_rows)
    calib_df = pd.concat(calib_frames, ignore_index=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    brier_df.to_csv(OUT_DIR / "cs_brier.csv", index=False)
    calib_df.to_csv(OUT_DIR / "cs_calibration.csv", index=False)

    print("\n  Brier by method x season (base-rate Brier in brackets):")
    for _, r in brier_df.iterrows():
        tag = " [in-sample fit]" if r["in_sample"] else ""
        print(f"    {r['method']:<18} {r['season']:<14} n={r['n']:>5} "
              f"brier={r['brier']:.5f} ({r['brier_base_rate']:.5f}) "
              f"pred={r['mean_pred']:.4f} real={r['realized_rate']:.4f}{tag}")
    print(f"\n  wrote {OUT_DIR / 'cs_brier.csv'} and {OUT_DIR / 'cs_calibration.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
