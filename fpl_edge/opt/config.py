"""Optimizer configuration.

The single most important thing in this module is :class:`ObjectiveMode`. This
project exists because optimising mean expected points is the wrong objective:
FPL is a rank tournament, and the quantity worth maximising is a utility of
final rank, which is a functional of the *joint* distribution of your score and
the field's. Expected points is a strictly-worse surrogate that ignores
ownership, variance and covariance entirely.

So the mode is an explicit, mandatory, logged choice. There is no default that
silently gives you means.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class ObjectiveMode(enum.StrEnum):
    """What the MILP actually maximises.

    ``RANK_UTILITY`` is the objective this engine is for. ``EXPECTED_POINTS``
    exists so the difference between the two can be measured rather than
    asserted -- run both, diff the squads, and the disagreement is the value the
    rank machinery is adding.
    """

    #: Maximise a simulated utility of final rank. Requires a
    #: :class:`~fpl_edge.opt.interfaces.RankUtilityProvider`.
    RANK_UTILITY = "rank_utility"

    #: Maximise discounted expected points net of transfer hits. A surrogate.
    EXPECTED_POINTS = "expected_points"


class SolverBackend(enum.StrEnum):
    HIGHS = "highs"
    CBC = "cbc"


@dataclass(frozen=True, slots=True)
class AutosubWeights:
    """Probability that a bench player's points end up counting.

    Autosubs are genuinely non-linear: whether bench slot 2 comes on depends on
    how many starters blanked *and* on whether the resulting formation is legal.
    Encoding that exactly needs the joint distribution of appearances, which
    lives in the simulator, not in a MILP.

    What the MILP uses instead is a fixed per-slot activation probability. The
    weights are part of the *declared objective* -- :func:`fpl_edge.opt.scoring.
    score_plan` recomputes with exactly these numbers -- so the model and the
    scorer agree by construction, and the approximation is visible in config
    rather than buried in a constraint.

    ``outfield`` must be non-increasing: the MILP relies on w1 >= w2 >= w3 to
    make "put your best bench player in slot 1" the optimal bench ordering.

    The defaults are deliberately crude placeholders. Replace them from the
    simulator via :meth:`from_blank_rate` once appearance draws are available.
    """

    #: P(starting keeper does not play, so the bench keeper's points count).
    gk: float = 0.06
    #: P(bench slot k is subbed on), k = 1, 2, 3.
    outfield: tuple[float, float, float] = (0.19, 0.06, 0.015)

    def __post_init__(self) -> None:
        if not 0.0 <= self.gk <= 1.0:
            raise ValueError(f"gk weight out of [0, 1]: {self.gk}")
        if len(self.outfield) != 3:
            raise ValueError("outfield needs exactly three slot weights")
        if any(not 0.0 <= w <= 1.0 for w in self.outfield):
            raise ValueError(f"outfield weights out of [0, 1]: {self.outfield}")
        if not (self.outfield[0] >= self.outfield[1] >= self.outfield[2]):
            raise ValueError(
                f"outfield weights must be non-increasing, got {self.outfield}. "
                "The bench-order constraints assume w1 >= w2 >= w3."
            )

    @classmethod
    def from_blank_rate(cls, p_blank: float, *, gk_blank: float | None = None) -> AutosubWeights:
        """Slot weights from a common per-starter blank probability.

        P(at least k of the 10 outfield starters fail to appear), under an
        independence assumption that the simulator does not have to make. Use
        this for a sanity-checkable weight set, not as a substitute for the
        simulator's own numbers.
        """
        if not 0.0 <= p_blank <= 1.0:
            raise ValueError("p_blank out of [0, 1]")
        n = 10
        # P(X >= k) for X ~ Binomial(10, p_blank), k = 1, 2, 3.
        from math import comb

        pmf = [comb(n, j) * p_blank**j * (1.0 - p_blank) ** (n - j) for j in range(n + 1)]
        tail = [sum(pmf[k:]) for k in range(4)]
        return cls(
            gk=gk_blank if gk_blank is not None else p_blank,
            outfield=(tail[1], tail[2], tail[3]),
        )


@dataclass(frozen=True, slots=True)
class SolverConfig:
    backend: SolverBackend = SolverBackend.HIGHS
    time_limit_s: float | None = 300.0
    mip_gap_rel: float | None = 1e-4
    #: Single-threaded by default. Parallel MIP is non-deterministic: worker
    #: threads race to find incumbents, so tie-broken optima differ run to run.
    threads: int = 1
    seed: int = 0
    msg: bool = False


@dataclass(frozen=True, slots=True)
class OptimizerConfig:
    """Everything that changes what the optimiser returns.

    ``mode`` has no default on purpose: pick one, and the choice is recorded on
    the resulting :class:`~fpl_edge.opt.plan.HorizonPlan`.
    """

    mode: ObjectiveMode
    autosubs: AutosubWeights = field(default_factory=AutosubWeights)
    solver: SolverConfig = field(default_factory=SolverConfig)

    #: Per-gameweek multiplier on that gameweek's contribution. Length must
    #: match the horizon, or None for no discounting. Later gameweeks are
    #: forecast worse; discounting says so explicitly rather than pretending
    #: GW+5 xPts are as trustworthy as GW+1.
    gw_discount: tuple[float, ...] | None = None

    #: Model which of the three outfield bench slots each benched player takes.
    #: Off collapses them to the mean weight and saves ~2 binaries per player
    #: per gameweek; the resulting bench order is then chosen greedily in
    #: post-processing and the declared objective changes to match.
    model_bench_order: bool = True

    #: Chips the planner is allowed to schedule. Availability windows still
    #: apply on top of this; this only ever removes options.
    allowed_chips: frozenset[str] = frozenset({"wildcard", "freehit", "bboost", "3xc"})

    #: Keep only the top-N players per position by horizon-total xPts, plus
    #: everyone currently owned. None = the full universe. Pruning changes the
    #: answer, so it is off by default.
    max_candidates_per_position: int | None = None

    def discount_for(self, n_gws: int) -> tuple[float, ...]:
        if self.gw_discount is None:
            return tuple(1.0 for _ in range(n_gws))
        if len(self.gw_discount) != n_gws:
            raise ValueError(
                f"gw_discount has {len(self.gw_discount)} entries for a {n_gws}-gameweek horizon"
            )
        return self.gw_discount
