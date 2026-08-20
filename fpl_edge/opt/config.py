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
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


class ObjectiveMode(enum.StrEnum):
    """What the MILP actually maximises.

    ``RANK_MV`` is the objective this engine is for and the one that is
    implemented. ``EXPECTED_POINTS`` exists so the difference between the two
    can be measured rather than asserted -- run both, diff the squads, and the
    disagreement is the value the rank machinery is adding.
    """

    #: Maximise the rank-aware certainty equivalent: per-player coefficients
    #: ``mu_p + theta(1 - 2 share_p) sigma_p^2``, with ``theta(D, tau)`` set by
    #: the state. This is F2 from ``docs/platform/rank_objectives.md`` §2, the
    #: study's own recommendation (§8.1) and the only formulation that is both
    #: cheap enough for the argmax loop and state-aware enough to capture the
    #: 9-19pp adaptivity prize. Requires
    #: :class:`~fpl_edge.rank.coefficients.RankCoefficients`.
    RANK_MV = "rank_mv"

    #: Maximise a *simulated* utility of final rank, with the simulator inside
    #: the argmax loop (F1). Still unimplemented, and deliberately so:
    #: ``rank_objectives.md`` §8.2 concludes that F1 belongs in the architecture
    #: as a **validator, not a searcher**. §7.4 is the measurement behind that --
    #: F1's Monte Carlo could not separate 0 of 6 candidate swaps at 2 SE, while
    #: the closed forms separate captaincy and hit decisions analytically from
    #: the same second moments the simulator estimates well. Use ``RANK_MV`` to
    #: choose and :mod:`fpl_edge.rank.validate` to check.
    RANK_UTILITY = "rank_utility"

    #: Maximise discounted expected points net of transfer hits. A surrogate.
    EXPECTED_POINTS = "expected_points"


#: Marginal value, in points, of rolling into each banked-free-transfer state.
#: Adopted from the public solver SOTA (``docs/platform/solver_state_of_art.md``
#: §1.3, their ``ft_value_list``), where these are community-tuned defaults.
#:
#: They are **not** recalibrated against our simulator, which is exactly why
#: :attr:`OptimizerConfig.ft_value_list` defaults to ``None`` and the term is
#: off unless a caller opts in. Under this repo's evidence rule an unvalidated
#: constant may be *offered*, never *defaulted into the objective*.
FT_VALUE_LIST_SOTA: Mapping[int, float] = MappingProxyType({2: 2.0, 3: 1.6, 4: 1.3, 5: 1.1})

#: Marginal value of a banked-FT state not named in the list.
FT_VALUE_DEFAULT = 1.5

#: Alternative discount factors the plan is re-scored under after solving,
#: reported and never optimised (their ``report_decay_base``). 1.0 is the
#: undiscounted truth; 0.85 is roughly the community default, and the gap
#: between the two is a free read on how horizon-sensitive a plan is.
REPORT_DECAY_BASES: tuple[float, ...] = (0.85, 1.0)


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

    # -- geometric discounting (SOTA §1.3 ``decay_base``) --------------------

    #: Geometric discount on future gameweeks: gameweek ``k`` of the horizon is
    #: weighted ``decay_base ** k``. Sugar over ``gw_discount``, which still
    #: wins if both are set. **1.0 = off**, unlike the public solver's 0.84-0.9,
    #: because a discount is a claim about how fast forecast trust decays and we
    #: have not measured ours. :func:`fpl_edge.opt.scoring.decay_metrics`
    #: re-scores the returned plan at :data:`REPORT_DECAY_BASES` so the
    #: sensitivity is visible without being baked in.
    decay_base: float = 1.0

    # -- terminal-value potentials (SOTA §1.3 / §6.1) ------------------------

    #: Marginal value of each banked-FT state, as a telescoping potential. None
    #: switches the term off, which is the default. See
    #: :data:`FT_VALUE_LIST_SOTA` for the community values and why they are not
    #: the default. Without this term the model spends every free transfer by
    #: the last gameweek of the horizon, because leftover resources are worth
    #: zero -- a pure truncation artefact, not a decision.
    ft_value_list: Mapping[int, float] | None = None

    #: Marginal value of a banked-FT state absent from ``ft_value_list``.
    ft_value: float = FT_VALUE_DEFAULT

    # -- what-if controls (SOTA §6.3, §6.4) ----------------------------------

    #: Player codes that must be owned in every gameweek of the horizon.
    locked: frozenset[int] = frozenset()

    #: Player codes that may never be owned. A banned player who is currently
    #: held is forced out in the first gameweek, which is what "ban" has to mean
    #: for it to be usable as a what-if.
    banned: frozenset[int] = frozenset()

    #: ``{chip: (gw, ...)}`` -- the chip may fire ONLY in these gameweeks; the
    #: solver still picks which. Availability windows apply on top.
    allowed_chip_gws: Mapping[str, tuple[int, ...]] | None = None

    #: ``{chip: (gw, ...)}`` -- the chip MUST fire in exactly one of these
    #: gameweeks. This is how "wildcard somewhere in 28-31, you pick" is asked.
    forced_chip_gws: Mapping[str, tuple[int, ...]] | None = None

    #: Gameweeks in which no chip may be played at all.
    no_chip_gws: tuple[int, ...] = ()

    # -- rank objective ------------------------------------------------------

    #: Clip on ``|theta|`` for :data:`ObjectiveMode.RANK_MV`. ``None`` (the
    #: default) leaves the F2 trust region off; see
    #: :func:`fpl_edge.rank.policy.theta` for why no value is invented here.
    theta_cap: float | None = None

    def __post_init__(self) -> None:
        if not 0.0 < self.decay_base <= 1.0:
            raise ValueError(
                f"decay_base must lie in (0, 1], got {self.decay_base}. A base "
                "above 1 weights the least trustworthy forecasts most heavily."
            )
        if self.ft_value_list is not None:
            for state, value in self.ft_value_list.items():
                if int(state) < 1:
                    raise ValueError(f"banked-FT state {state} is not a state")
                if float(value) < 0.0:
                    raise ValueError(f"banked-FT value for state {state} is negative")
        overlap = self.locked & self.banned
        if overlap:
            raise ValueError(f"players both locked and banned: {sorted(overlap)}")

    def discount_for(self, n_gws: int) -> tuple[float, ...]:
        if self.gw_discount is None:
            if self.decay_base == 1.0:
                return tuple(1.0 for _ in range(n_gws))
            return tuple(self.decay_base**k for k in range(n_gws))
        if len(self.gw_discount) != n_gws:
            raise ValueError(
                f"gw_discount has {len(self.gw_discount)} entries for a {n_gws}-gameweek horizon"
            )
        return self.gw_discount

    def ft_state_values(self, min_state: int, max_state: int) -> dict[int, float]:
        """The telescoping potential ``V(s)``, normalised to ``V(min_state) = 0``.

        ``V(s) = V(s-1) + ft_value_list.get(s, ft_value)`` (SOTA §1.3). Banking
        a transfer earns the marginal state value once and spending it pays the
        value back, so the term is a terminal-value correction for horizon
        truncation rather than a standing bonus for hoarding.

        Normalising at ``min_state`` matters: the MILP's FT variable is bounded
        below by ``free_per_gw``, so ``V(min_state)`` is a constant every
        feasible plan pays and carrying it would only shift the objective.
        """
        if self.ft_value_list is None:
            raise ValueError("ft_value_list is None; the banked-FT term is off")
        table = {int(k): float(v) for k, v in self.ft_value_list.items()}
        out = {min_state: 0.0}
        for s in range(min_state + 1, max_state + 1):
            out[s] = out[s - 1] + table.get(s, self.ft_value)
        increments = [out[s] - out[s - 1] for s in range(min_state + 1, max_state + 1)]
        if any(b > a for a, b in zip(increments, increments[1:])):
            raise ValueError(
                f"banked-FT values must be concave over [{min_state}, {max_state}] "
                f"(non-increasing marginal value), got increments {increments}. "
                "The MILP prices them with a concave piecewise-linear envelope, "
                "which is exact at integers only under concavity."
            )
        return out
