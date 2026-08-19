"""The rest-of-season Monte Carlo engine.

Structure of a run:

1. For every remaining gameweek, draw ``n_sims`` correlated samples of every
   player's points from the :class:`~fpl_edge.models.contracts.PointsModel`.
   These draws are the *only* source of outcome randomness in the system.
2. Sample a field of ``n_rivals`` explicit squads per gameweek from the
   :class:`~fpl_edge.models.contracts.OwnershipModel`, and score every one of
   them against those same draws, accumulating season totals.
3. Score the candidate squad against the same draws and read off its rank in
   each simulation.

Step 2 is done once and cached. The field does not depend on my decisions, so
every candidate is ranked against an identical field on identical draws. That
is not just a speed optimisation: it makes every comparison a *paired* one, and
the paired standard error of a difference in P(top 10k) is roughly an order of
magnitude smaller than the unpaired one. Without it, resolving the differences
that actually matter -- a few tenths of a percentage point -- would need
millions of simulations rather than thousands.

Points are cached as ``int8`` and minutes as ``int8``. FPL points are integers
and no player has ever scored more than about 40 in a gameweek, so this is
lossless and keeps a 38-gameweek, 4,000-simulation cache under 200 MB.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from dataclasses import field as _field
from typing import Any

import numpy as np

from fpl_edge.models.contracts import OwnershipModel, PointsModel, PointsSample
from fpl_edge.sim.field import FieldConfig, FieldModel, FieldSquads
from fpl_edge.sim.rank import Counterfactual, RankDistribution, rank_from_scores
from fpl_edge.sim.squad import PlayerUniverse, Squad, score_squads
from fpl_edge.types import GwId, Season

#: Largest single-gameweek score we are willing to store in an int8 cache.
_POINTS_CACHE_LIMIT = 120


@dataclass(frozen=True)
class SquadPlan:
    """A candidate decision: which squad is fielded in which gameweek.

    A transfer plan is expressed as a different :class:`Squad` from the
    gameweek the transfer takes effect, plus the points hit it costs. That is
    enough to express everything the optimizer needs to compare without the
    simulator having to know what a transfer is.
    """

    base: Squad
    overrides: dict[int, Squad] = _field(default_factory=dict)
    hits: dict[int, int] = _field(default_factory=dict)
    chips: dict[int, str] = _field(default_factory=dict)
    label: str = ""

    def squad_for(self, gw: int) -> Squad:
        chosen = self.base
        for g in sorted(self.overrides):
            if g <= gw:
                chosen = self.overrides[g]
        return chosen

    def hit_for(self, gw: int) -> int:
        return int(self.hits.get(gw, 0))

    def captain_multiplier(self, gw: int) -> float:
        return 3.0 if self.chips.get(gw) == "3xc" else 2.0

    def with_captain(self, gw: int, captain: int, *, label: str = "",
                     revert_gw: int | None = None) -> SquadPlan:
        """A copy that captains a different player.

        ``revert_gw`` restores the original armband from that gameweek, which is
        what makes a *single-gameweek* captaincy decision expressible. Without
        it, changing the captain in GW1 changes it for the whole season, and any
        measured effect is 38 decisions rather than one.
        """
        ov = dict(self.overrides)
        original = self.squad_for(gw)
        ov[gw] = original.with_captain(captain)
        if revert_gw is not None:
            ov[revert_gw] = original
        return SquadPlan(base=self.base, overrides=ov, hits=dict(self.hits),
                         chips=dict(self.chips), label=label or self.label)

    def relabel(self, label: str) -> SquadPlan:
        return SquadPlan(self.base, dict(self.overrides), dict(self.hits),
                         dict(self.chips), label)


@dataclass
class SeasonSimulator:
    """Owns the point draws, the sampled field, and the ranking."""

    universe: PlayerUniverse
    points_model: PointsModel
    ownership_model: OwnershipModel
    snapshot: Any                 # Snapshot, or None for warehouse-free tests
    season: Season
    gws: tuple[int, ...]
    n_sims: int = 4_000
    seed: int = 20_260_821
    field_config: FieldConfig = _field(default_factory=FieldConfig)

    def __post_init__(self) -> None:
        self.field = FieldModel(self.universe, self.field_config)
        self._points: dict[int, np.ndarray] = {}
        self._minutes: dict[int, np.ndarray | None] = {}
        self._squads: dict[int, FieldSquads] = {}
        self._xp: dict[int, np.ndarray] = {}
        self.rival_totals: np.ndarray | None = None
        self.prepare_seconds: float = 0.0
        self.diagnostics: dict[str, float] = {}

    # -- preparation ---------------------------------------------------------

    def prepare(self, *, verbose: bool = False) -> SeasonSimulator:
        """Draw every gameweek and score the whole field. Idempotent."""
        if self.rival_totals is not None:
            return self
        t0 = time.perf_counter()
        m = self.field_config.n_rivals
        totals = np.zeros((m, self.n_sims), dtype=np.float32)
        for k, gw in enumerate(self.gws):
            sample: PointsSample = self.points_model.simulate(
                self.snapshot, self.season, GwId(gw), n_sims=self.n_sims, seed=self.seed
            )
            pts, mins = self._cache_sample(gw, sample)
            xp = pts.mean(axis=1)
            self._xp[gw] = xp
            own = self._forecast(GwId(gw), xp)
            eo = _align_ownership(own, self.universe)
            cap = _align(own, "captaincy_share", self.universe)
            squads = self.field.sample_squads(eo, cap, xp, gw_offset=k)
            self._squads[gw] = squads
            totals += self.field.score(squads, pts.astype(np.float32), mins)
            if verbose:
                print(f"  GW{gw:>2}  field mean {totals.mean() / (k + 1):7.2f}", flush=True)
        self.rival_totals = totals
        self.prepare_seconds = time.perf_counter() - t0
        self._record_diagnostics()
        return self

    def _forecast(self, gw: GwId, xp: np.ndarray):
        """Ask the ownership model for this gameweek's forecast.

        ``OwnershipModel.forecast(snapshot, season, gw)`` deliberately takes no
        expected points -- ownership is a forecast of the field, not of the
        game. Captaincy share is not forecastable without them, though, so a
        model that wants them advertises a ``set_expected_points`` hook and they
        are handed over out of band rather than by widening the shared contract
        from inside the simulator.
        """
        setter = getattr(self.ownership_model, "set_expected_points", None)
        if setter is not None:
            setter(xp)
        return self.ownership_model.forecast(self.snapshot, self.season, gw)

    def _cache_sample(self, gw: int, sample: PointsSample) -> tuple[np.ndarray, np.ndarray | None]:
        if not np.array_equal(sample.codes, self.universe.codes):
            raise ValueError(
                "PointsSample codes do not match the universe. The points model and the "
                "simulator must be built from the same Snapshot."
            )
        pts = np.rint(sample.points)
        if np.abs(pts - sample.points).max() > 1e-6:
            raise ValueError("FPL points must be integers; got fractional values")
        if np.abs(pts).max() > _POINTS_CACHE_LIMIT:
            raise ValueError(f"points outside +-{_POINTS_CACHE_LIMIT}, refusing to cache as int8")
        self._points[gw] = pts.astype(np.int8)
        mins = sample.minutes
        cached: np.ndarray | None = (
            None if mins is None else np.clip(mins, 0, 127).astype(np.int8)
        )
        self._minutes[gw] = cached
        return self._points[gw], cached

    def _record_diagnostics(self) -> None:
        assert self.rival_totals is not None
        t = self.rival_totals
        self.diagnostics = {
            "n_sims": float(self.n_sims),
            "n_rivals": float(self.field_config.n_rivals),
            "n_gws": float(len(self.gws)),
            "field_mean_season": float(t.mean()),
            "field_sd_across_managers": float(t.mean(axis=1).std()),
            "field_sd_across_sims": float(t.mean(axis=0).std()),
            "field_mean_per_gw": float(t.mean() / len(self.gws)),
            "prepare_seconds": self.prepare_seconds,
        }

    # -- evaluation ----------------------------------------------------------

    def score_plan(self, plan: SquadPlan) -> np.ndarray:
        """``(n_sims,)`` season points for a candidate, autosubs and hits included."""
        if self.rival_totals is None:
            self.prepare()
        total = np.zeros(self.n_sims, dtype=np.float64)
        for gw in self.gws:
            sq = plan.squad_for(gw)
            slots = np.array(sq.all_indices, dtype=np.int64)
            pts = self._points[gw][slots][None, :, :].astype(np.float32)
            mins = self._minutes[gw]
            mins = None if mins is None else mins[slots][None, :, :]
            pos = self.universe.position[slots][None, :]
            cap = np.array([sq.all_indices.index(sq.captain)])
            vc = np.array([sq.all_indices.index(sq.vice)])
            total += score_squads(
                pts, mins, pos, cap, vc,
                captain_multiplier=plan.captain_multiplier(gw),
            )[0]
            total -= plan.hit_for(gw)
        return total

    def evaluate(self, plan: SquadPlan, *, label: str | None = None,
                 keep_rivals: bool = False) -> RankDistribution:
        """Full rank distribution for a candidate plan."""
        if self.rival_totals is None:
            self.prepare()
        assert self.rival_totals is not None
        mine = self.score_plan(plan)
        ranks = rank_from_scores(
            mine, self.rival_totals, field_size=self.field_config.field_size
        )
        return RankDistribution(
            ranks=ranks,
            my_scores=mine,
            field_mean_score=self.rival_totals.mean(axis=0).astype(np.float64),
            rival_scores=self.rival_totals if keep_rivals else None,
            field_size=self.field_config.field_size,
            label=label if label is not None else plan.label,
        )

    def compare(self, a: SquadPlan, b: SquadPlan) -> Counterfactual:
        """Paired counterfactual: same draws, same field, two decisions."""
        return Counterfactual(a=self.evaluate(a), b=self.evaluate(b))

    # -- field diagnostics ---------------------------------------------------

    def field_rank_ladder(self, quantiles=(0.5, 0.9, 0.99, 0.999, 0.9999)) -> dict[str, float]:
        """Season score at given field percentiles, averaged over simulations.

        Quantiles are taken *within* each simulation and then averaged, because
        the real-world rank ladder is a within-season object: everyone in a
        given season experiences the same set of match results. Pooling scores
        across simulations first would fold season-to-season variation into the
        ladder and inflate every gap.

        This is what is checked against the published rank-vs-points ladder in
        :mod:`fpl_edge.sim.experiments`.
        """
        if self.rival_totals is None:
            self.prepare()
        assert self.rival_totals is not None
        t = self.rival_totals
        n = self.field_config.field_size
        out = {"mean": float(t.mean()), "sd": float(t.std(axis=0).mean())}
        qs = np.quantile(t, list(quantiles), axis=0).mean(axis=1)
        for q, v in zip(quantiles, qs):
            out[f"rank_{round(n * (1 - q)):,}"] = float(v)
        out["median_minus_mean"] = out["rank_2,948,322"] - out["mean"] if \
            "rank_2,948,322" in out else float("nan")
        return out

    def realised_ownership_error(self, gw: int) -> dict[str, float]:
        """How closely the sampled field reproduces the ownership it was given."""
        own = self._forecast(GwId(gw), self._xp[gw])
        given = _align_ownership(own, self.universe)
        target = self.field.target_ownership(given)
        got = self._squads[gw].ownership_realised(self.universe.n_players)
        cap_t = _align(own, "captaincy_share", self.universe)
        cap_g = self._squads[gw].captain_share(self.universe.n_players)
        out = {
            "max_abs_eo_error": float(np.abs(got - target).max()),
            "mean_abs_eo_error": float(np.abs(got - target).mean()),
            "weighted_abs_eo_error": float((target * np.abs(got - target)).sum() / target.sum()),
            "max_abs_captaincy_error": float(np.abs(cap_g - cap_t).max()),
        }
        out.update(self.field.ownership_renormalisation(given))
        return out


def _align(df, column: str, universe: PlayerUniverse) -> np.ndarray:
    import pandas as pd

    s = pd.Series(df[column].to_numpy(), index=df["code"].to_numpy())
    return s.reindex(universe.codes).fillna(0.0).to_numpy(dtype=np.float64)


#: Column the field sampler wants: the share of managers who OWN each player.
OWNERSHIP_COLUMN = "own_mean"


def _align_ownership(df, universe: PlayerUniverse) -> np.ndarray:
    """The squad-inclusion marginal the field sampler needs.

    ``FieldModel.sample_squads`` draws 15-man squads, so its input must be
    *ownership* -- the share of managers holding the player -- and ownership
    alone sums to 15 across the player set.

    ``eo_overall`` is not that. Effective ownership is the mean multiplier the
    field applies, so it counts a captain twice, drops benched owners, and sums
    to 12 (eleven starters plus one armband). Feeding it to the sampler
    over-states every captaincy magnet: at the 2026-27 GW1 forecast Haaland's
    EO is 1.139, which the sampler clips to 0.999 and reproduces as a field that
    owns him almost universally, against a real forecast ownership of 0.730.
    Every Haaland differential is then priced against a field that does not
    exist.

    So the real ownership column is used when the model supplies one, and
    ``eo_overall`` only as a fallback for models that emit the bare contract --
    which is correct for those, because a model with no ownership column has
    nothing better to offer.
    """
    if OWNERSHIP_COLUMN in getattr(df, "columns", ()):
        return _align(df, OWNERSHIP_COLUMN, universe)
    return _align(df, "eo_overall", universe)


def template_squad(universe: PlayerUniverse, eo: np.ndarray, xp: np.ndarray,
                   *, budget_tenths: int = 1000) -> Squad:
    """The squad the field converges on: highest-owned, budget- and club-feasible.

    Used as the baseline candidate in the divergence experiment, because "what
    happens if I just own what everyone else owns" is the null hypothesis the
    whole engine is arguing against.
    """
    return greedy_squad(universe, eo, xp, budget_tenths=budget_tenths)


def greedy_squad(universe: PlayerUniverse, score: np.ndarray, xp: np.ndarray,
                 *, budget_tenths: int = 1000, forced: tuple[int, ...] = ()) -> Squad:
    """Greedy feasible 15 by ``score``, repaired until it fits the budget.

    Deliberately not an optimizer -- that belongs to the optimizer team. It only
    has to produce realistic, legal squads for the simulator to reason about.
    """
    from fpl_edge.sim.squad import SQUAD_BY_POSITION, pick_best_xi

    need = dict(SQUAD_BY_POSITION)
    chosen: list[int] = []
    club: dict[int, int] = {}
    for f in forced:
        p = int(universe.position[f])
        need[p] -= 1
        chosen.append(int(f))
        club[int(universe.team_code[f])] = club.get(int(universe.team_code[f]), 0) + 1

    order = np.argsort(-score, kind="stable")
    for i in order:
        i = int(i)
        if i in chosen:
            continue
        p = int(universe.position[i])
        if need[p] <= 0:
            continue
        t = int(universe.team_code[i])
        if club.get(t, 0) >= 3:
            continue
        chosen.append(i)
        need[p] -= 1
        club[t] = club.get(t, 0) + 1
        if sum(need.values()) == 0:
            break
    if sum(need.values()) != 0:
        raise ValueError("could not fill the squad")

    # Repair the budget by swapping the worst value-for-money player down.
    for _ in range(400):
        cost = int(universe.price_tenths[np.array(chosen)].sum())
        if cost <= budget_tenths:
            break
        over = cost - budget_tenths
        best: tuple[float, int, int] | None = None
        for out_i in chosen:
            if out_i in forced:
                continue
            p = int(universe.position[out_i])
            for in_i in order:
                in_i = int(in_i)
                if in_i in chosen or int(universe.position[in_i]) != p:
                    continue
                saving = int(universe.price_tenths[out_i] - universe.price_tenths[in_i])
                if saving <= 0:
                    continue
                t = int(universe.team_code[in_i])
                if t != int(universe.team_code[out_i]) and club.get(t, 0) >= 3:
                    continue
                loss = float(score[out_i] - score[in_i])
                key = loss / max(min(saving, over), 1)
                if best is None or key < best[0]:
                    best = (key, out_i, in_i)
                break
        if best is None:
            raise ValueError("cannot repair budget")
        _, out_i, in_i = best
        chosen.remove(out_i)
        chosen.append(in_i)
        club[int(universe.team_code[out_i])] -= 1
        club[int(universe.team_code[in_i])] = club.get(int(universe.team_code[in_i]), 0) + 1

    squad = pick_best_xi(chosen, xp, universe)
    squad.validate(universe)
    return squad
