"""Walk-forward scoring of every projection source against realised points.

This is where the weights come from. The rule the whole module is built around:
a source is only allowed to influence a decision after it has been scored on
gameweeks it could not see when it made the projection.

The loop, per held-out gameweek
-------------------------------
1. Derive the deadline as ``min(kickoff) - 90 minutes``. ``dim_event`` only
   holds 2026-27, so historical deadlines have to be derived; 90 minutes is
   ``deadlines.offset_before_first_kickoff_minutes`` from the verified rule
   registry, not a guess.
2. Open a ``Snapshot`` at that instant. Every source reads through it, so none
   can see a price, a flag, a lineup or a result from after the deadline.
3. Refit. Dixon-Coles is refit every gameweek because it is cheap. The minutes
   GBM and the per-90 rate table are refit every ``refit_every`` gameweeks
   because building their training set is a 60-second point-in-time scan of
   113k rows, and using a *staler* model is never leakage -- it only makes the
   internal source look worse, which biases the comparison against us.
4. Score against realised ``total_points``, summed per player per gameweek so a
   double gameweek is one observation with two matches in it.

What is not measured, and why
-----------------------------
FPL Form publishes only the current gameweek range; there is no archive of what
it said in January. So it cannot appear in this backtest at all, and it gets no
weight -- see ``weights.UnearnedWeightError``. That is not a defect in the
harness, it is the honest state of the evidence, and it changes the moment GW1
of 2026-27 is played, because our own ingest has been archiving their
projections with the fetch instant since 2026-08-19.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from fpl_edge.models.ensemble import sources
from fpl_edge.models.ensemble.weights import EnsembleWeights, earn_weights
from fpl_edge.models.minutes import GBMMinutesModel, TrainingSetBuilder
from fpl_edge.models.points.model import DecomposedPointsModel
from fpl_edge.models.points.shares import estimate_rates
from fpl_edge.models.team_goals import DixonColesModel
from fpl_edge.store import Warehouse

#: Verified rule ``deadlines.offset_before_first_kickoff_minutes``.
DEADLINE_OFFSET = dt.timedelta(minutes=90)


@dataclass
class BacktestResult:
    """Everything the fit saw, kept so the weights can be re-derived."""

    panel: pd.DataFrame
    samples: dict[str, np.ndarray]
    providers: list[str]
    season: str
    gws: list[int]
    coverage: pd.DataFrame
    notes: list[str] = field(default_factory=list)

    @property
    def holdout(self) -> str:
        return f"{self.season} GW{min(self.gws)}-GW{max(self.gws)}, {len(self.panel):,} obs"

    def fit(self, *, method: str = "stacking",
            unearned: tuple[str, ...] = ()) -> EnsembleWeights:
        return earn_weights(self.panel, self.providers, holdout=self.holdout,
                            samples=self.samples, unearned=unearned, method=method)


def derive_deadline(warehouse: Warehouse, season: str, gw: int) -> dt.datetime:
    """``min(kickoff) - 90 minutes`` for a gameweek."""
    frame = warehouse.sql(
        "SELECT min(kickoff_utc) AS ko FROM fact_fixture WHERE season = ? AND gw = ?",
        [season, gw],
    )
    kickoff = frame.iloc[0]["ko"]
    if pd.isna(kickoff):
        raise KeyError(f"no kickoff known for {season} GW{gw}")
    return kickoff.to_pydatetime() - DEADLINE_OFFSET


def realised_points(warehouse: Warehouse, season: str, gws: list[int]) -> pd.DataFrame:
    """Points actually scored, one row per ``(code, gw)``.

    Read unfiltered on purpose: this is the *label*. Point-in-time discipline
    applies to what the model saw, not to the answer it is being marked against.
    Summed across fixtures so a double gameweek is one observation.
    """
    placeholders = ", ".join("?" for _ in gws)
    return warehouse.sql(
        f"""
        SELECT code, gw, sum(total_points) AS actual, sum(minutes) AS minutes
        FROM (
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY season, code, fixture_id ORDER BY as_of DESC) rn
                FROM fact_player_fixture WHERE season = ? AND gw IN ({placeholders})
            ) WHERE rn = 1
        ) GROUP BY code, gw
        """,
        [season, *gws],
    )


def walk_forward(
    warehouse: Warehouse,
    season: str,
    gws: list[int],
    *,
    history: list[str],
    refit_every: int = 5,
    n_sims: int = 1500,
    market_at_kickoff: bool = False,
    seed: int = 20260821,
    verbose: bool = True,
) -> BacktestResult:
    """Score internal / market / ppg over ``gws`` and return the fitted panel."""
    frames: list[pd.DataFrame] = []
    draws: dict[str, list[pd.DataFrame]] = {"internal": [], "market": []}
    notes: list[str] = []
    minutes_model = None
    rates = None
    market_coverage: list[float] = []

    for i, gw in enumerate(sorted(gws)):
        deadline = derive_deadline(warehouse, season, gw)
        snapshot = warehouse.snapshot_at(deadline)

        if i % refit_every == 0:
            builder = TrainingSetBuilder(snapshot_at=warehouse.snapshot_at,
                                         catalog=snapshot)
            training = builder.build(history)
            minutes_model = GBMMinutesModel().fit(training)
            rates = estimate_rates(snapshot, history)
            if verbose:
                print(f"  GW{gw}: refit minutes on {len(training.frame):,} rows, "
                      f"rates for {len(rates.frame)} players")

        goals = DixonColesModel()
        goals.fit(snapshot, season)

        internal = DecomposedPointsModel(goal_model=goals, minutes_model=minutes_model,
                                         rates=rates)
        frame, sample = sources.simulate_source(
            "internal", internal, snapshot, season, gw, n_sims=n_sims, seed=seed + gw
        )
        frames.append(frame)
        draws["internal"].append(sample.assign(gw=gw))

        try:
            builder_fn = (sources.market_goal_model_at_kickoff if market_at_kickoff
                          else sources.market_goal_model)
            market_goals, _ = builder_fn(warehouse, season, deadline)
            market_goals.set_rho(getattr(goals, "rho", 0.0))
            market = DecomposedPointsModel(goal_model=market_goals,
                                           minutes_model=minutes_model, rates=rates)
            m_frame, m_sample = sources.simulate_source(
                "market", market, snapshot, season, gw, n_sims=n_sims, seed=seed + gw
            )
            frames.append(m_frame)
            draws["market"].append(m_sample.assign(gw=gw))
            market_coverage.append(float(market_goals.last_coverage))
        except (sources.NoOddsCoverageError, ValueError, KeyError) as exc:
            notes.append(f"GW{gw}: market source unavailable ({type(exc).__name__}: {exc})")

        frames.append(sources.ppg_source(snapshot, season, gw, history=history))
        frames.append(sources.form_source(snapshot, season, gw))
        frames.append(sources.xstat_source(snapshot, season, gw, history=history))
        if verbose:
            print(f"  GW{gw}: deadline {deadline:%Y-%m-%d %H:%MZ}, "
                  f"{len(frame)} players, market coverage "
                  f"{market_coverage[-1]:.1%}" if market_coverage else "")

    long = pd.concat(frames, ignore_index=True)
    wide = long.pivot_table(index=["season", "gw", "code"], columns="provider",
                            values="xp", aggfunc="first")
    wide.columns.name = None
    wide = wide.reset_index()

    actual = realised_points(warehouse, season, sorted(gws))
    panel = wide.merge(actual, on=["code", "gw"], how="inner")
    providers = [p for p in ("internal", "market", "ppg", "form", "xstat")
                 if p in panel.columns]
    panel = panel.dropna(subset=["actual"]).reset_index(drop=True)

    samples: dict[str, np.ndarray] = {}
    for name, parts in draws.items():
        if not parts:
            continue
        stacked = pd.concat(parts).reset_index()
        stacked = stacked.set_index(["code", "gw"])
        idx = pd.MultiIndex.from_frame(panel[["code", "gw"]])
        aligned = stacked.reindex(idx)
        samples[name] = aligned.to_numpy(dtype=float)

    coverage = pd.DataFrame([
        {"provider": p, "rows": int(panel[p].notna().sum()),
         "coverage": float(panel[p].notna().mean())}
        for p in providers
    ])
    if market_coverage:
        notes.append(
            f"market fixture coverage {np.mean(market_coverage):.1%} "
            f"(min {min(market_coverage):.1%})"
        )
    return BacktestResult(panel, samples, providers, season, sorted(gws),
                          coverage, notes)
