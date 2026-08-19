"""Per-player scoring rates, shrunk toward a position prior.

The team goal model says how many goals a team scores. This module says which
player gets them. Both halves are needed and neither is sufficient: a great
attacker in a poor team and a poor attacker in a great team can carry the same
raw xG, and only the product predicts FPL points.

Estimation is empirical Bayes. A player's per-90 rate is shrunk toward the mean
for their position by a pseudo-count ``k``, so a striker with 200 minutes does
not out-rank one with 3,000 on the strength of a hot fortnight::

    rate = (player_events + k * prior_rate) / (player_90s + k)

Two deliberate choices worth stating:

* **xG here includes penalties.** FPL pays for penalty goals exactly as for open
  play, so total xG is the correct quantity for predicting points. The cost is
  that a player losing penalty duty keeps an inflated rate until the history
  rolls off, and the archive has no penalties-taken column to separate them.
  Recorded in docs/known_weaknesses.md rather than silently accepted.
* **Rates are per 90 of actual playing time**, so they compose cleanly with the
  minutes model instead of double-counting rotation risk.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from fpl_edge.rules import rules
from fpl_edge.store import Snapshot
from fpl_edge.types import Position

#: Pseudo-counts (in 90s) for shrinkage toward the positional prior. Chosen to
#: be roughly a third of a season: enough to tame small samples without erasing
#: a genuinely elite rate over a full campaign.
SHRINKAGE_90S = 12.0

#: Weight applied to each season relative to the most recent one.
SEASON_DECAY = 0.55

RATE_COLUMNS = (
    "xg90", "xa90", "save90", "dc_rate", "yellow90", "concede90", "bps90",
)


@dataclass(frozen=True)
class PlayerRates:
    """Per-90 rates by player code, with the priors used to shrink them."""

    frame: pd.DataFrame          # indexed by code, columns RATE_COLUMNS + minutes
    position_priors: pd.DataFrame
    seasons_used: tuple[str, ...]

    def for_codes(self, codes: np.ndarray, positions: np.ndarray) -> pd.DataFrame:
        """Rates for the given codes, falling back to the position prior.

        A player with no history at all -- a promoted-club regular, a summer
        signing from abroad -- gets the positional prior rather than a zero.
        Returning zero would silently make every new player unpickable, which is
        a strong claim the data does not support.
        """
        out = self.frame.reindex(codes)
        missing = out["xg90"].isna()
        if missing.any():
            prior = self.position_priors.reindex(positions[missing.to_numpy()])
            for col in RATE_COLUMNS:
                out.loc[missing.to_numpy(), col] = prior[col].to_numpy()
            out.loc[missing.to_numpy(), "minutes"] = 0.0
        return out.fillna(0.0)


def _weighted_totals(df: pd.DataFrame, seasons: list[str]) -> pd.DataFrame:
    """Sum event counts with exponential decay on older seasons.

    ``df`` must carry a ``position`` column: the defensive-contribution
    threshold differs by position (10 for defenders, 12 for everyone else), and
    applying the defender threshold uniformly roughly doubles the apparent hit
    rate for midfielders and quadruples it for forwards.
    """
    order = {s: i for i, s in enumerate(sorted(seasons, reverse=True))}
    w = df["season"].map(order).map(lambda i: SEASON_DECAY ** i)
    cols = ["minutes", "expected_goals", "expected_assists", "saves",
            "yellow_cards", "goals_conceded", "bps", "defensive_contribution"]
    weighted = df[cols].multiply(w, axis=0)
    weighted["code"] = df["code"].to_numpy()
    thresholds = np.where(
        df["position"].to_numpy() == int(Position.DEF),
        rules().get("defensive_contribution.def_threshold"),
        rules().get("defensive_contribution.mid_fwd_threshold"),
    )
    eligible = df["position"].to_numpy() != int(Position.GKP)
    weighted["dc_hits"] = (
        (df["defensive_contribution"].fillna(0).to_numpy() >= thresholds) & eligible
    ).astype(float) * w.to_numpy()
    weighted["appearances"] = w.to_numpy() * (df["minutes"].to_numpy() > 0)
    return weighted.groupby("code").sum()


def estimate_rates(snapshot: Snapshot, seasons: list[str]) -> PlayerRates:
    """Estimate per-90 rates from every result visible at this snapshot.

    Reads only through the snapshot, so a backtest cannot pick up a rate that
    incorporates matches played after the deadline being decided.
    """
    frames = []
    for season in seasons:
        df = snapshot.results_before(season)
        if not df.empty:
            frames.append(df.assign(season=season))
    if not frames:
        raise ValueError(f"no visible results in seasons {seasons} at {snapshot.as_of}")

    hist = pd.concat(frames, ignore_index=True)
    pos_by_code = _positions_for(snapshot, seasons, hist["code"].unique())
    hist["position"] = hist["code"].map(pos_by_code).fillna(int(Position.MID)).astype(int)
    totals = _weighted_totals(hist, seasons)
    nineties = (totals["minutes"] / 90.0).clip(lower=0.0)

    # Positional priors from the pooled totals.
    totals["position"] = pd.Series(
        {c: pos_by_code.get(int(c), int(Position.MID)) for c in totals.index}
    )

    raw = pd.DataFrame(index=totals.index)
    raw["minutes"] = totals["minutes"]
    raw["_90s"] = nineties
    raw["position"] = totals["position"]

    numerators = {
        "xg90": totals["expected_goals"],
        "xa90": totals["expected_assists"],
        "save90": totals["saves"],
        "yellow90": totals["yellow_cards"],
        "concede90": totals["goals_conceded"],
        "bps90": totals["bps"],
    }
    priors = {}
    for name, num in numerators.items():
        pooled = raw.assign(_n=num).groupby("position").apply(
            lambda g: g["_n"].sum() / max(g["_90s"].sum(), 1e-9), include_groups=False
        )
        priors[name] = pooled
        prior_by_row = raw["position"].map(pooled).fillna(0.0)
        raw[name] = (num + SHRINKAGE_90S * prior_by_row) / (nineties + SHRINKAGE_90S)

    # Defensive-contribution hit rate per appearance, same shrinkage logic.
    apps = totals["appearances"].clip(lower=0.0)
    dc_prior = raw.assign(_h=totals["dc_hits"], _a=apps).groupby("position").apply(
        lambda g: g["_h"].sum() / max(g["_a"].sum(), 1e-9), include_groups=False
    )
    priors["dc_rate"] = dc_prior
    raw["dc_rate"] = (
        (totals["dc_hits"] + SHRINKAGE_90S * raw["position"].map(dc_prior).fillna(0.0))
        / (apps + SHRINKAGE_90S)
    ).clip(0.0, 1.0)

    prior_frame = pd.DataFrame(priors)
    return PlayerRates(
        frame=raw[["minutes", *RATE_COLUMNS]],
        position_priors=prior_frame,
        seasons_used=tuple(seasons),
    )


def _positions_for(snapshot: Snapshot, seasons: list[str], codes: np.ndarray) -> dict[int, int]:
    """Most recent known position per code across the given seasons."""
    out: dict[int, int] = {}
    for season in seasons:
        dim = snapshot.table("dim_player", where="season = ?", params=[season])
        for code, pos in zip(dim["code"], dim["position"]):
            out[int(code)] = int(pos)
    return {int(c): out.get(int(c), int(Position.MID)) for c in codes}
