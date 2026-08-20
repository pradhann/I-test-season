"""F1 as a validator: paired common-random-number checks on the shortlist.

``rank_objectives.md`` §8.2 gives this layer its exact job:

    Run the full simulator on the F2 shortlist (<=10 candidates) with common
    random numbers and report paired Delta P(top 10k); it is the only layer that
    prices squad-level covariance exactly.

and §7.4 gives it its exact limits:

    F1's Monte Carlo could not separate 0 of 6 candidate swaps at 2 SE, yet the
    closed forms in §4-§5 separate captaincy and hit decisions analytically
    under the same second-moment assumptions the simulator itself estimates
    well. The simulator's comparative advantage is estimating (m, s, Cov) --
    not being the argmax loop.

So this module reports, and reports honestly, including when it cannot resolve
a difference. **A validator that cannot separate two plans has told you
something true** -- that the MILP's preference between them is inside Monte
Carlo noise -- and that is why :attr:`PlanDelta.resolved` exists and why the
summary line says "unresolved" rather than quietly showing a sign.

Everything here is *paired*. Both plans are scored on the same point draws
against the same sampled field, so the difference is a paired statistic whose
standard error is roughly an order of magnitude smaller than the unpaired one
(:class:`fpl_edge.sim.rank.Counterfactual`). Levels are not comparable across
runs and are deliberately not the headline: §7.4's "model-flattering" critique
applies to the level of P(top 10k), not to a paired difference.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from fpl_edge.rank.state import TOP_K


@runtime_checkable
class PairedSimulator(Protocol):
    """The slice of :class:`~fpl_edge.sim.engine.SeasonSimulator` this needs.

    Narrow on purpose: a validator that demanded the whole simulator could not
    be tested without a warehouse, and a validator nobody tests is not a check.
    """

    def evaluate(self, plan: object, *, label: str | None = ...) -> object:
        """Return a :class:`~fpl_edge.sim.rank.RankDistribution` for ``plan``."""
        ...


@dataclass(frozen=True, slots=True)
class PlanDelta:
    """One plan's simulated result, paired against the baseline plan.

    ``delta_*`` fields are ``None`` for the baseline itself, which is the plan
    everything else is measured against -- normally plan 1, the MILP's argmax.
    """

    label: str
    p_top: float
    se_p_top: float
    mean_points: float
    delta_p_top: float | None = None
    se_delta_p_top: float | None = None
    delta_points: float | None = None
    se_delta_points: float | None = None
    #: Whether the sim was extrapolated past its rival resolution at this
    #: threshold (the Cornish-Fisher tail in :mod:`fpl_edge.sim.rank`).
    extrapolated: bool = False

    @property
    def is_baseline(self) -> bool:
        return self.delta_p_top is None

    @property
    def resolved(self) -> bool:
        """``|Delta| > 2 SE`` -- did the simulator actually separate the plans?

        False is a finding, not a failure. §7.4 measured 0 of 6 swaps resolving
        at this bar, and a report that hid that would be inviting the reader to
        act on noise.
        """
        if self.delta_p_top is None or self.se_delta_p_top is None:
            return False
        return abs(self.delta_p_top) > 2.0 * self.se_delta_p_top

    def describe(self, threshold: int = TOP_K) -> str:
        if self.is_baseline:
            return (
                f"{self.label:<28} P(top {threshold:,}) = {self.p_top:.4f} "
                f"+-{self.se_p_top:.4f}   [baseline]"
            )
        assert self.delta_p_top is not None and self.se_delta_p_top is not None
        verdict = "RESOLVED" if self.resolved else "unresolved at 2 SE"
        return (
            f"{self.label:<28} P(top {threshold:,}) = {self.p_top:.4f}   "
            f"delta {self.delta_p_top:+.4f} +-{self.se_delta_p_top:.4f} "
            f"({verdict}); delta points {self.delta_points:+.2f}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "p_top": self.p_top,
            "se_p_top": self.se_p_top,
            "mean_points": self.mean_points,
            "delta_p_top": self.delta_p_top,
            "se_delta_p_top": self.se_delta_p_top,
            "delta_points": self.delta_points,
            "se_delta_points": self.se_delta_points,
            "resolved": self.resolved,
            "extrapolated": self.extrapolated,
        }


def validate_plans(
    simulator: PairedSimulator,
    plans: Sequence[object],
    *,
    threshold: int = TOP_K,
    labels: Sequence[str] | None = None,
) -> list[PlanDelta]:
    """Score every plan on common random numbers; report deltas vs ``plans[0]``.

    ``plans`` are :class:`~fpl_edge.sim.engine.SquadPlan` objects -- typically
    the output of :func:`fpl_edge.opt.milp.enumerate_plans` converted by
    :func:`squad_plan_from_horizon`. The first is the baseline.

    The common random numbers come for free: :class:`SeasonSimulator` draws its
    points once and samples its field once, then scores every candidate against
    those same draws. Nothing here re-seeds anything, and nothing here should --
    re-drawing between candidates would destroy exactly the pairing that makes
    these differences resolvable at a few thousand simulations rather than a few
    million.
    """
    from fpl_edge.sim.rank import Counterfactual

    if not plans:
        raise ValueError("nothing to validate")
    if labels is not None and len(labels) != len(plans):
        raise ValueError(
            f"got {len(labels)} labels for {len(plans)} plans; a mislabelled "
            "validation is worse than an unlabelled one"
        )

    dists = []
    for k, plan in enumerate(plans):
        label = labels[k] if labels is not None else f"plan {k + 1}"
        dists.append(simulator.evaluate(plan, label=label))

    baseline = dists[0]
    out: list[PlanDelta] = []
    for k, dist in enumerate(dists):
        label = labels[k] if labels is not None else f"plan {k + 1}"
        extrapolated = threshold < getattr(dist, "field_size", TOP_K * 200) / 200
        common = {
            "label": label,
            "p_top": dist.p_top(threshold),
            "se_p_top": dist.se_p_top(threshold),
            "mean_points": dist.expected_points(),
            "extrapolated": bool(extrapolated),
        }
        if k == 0:
            out.append(PlanDelta(**common))
            continue
        cf = Counterfactual(a=dist, b=baseline)
        out.append(
            PlanDelta(
                **common,
                delta_p_top=cf.delta_p_top(threshold),
                se_delta_p_top=cf.se_delta_p_top(threshold),
                delta_points=cf.delta_points(),
                se_delta_points=cf.se_delta_points(),
            )
        )
    return out


def render(deltas: Sequence[PlanDelta], *, threshold: int = TOP_K) -> str:
    """A report block, including the sentence that must not be omitted."""
    lines = [
        f"F1 paired-CRN validation against P(top {threshold:,}), "
        f"baseline = {deltas[0].label}:"
    ]
    lines += [f"  {d.describe(threshold)}" for d in deltas]
    n_resolved = sum(1 for d in deltas if not d.is_baseline and d.resolved)
    n_compared = sum(1 for d in deltas if not d.is_baseline)
    lines.append(
        f"  {n_resolved} of {n_compared} alternatives separated from the baseline "
        "at 2 paired SE."
    )
    if n_compared and n_resolved == 0:
        lines.append(
            "  The simulator cannot tell these plans apart. That is a result, not "
            "a failure: rank_objectives.md §7.4 measured the same thing (0 of 6 "
            "swaps resolved at 2 SE) and concluded the simulator's comparative "
            "advantage is estimating (m, s, Cov), not being the argmax loop. "
            "Decide on the F2 objective and the closed forms; do not read a sign "
            "off noise."
        )
    if any(d.extrapolated for d in deltas):
        lines.append(
            "  NOTE: this threshold is below the sampled field's resolution, so "
            "the Cornish-Fisher tail extrapolation is load-bearing in the levels. "
            "The paired deltas are less affected than the levels, but neither is "
            "purely empirical here."
        )
    return "\n".join(lines)


def squad_plan_from_horizon(
    plan: object,
    universe: object,
    *,
    label: str = "",
    chips: bool = True,
) -> object:
    """Convert a :class:`~fpl_edge.opt.plan.HorizonPlan` into a simulator plan.

    The two layers speak different languages on purpose -- the optimiser is
    keyed on player *codes* and the simulator on *universe indices* -- so the
    translation is explicit and it raises on anything it cannot represent
    faithfully rather than dropping it.

    Hits are carried across, because a plan's points hit is part of the decision
    being validated: a -4 that the F2 objective judged worth taking must be
    charged against it in the simulation too, or the validator is grading a
    different plan from the one the solver chose.
    """
    import numpy as np

    from fpl_edge.sim.engine import SquadPlan
    from fpl_edge.sim.squad import Squad

    codes = np.asarray(universe.codes)  # type: ignore[attr-defined]
    index_of = {int(c): i for i, c in enumerate(codes)}

    def to_index(code: int) -> int:
        try:
            return index_of[int(code)]
        except KeyError:
            raise ValueError(
                f"player code {code} is in the plan but not in the simulator's "
                "universe; the optimiser and the simulator must be built from "
                "the same snapshot"
            ) from None

    overrides: dict[int, Squad] = {}
    hits: dict[int, int] = {}
    chip_plays: dict[int, str] = {}
    base: Squad | None = None

    for decision in plan.decisions:  # type: ignore[attr-defined]
        gw = int(decision.gw)
        squad = Squad(
            starters=tuple(to_index(c) for c in decision.starting_xi),
            bench=tuple(to_index(c) for c in decision.bench),
            captain=to_index(decision.captain),
            vice=to_index(decision.vice_captain),
        )
        if base is None:
            base = squad
        overrides[gw] = squad
        if decision.hits:
            hits[gw] = decision.hit_points
        if chips and decision.chip:
            chip_plays[gw] = str(decision.chip)

    if base is None:
        raise ValueError("plan has no gameweeks")
    return SquadPlan(
        base=base,
        overrides=overrides,
        hits=hits,
        chips=chip_plays,
        label=label or getattr(plan, "status", ""),
    )
