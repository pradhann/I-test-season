"""Backtestable policies distilled from the manager-mining work.

Each module here turns one claim about what skilled managers do into a
parameterised object another team's backtester can run through
:mod:`fpl_edge.eval.replay`. Nothing in this package asserts that a claim is
true; the point is to make each one falsifiable and to hand the falsification to
a harness that enforces the real rules.

Reading the ``evidence`` field
------------------------------
Every :class:`~strategies.base.PolicySpec` carries an ``evidence`` string, and
it is the most important field on the object. Right now most of them read
``UNTESTED``, and the reason is structural rather than a matter of effort: the
FPL API publishes a manager's squad, transfers and chips **only for the season
in progress**, and only from the moment each gameweek's deadline passes. There
is no endpoint that returns what the 2022/23 champion owned in GW14, for anyone,
at any budget. So the strategy features that would separate winners from the
field cannot be computed for any completed season, and a policy grid tuned today
would be tuned on nothing.

What is available today is the seasonal record -- final ranks, across many
seasons, for hundreds of managers -- and that is enough to answer the prior
question of whether manager skill persists at all
(:mod:`fpl_edge.models.copying.skill`). The policies here become testable from
GW1 onward, as the crawl accumulates squads.

Marking them ``UNTESTED`` rather than shipping tuned defaults is the point. A
grid fitted to a story is indistinguishable, at the level of the code, from a
grid fitted to data.

Usage::

    from fpl_edge.eval.baselines import TemplateStrategy
    from strategies import PolicyStrategy
    from strategies.hits import HitCap
    from strategies.chips import HoldWildcard

    strategy = PolicyStrategy(
        inner=TemplateStrategy(),
        policies=(HoldWildcard(not_before_gw=8), HitCap(max_hits_per_gw=1)),
    )

``strategy`` satisfies the :class:`~fpl_edge.eval.replay.Strategy` protocol and
can be replayed directly. Running the inner strategy alone and then wrapped
gives a controlled comparison in which the difference is attributable to the
policies.

STATUS: RESEARCH, wholly outside the production import closure (reachability audit 2026-08-20). This package is the Backtesting phase's raw material: each module is a falsifiable policy for fpl_edge.eval.replay to grade. It becomes production the day the replay harness runs it, not before.
"""

from __future__ import annotations

from strategies.base import NullPolicy, Policy, PolicySpec, PolicyStrategy
from strategies.chips import SPECS as CHIP_SPECS
from strategies.chips import BenchBoostOnDoubles, ChipCalendar, HoldWildcard
from strategies.copying import SPECS as COPY_SPECS
from strategies.copying import CohortPicks, CopyCohort
from strategies.differentials import SPECS as DIFF_SPECS
from strategies.differentials import DifferentialFloor, OwnershipCeiling
from strategies.hits import SPECS as HIT_SPECS
from strategies.hits import HitCap, NoHitsAfter, SeasonHitBudget

#: Every policy hypothesis in one place, so a sweep cannot quietly omit the
#: arms that were expected to lose.
ALL_SPECS: tuple[PolicySpec, ...] = CHIP_SPECS + DIFF_SPECS + HIT_SPECS + COPY_SPECS

REGISTRY: dict[str, PolicySpec] = {spec.name: spec for spec in ALL_SPECS}


def parameter_grid(spec: PolicySpec) -> list[dict]:
    """Expand a spec's grid into the full list of parameter combinations."""
    import itertools

    if not spec.grid:
        return [{}]
    keys = sorted(spec.grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(spec.grid[k] for k in keys))]


def untested() -> list[str]:
    """Policy names whose evidence field still says they have not been tested.

    Exists so that a report can state the number rather than a reader having to
    grep for it, and so that the number visibly falls as data arrives.
    """
    return [name for name, spec in REGISTRY.items() if "UNTESTED" in spec.evidence]


__all__ = [
    "ALL_SPECS", "REGISTRY", "BenchBoostOnDoubles", "ChipCalendar", "CohortPicks",
    "CopyCohort", "DifferentialFloor", "HitCap", "HoldWildcard", "NoHitsAfter",
    "NullPolicy", "OwnershipCeiling", "Policy", "PolicySpec", "PolicyStrategy",
    "SeasonHitBudget", "parameter_grid", "untested",
]
