"""The declared objective, and an independent recomputation of it.

This module is the reference implementation of what "the objective" *means*.
The MILP in :mod:`fpl_edge.opt.milp` builds a linear encoding of exactly this
formula; :func:`score_plan` recomputes it from the returned decisions using
plain Python. If those two numbers disagree, the model is not optimising what
it reports, which is the classic and silent MILP failure. ``tests/unit/
test_opt_objective.py`` asserts they agree to 1e-6.

Approximations are declared here rather than hidden in the encoding. Both the
MILP and this scorer use them, so agreement is meaningful:

* **Autosub weights** are fixed per-slot activation probabilities from
  :class:`~fpl_edge.opt.config.AutosubWeights`, not the true joint
  distribution of appearances.
* **The vice-captain term** is ``(1 - P(captain plays)) * xPts(vice)``. It uses
  the base captain multiplier even under Triple Captain, because the exact
  version is a product of three binaries for a term of order
  ``P(no play) * P(TC)``.
* **Expected points are per gameweek**, so double gameweeks must arrive
  pre-aggregated in ``xpts``.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable

from fpl_edge.opt.config import ObjectiveMode, OptimizerConfig
from fpl_edge.opt.plan import GwDecision, HorizonPlan
from fpl_edge.opt.problem import CHIP_NAMES, HorizonProblem, Ruleset
from fpl_edge.types import Money, PlayerCode, Position, selling_price


class PlanInvalidError(ValueError):
    """A plan violates a rule the optimiser claims to enforce."""


def score_plan(problem: HorizonProblem, plan: HorizonPlan, config: OptimizerConfig) -> float:
    """Recompute the declared objective for ``plan`` from its decisions alone.

    Raises for :data:`ObjectiveMode.RANK_UTILITY`: scoring a rank objective
    means running the simulator, and approximating it with means here would
    reintroduce exactly the bug the mode exists to avoid.
    """
    if config.mode is ObjectiveMode.RANK_UTILITY:
        from fpl_edge.opt.interfaces import RankUtilityUnavailableError

        raise RankUtilityUnavailableError(
            "score_plan cannot evaluate RANK_UTILITY without a RankUtilityProvider. "
            "Call provider.evaluate_plan(plan) instead; the simulated value is the "
            "only honest number for that mode."
        )
    return sum(
        contribution
        for _, contribution in gw_contributions(problem, plan, config)
    )


def gw_contributions(
    problem: HorizonProblem, plan: HorizonPlan, config: OptimizerConfig
) -> list[tuple[int, float]]:
    """Per-gameweek discounted objective contributions, for reporting."""
    idx = problem.index_of
    discount = config.discount_for(problem.n_gws)
    out: list[tuple[int, float]] = []

    for j, decision in enumerate(plan.decisions):
        if int(decision.gw) != int(problem.gws[j]):
            raise PlanInvalidError(
                f"plan gameweek {decision.gw} does not match problem gameweek {problem.gws[j]}"
            )
        xp = {c: float(problem.xpts[idx[c], j]) for c in decision.fielded}
        value = sum(xp[c] for c in decision.starting_xi)

        cap_mult = (
            problem.ruleset.triple_captain_multiplier
            if decision.chip == "3xc"
            else problem.ruleset.captain_multiplier
        )
        value += (cap_mult - 1) * xp[decision.captain]

        p_cap_plays = float(problem.p_play[idx[decision.captain], j])
        value += (
            (problem.ruleset.captain_multiplier - 1)
            * (1.0 - p_cap_plays)
            * xp[decision.vice_captain]
        )

        value += _bench_value(decision, xp, config, problem.ruleset)
        value += problem.ruleset.hit_cost * decision.hits  # hit_cost is negative
        out.append((int(decision.gw), discount[j] * value))
    return out


def _bench_value(
    decision: GwDecision,
    xp: dict[PlayerCode, float],
    config: OptimizerConfig,
    ruleset: Ruleset,
) -> float:
    bench_gk, *outfield = decision.bench
    if decision.chip == "bboost":
        return xp[bench_gk] + sum(xp[c] for c in outfield)
    w = config.autosubs
    if config.model_bench_order:
        weights = w.outfield
    else:
        mean_w = sum(w.outfield) / 3.0
        weights = (mean_w, mean_w, mean_w)
    return w.gk * xp[bench_gk] + sum(wk * xp[c] for wk, c in zip(weights, outfield))


# ---------------------------------------------------------------------------
# Constraint validation, independent of the MILP encoding.
# ---------------------------------------------------------------------------


def validate_plan(problem: HorizonProblem, plan: HorizonPlan) -> list[str]:
    """Every rule violation in ``plan``, as human-readable strings.

    Written against the plan's decisions and the rule registry, with no
    reference to the MILP. A constraint that only exists in the encoding and
    not here (or vice versa) is a bug in one of them.
    """
    problems: list[str] = []
    rs = problem.ruleset
    idx = problem.index_of
    by_code = {p.code: p for p in problem.players}

    if len(plan.decisions) != problem.n_gws:
        problems.append(f"plan has {len(plan.decisions)} gameweeks, problem has {problem.n_gws}")
        return problems

    finance = replay_finances(problem, plan, problems)

    prev_squad: frozenset[PlayerCode] | None = (
        frozenset(problem.state.holdings) if not problem.state.is_preseason else None
    )
    fh_gws: list[int] = []
    chip_uses: dict[str, list[int]] = {c: list(problem.state.chips.played.get(c, ())) for c in CHIP_NAMES}

    for j, d in enumerate(plan.decisions):
        tag = f"GW{d.gw}"
        squad, fielded, xi = set(d.squad), set(d.fielded), set(d.starting_xi)
        bench = list(d.bench)

        # -- squad shape -----------------------------------------------------
        if len(d.squad) != len(squad):
            problems.append(f"{tag}: duplicate players in squad")
        if len(squad) != rs.squad_size:
            problems.append(f"{tag}: squad has {len(squad)} players, rule says {rs.squad_size}")
        if len(fielded) != rs.squad_size:
            problems.append(f"{tag}: fielded squad has {len(fielded)}, rule says {rs.squad_size}")
        for group, label in ((squad, "squad"), (fielded, "fielded")):
            counts = _position_counts(group, by_code)
            for pos, want in rs.select_by_position.items():
                if counts.get(pos, 0) != want:
                    problems.append(
                        f"{tag}: {label} has {counts.get(pos, 0)} {pos.name}, rule says {want}"
                    )
            clubs: dict[int, int] = {}
            for c in group:
                clubs[int(by_code[c].team_code)] = clubs.get(int(by_code[c].team_code), 0) + 1
            for club, n in sorted(clubs.items()):
                if n > rs.max_per_club:
                    problems.append(
                        f"{tag}: {label} has {n} players from club {club}, "
                        f"rule says at most {rs.max_per_club}"
                    )
            for c in group:
                if not problem.ownable[idx[c], j]:
                    problems.append(f"{tag}: {label} contains unownable player {int(c)}")

        # -- starting XI and bench ------------------------------------------
        if len(xi) != rs.starting_xi:
            problems.append(f"{tag}: XI has {len(xi)}, rule says {rs.starting_xi}")
        if not xi <= fielded:
            problems.append(f"{tag}: XI contains players outside the fielded squad")
        if set(bench) | xi != fielded or set(bench) & xi:
            problems.append(f"{tag}: bench and XI do not partition the fielded squad")
        if len(bench) != rs.squad_size - rs.starting_xi:
            problems.append(f"{tag}: bench has {len(bench)} players")
        xi_counts = _position_counts(xi, by_code)
        for pos in Position:
            n = xi_counts.get(pos, 0)
            lo = rs.min_play_by_position.get(pos, 0)
            hi = rs.max_play_by_position.get(pos, rs.starting_xi)
            if not lo <= n <= hi:
                problems.append(f"{tag}: XI has {n} {pos.name}, rule allows {lo}..{hi}")
        if bench and by_code[bench[0]].position is not Position.GKP:
            problems.append(f"{tag}: bench slot 0 must be the reserve keeper")
        if any(by_code[c].position is Position.GKP for c in bench[1:]):
            problems.append(f"{tag}: a keeper is in an outfield bench slot")

        # -- captaincy -------------------------------------------------------
        if d.captain not in xi:
            problems.append(f"{tag}: captain is not in the XI")
        if d.vice_captain not in xi:
            problems.append(f"{tag}: vice-captain is not in the XI")
        if d.captain == d.vice_captain:
            problems.append(f"{tag}: captain and vice-captain are the same player")

        # -- transfers -------------------------------------------------------
        if prev_squad is None:
            if d.transfers_out:
                problems.append(f"{tag}: transfers out of an empty pre-season squad")
        else:
            expect_in = squad - prev_squad
            expect_out = prev_squad - squad
            if d.chip == "freehit":
                if squad != prev_squad:
                    problems.append(f"{tag}: Free Hit changed the persistent squad")
                if d.transfers_in or d.transfers_out:
                    problems.append(f"{tag}: Free Hit recorded persistent transfers")
            else:
                if set(d.transfers_in) != expect_in:
                    problems.append(
                        f"{tag}: transfers_in {sorted(map(int, d.transfers_in))} != "
                        f"squad delta {sorted(map(int, expect_in))}"
                    )
                if set(d.transfers_out) != expect_out:
                    problems.append(
                        f"{tag}: transfers_out {sorted(map(int, d.transfers_out))} != "
                        f"squad delta {sorted(map(int, expect_out))}"
                    )
            if len(d.transfers_in) != len(d.transfers_out):
                problems.append(f"{tag}: unbalanced transfer counts")
            if d.chip not in ("wildcard", "freehit") and len(d.transfers_in) > rs.transfer_cap_per_gw:
                problems.append(
                    f"{tag}: {len(d.transfers_in)} transfers exceeds the "
                    f"{rs.transfer_cap_per_gw} per-gameweek cap"
                )
        if d.chip == "freehit" and fielded == squad and prev_squad is not None:
            pass  # a Free Hit squad may legitimately coincide with the real one

        # -- chips -----------------------------------------------------------
        if d.chip is not None:
            if d.chip not in CHIP_NAMES:
                problems.append(f"{tag}: unknown chip {d.chip!r}")
            elif not rs.chip_available_in_gw(d.chip, int(d.gw)):
                problems.append(
                    f"{tag}: chip {d.chip!r} is outside its availability window "
                    f"{rs.chip_windows[d.chip]}"
                )
            else:
                half = rs.chip_half_of(d.chip, int(d.gw))
                already = [
                    g for g in chip_uses[d.chip] if rs.chip_half_of(d.chip, int(g)) == half
                ]
                if already:
                    problems.append(
                        f"{tag}: second {d.chip!r} in the same half of the season "
                        f"(already used in GW{already[0]})"
                    )
                chip_uses[d.chip].append(int(d.gw))
            if d.chip == "freehit":
                fh_gws.append(int(d.gw))
        if d.chip != "freehit" and fielded != squad:
            problems.append(f"{tag}: fielded squad differs from the persistent squad without Free Hit")

        prev_squad = frozenset(squad)

    if rs.freehit_not_consecutive:
        prior = problem.state.chips.last_gw("freehit")
        marks = ([prior] if prior is not None else []) + fh_gws
        for a, b in itertools.pairwise(marks):
            if b - a <= 1:
                problems.append(f"Free Hit played in consecutive gameweeks GW{a} and GW{b}")
    for chip, uses in chip_uses.items():
        if len(uses) > rs.chip_count_each:
            problems.append(f"{chip!r} used {len(uses)} times, rule says {rs.chip_count_each}")
    seen_gw: dict[int, str] = {}
    for d in plan.decisions:
        if d.chip is None:
            continue
        if rs.chip_one_per_gw and int(d.gw) in seen_gw:
            problems.append(f"GW{d.gw}: two chips in one gameweek")
        seen_gw[int(d.gw)] = d.chip

    problems.extend(finance)
    return problems


def assert_valid(problem: HorizonProblem, plan: HorizonPlan) -> None:
    found = validate_plan(problem, plan)
    if found:
        raise PlanInvalidError("; ".join(found))


def _position_counts(codes: Iterable[PlayerCode], by_code: dict) -> dict[Position, int]:
    out: dict[Position, int] = {}
    for c in codes:
        pos = by_code[c].position
        out[pos] = out.get(pos, 0) + 1
    return out


def replay_finances(
    problem: HorizonProblem, plan: HorizonPlan, sink: list[str] | None = None
) -> list[str]:
    """Re-run the money and free-transfer ledger in exact integer tenths.

    Uses :func:`fpl_edge.types.selling_price` for every sale, so the 50%
    sell-on fee and its floor-to-0.1m rounding are applied by the same code the
    rest of the engine uses. Returns the violations found (also appended to
    ``sink`` if given).
    """
    out: list[str] = []
    rs = problem.ruleset
    idx = problem.index_of
    bought_at: dict[PlayerCode, Money] = dict(problem.state.holdings)
    bank = problem.initial_bank
    ft = problem.initial_free_transfers

    for j, d in enumerate(plan.decisions):
        tag = f"GW{d.gw}"
        free_gw = d.chip in ("wildcard", "freehit") or (
            j == 0 and problem.state.is_preseason and rs.unlimited_before_first_deadline
        )

        if d.chip == "freehit":
            # The persistent squad and its purchase prices are untouched. The
            # one-week squad must still be affordable out of bank plus the full
            # selling value of what you own.
            pot = bank
            for code in d.squad:
                pot = pot + selling_price(bought_at[code], Money(int(problem.price_tenths[idx[code], j])))
            cost = Money(sum(int(problem.price_tenths[idx[c], j]) for c in d.fielded))
            if cost.tenths > pot.tenths:
                out.append(f"{tag}: Free Hit squad costs {cost} but only {pot} is available")
        else:
            for code in d.transfers_out:
                if code not in bought_at:
                    out.append(f"{tag}: sold {int(code)}, which was not owned")
                    continue
                bank = bank + selling_price(
                    bought_at.pop(code), Money(int(problem.price_tenths[idx[code], j]))
                )
            for code in d.transfers_in:
                price = Money(int(problem.price_tenths[idx[code], j]))
                bank = bank - price
                bought_at[code] = price
            if problem.state.is_preseason and j == 0:
                # Pre-season: everything is a purchase out of the initial budget.
                bought_at = {
                    c: Money(int(problem.price_tenths[idx[c], j])) for c in d.squad
                }
                spend = sum(v.tenths for v in bought_at.values())
                bank = Money(problem.initial_bank.tenths - spend)
            if bank.tenths < 0:
                out.append(f"{tag}: bank is {bank}, which is negative")

        if bank.tenths != d.bank_after.tenths:
            out.append(f"{tag}: reported bank {d.bank_after} but ledger says {bank}")

        # -- free transfers --------------------------------------------------
        if d.free_transfers_available != ft:
            out.append(f"{tag}: reported {d.free_transfers_available} free transfers, ledger says {ft}")
        n_transfers = len(d.transfers_in)
        if free_gw:
            hits = 0
            ft_next = min(rs.max_banked_ft, ft + rs.free_per_gw)
        else:
            used = min(n_transfers, ft)
            hits = n_transfers - used
            ft_next = min(rs.max_banked_ft, ft - used + rs.free_per_gw)
        if d.hits != hits:
            out.append(f"{tag}: reported {d.hits} hits, ledger says {hits}")
        ft = ft_next

    if sink is not None:
        sink.extend(out)
    return out
