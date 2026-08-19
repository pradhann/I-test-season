"""Multi-gameweek FPL squad MILP.

One model covers the whole rolling horizon: squad, starting XI, bench order,
captain, vice-captain, transfers with free-transfer carryover and hits, the
50% sell-on fee in exact integer tenths, and chip scheduling. Solving each
gameweek greedily and stitching the answers together gets transfer planning
wrong in the one place it matters -- banking a transfer now to make two next
week is a multi-period decision -- so the horizon is a single optimisation.

Formulation notes worth knowing before reading the code:

**Two squads per gameweek.** ``own`` is the squad you persistently hold;
``play`` is the fifteen that score this week. They are identical except under
Free Hit, where ``play`` is a one-week squad and ``own`` carries through
untouched. This is what makes Free Hit's reversion fall out of the transfer
flow constraint instead of needing a special case.

**Selling prices stay integral.** ``sale_value`` is an integer variable
constrained by ``2 * sale <= purchase_price + price`` and ``sale <= price``.
For integer tenths those two bounds give exactly
``min(price, purchase + floor((price - purchase) / 2))``, which is the FPL
rule: you keep half the rise, floored to 0.1m, and bear the whole of any fall.
No floats and no rounding step. See
:func:`fpl_edge.types.selling_price`, which the independent scorer uses.

**Purchase prices are tracked, not assumed.** ``purchase[i][j]`` follows the
player: set to that gameweek's price when bought, carried unchanged while held,
zero when not owned. That is what makes the sell-on fee correct for a player
bought mid-horizon.

**Bilinear terms are linearised exactly, not approximated.** Vice-captain value
is ``P(captain blanks) x xPts(vice)``, a product of a continuous expression and
a binary; Triple Captain and Bench Boost are binary-times-binary. All three use
standard exact linearisations, so nothing here is an envelope or a relaxation.

The two things that are genuinely approximated -- fixed autosub slot weights,
and the vice-captain term ignoring Triple Captain -- are declared in
:mod:`fpl_edge.opt.scoring` and applied identically there, so the objective the
model maximises equals the objective the scorer recomputes.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pulp

from fpl_edge.opt.config import ObjectiveMode, OptimizerConfig, SolverBackend
from fpl_edge.opt.interfaces import RankUtilityUnavailableError
from fpl_edge.opt.plan import GwDecision, HorizonPlan
from fpl_edge.opt.problem import CHIP_NAMES, HorizonProblem
from fpl_edge.types import Money, PlayerCode, Position

_FREE_CHIPS = ("wildcard", "freehit")


def _live(term: object) -> bool:
    """True when ``term`` is a real decision variable rather than the constant 0.

    Chips outside their availability window are not modelled at all; the helper
    that looks them up returns a plain 0 so the algebra still reads naturally.
    """
    return not isinstance(term, int)


class NoIncumbentError(RuntimeError):
    """The time budget expired before any feasible plan was found.

    Distinct from :class:`InfeasibleError`: a feasible plan exists, the solver
    just did not find one in time. Callers may retry with a larger budget or a
    smaller problem; they must not treat this as "no legal squad exists".
    """


class InfeasibleError(RuntimeError):
    """No squad satisfies the constraints."""


@dataclass(frozen=True, slots=True)
class ModelStats:
    n_variables: int
    n_binary: int
    n_integer: int
    n_constraints: int
    build_seconds: float


def solve_horizon(
    problem: HorizonProblem,
    config: OptimizerConfig,
    *,
    rank_utility: object | None = None,
    return_stats: bool = False,
) -> HorizonPlan | tuple[HorizonPlan, ModelStats]:
    """Optimise a squad plan over the problem's gameweek horizon."""
    if config.mode is ObjectiveMode.RANK_UTILITY:
        if rank_utility is None:
            raise RankUtilityUnavailableError(
                "ObjectiveMode.RANK_UTILITY needs a RankUtilityProvider (see "
                "fpl_edge.opt.interfaces.RankUtilityProvider). Refusing to run: "
                "falling back to expected points would return means while "
                "reporting a rank objective, which is the failure this mode exists "
                "to prevent. Use ObjectiveMode.EXPECTED_POINTS explicitly if means "
                "are what you want."
            )
        raise NotImplementedError(
            "A RankUtilityProvider was supplied but the linearise/solve/re-evaluate "
            "trust-region loop is not implemented yet. E[U(rank)] is not separable "
            "across players, so it cannot be dropped into the MILP objective "
            "directly; wiring it up requires iterating provider.linear_coefficients() "
            "and accepting steps on provider.evaluate_plan(). Until then this mode "
            "raises rather than optimising a surrogate and calling it rank utility."
        )

    problem = problem.prune(config.max_candidates_per_position)
    builder = _Builder(problem, config)
    plan, stats = builder.solve()
    return (plan, stats) if return_stats else plan


class _Builder:
    def __init__(self, problem: HorizonProblem, config: OptimizerConfig) -> None:
        self.p = problem
        self.cfg = config
        self.rs = problem.ruleset
        self.n = problem.n_players
        self.T = problem.n_gws
        self.gws = [int(g) for g in problem.gws]
        self.discount = config.discount_for(self.T)
        self.price = problem.price_tenths
        self.xp = problem.xpts
        self.pplay = problem.p_play
        self.MAXP = int(self.price.max()) + 1
        self.notes: list[str] = []

        self.by_pos: dict[Position, list[int]] = {pos: [] for pos in Position}
        for i, row in enumerate(problem.players):
            self.by_pos[row.position].append(i)
        self.outfield = [i for i in range(self.n) if problem.players[i].position is not Position.GKP]
        self.keepers = self.by_pos[Position.GKP]
        self.by_club: dict[int, list[int]] = {}
        for i, row in enumerate(problem.players):
            self.by_club.setdefault(int(row.team_code), []).append(i)

        self.prior_own = [1 if row.code in problem.state.holdings else 0 for row in problem.players]
        self.prior_pp = [
            problem.state.holdings[row.code].tenths if row.code in problem.state.holdings else 0
            for row in problem.players
        ]
        self.prior_bank = problem.initial_bank.tenths
        self.prior_ft = problem.initial_free_transfers
        # Pre-season: transfers before the first deadline are unlimited and free,
        # so GW1 behaves like a wildcard that costs no chip.
        self.free_gw0 = problem.state.is_preseason and self.rs.unlimited_before_first_deadline

        # Big-M values are per player and per gameweek wherever possible. A
        # single global M over a universe spanning 3.5m to 15.5m makes the LP
        # relaxation useless and the branch-and-bound tree enormous.
        self.MP = [
            max(int(self.price[i, :].max()), self.prior_pp[i]) for i in range(self.n)
        ]

        self.max_squad_cost = [
            sum(
                sum(sorted((int(self.price[i, j]) for i in self.by_pos[pos]), reverse=True)[:want])
                for pos, want in self.rs.select_by_position.items()
            )
            for j in range(self.T)
        ]

        self.model = pulp.LpProblem("fpl_horizon", pulp.LpMaximize)
        self._build()

    # -- variable helpers ----------------------------------------------------

    def _var(self, name: str, lo: float | None = None, hi: float | None = None,
             cat: str = "Continuous"):
        # PuLP 4 attaches variables to the problem at creation; the older
        # free-standing LpVariable constructor is deprecated and noisy.
        return self.model.add_variable(name, lo, hi, cat=cat)

    def _bin(self, name: str, keys: Sequence[tuple]) -> dict:
        return {k: self._var(f"{name}_{'_'.join(map(str, k))}", 0, 1, "Binary") for k in keys}

    def _ij(self) -> list[tuple[int, int]]:
        return [(i, j) for i in range(self.n) for j in range(self.T)]

    # -- model ---------------------------------------------------------------

    def _build(self) -> None:
        t0 = time.perf_counter()
        p, rs, n, T = self.p, self.rs, self.n, self.T
        ij = self._ij()

        # ---- chips ---------------------------------------------------------
        self.chip = {}
        for chip in CHIP_NAMES:
            for j, gw in enumerate(self.gws):
                if chip not in self.cfg.allowed_chips:
                    continue
                half = rs.chip_half_of(chip, gw)
                if half is None:
                    continue  # outside the availability window (this is the GW1 WC/FH lockout)
                if p.state.chips.remaining_in_half(chip, rs, half) < 1:
                    continue
                self.chip[chip, j] = self._var(f"chip_{chip}_{j}", 0, 1, "Binary")

        def chip_at(chip: str, j: int):
            return self.chip.get((chip, j), 0)

        self.fh_gws = frozenset(j for (c, j) in self.chip if c == "freehit")
        self.fh_possible = bool(self.fh_gws)

        # one chip per gameweek; one of each chip per half of the season
        for j in range(T):
            active = [self.chip[c, j] for c in CHIP_NAMES if (c, j) in self.chip]
            if rs.chip_one_per_gw and len(active) > 1:
                self.model += pulp.lpSum(active) <= 1, f"one_chip_gw{j}"
        for chip in CHIP_NAMES:
            for half, (lo, hi) in enumerate(rs.chip_windows.get(chip, ())):
                inside = [
                    self.chip[chip, j]
                    for j, gw in enumerate(self.gws)
                    if (chip, j) in self.chip and lo <= gw <= hi
                ]
                if inside:
                    self.model += (
                        pulp.lpSum(inside) <= p.state.chips.remaining_in_half(chip, rs, half),
                        f"chip_{chip}_half{half}",
                    )
        if rs.freehit_not_consecutive:
            for j in range(T - 1):
                a, b = chip_at("freehit", j), chip_at("freehit", j + 1)
                if _live(a) and _live(b):
                    self.model += a + b <= 1, f"fh_not_consecutive_{j}"
            last = p.state.chips.last_gw("freehit")
            fh0 = chip_at("freehit", 0)
            if last is not None and _live(fh0) and self.gws[0] - last <= 1:
                self.model += fh0 == 0, "fh_not_consecutive_prior"

        # ---- squads --------------------------------------------------------
        self.own = self._bin("own", ij)
        for (i, j), v in self.own.items():
            if not p.ownable[i, j]:
                v.upBound = 0
        # Under Free Hit the fielded squad detaches from the owned squad for one
        # week. With no Free Hit reachable in the horizon they are the same
        # variable, which halves the binary count.
        # A separate fielded squad is only needed in gameweeks where Free Hit is
        # actually reachable. Everywhere else it is the same variable, which
        # keeps the binary count down without weakening anything.
        self.play = dict(self.own)
        for j in self.fh_gws:
            fh = self.chip["freehit", j]
            for i in range(n):
                v = self._var(f"play_{i}_{j}", 0, 1, "Binary")
                if not p.ownable[i, j]:
                    v.upBound = 0
                self.play[i, j] = v
                self.model += v - self.own[i, j] <= fh, f"fhlink_hi_{i}_{j}"
                self.model += self.own[i, j] - v <= fh, f"fhlink_lo_{i}_{j}"

        def shape(group, tag: str, j: int) -> None:
            self.model += (
                pulp.lpSum(group[i, j] for i in range(n)) == rs.squad_size,
                f"{tag}_size_{j}",
            )
            for pos, want in rs.select_by_position.items():
                self.model += (
                    pulp.lpSum(group[i, j] for i in self.by_pos[pos]) == want,
                    f"{tag}_pos_{pos.name}_{j}",
                )
            for club, members in sorted(self.by_club.items()):
                if len(members) <= rs.max_per_club:
                    continue
                self.model += (
                    pulp.lpSum(group[i, j] for i in members) <= rs.max_per_club,
                    f"{tag}_club_{club}_{j}",
                )

        for j in range(T):
            shape(self.own, "own", j)
            if j in self.fh_gws:
                shape(self.play, "play", j)

        # ---- starting XI, bench order --------------------------------------
        self.start = self._bin("start", ij)
        for j in range(T):
            self.model += (
                pulp.lpSum(self.start[i, j] for i in range(n)) == rs.starting_xi,
                f"xi_size_{j}",
            )
            for pos in Position:
                members = self.by_pos[pos]
                total = pulp.lpSum(self.start[i, j] for i in members)
                self.model += total >= rs.min_play_by_position[pos], f"xi_min_{pos.name}_{j}"
                self.model += total <= rs.max_play_by_position[pos], f"xi_max_{pos.name}_{j}"
        for i, j in ij:
            self.model += self.start[i, j] <= self.play[i, j], f"xi_in_squad_{i}_{j}"

        # Bench weights are non-increasing, so "slot 1 holds the best bench
        # player" is optimal. b1 marks slot 1; b12 marks slots 1 and 2; slot 3
        # is whatever is left on the bench. Two binaries per player rather than
        # three, and a tighter LP relaxation than one-hot slot assignment.
        self.b1: dict = {}
        self.b12: dict = {}
        if self.cfg.model_bench_order:
            keys = [(i, j) for i in self.outfield for j in range(T)]
            self.b1 = self._bin("b1", keys)
            self.b12 = self._bin("b12", keys)
            for j in range(T):
                self.model += pulp.lpSum(self.b1[i, j] for i in self.outfield) == 1, f"bench1_{j}"
                self.model += pulp.lpSum(self.b12[i, j] for i in self.outfield) == 2, f"bench12_{j}"
            for i, j in keys:
                self.model += self.b1[i, j] <= self.b12[i, j], f"bench_nest_{i}_{j}"
                self.model += (
                    self.b12[i, j] <= self.play[i, j] - self.start[i, j],
                    f"bench_member_{i}_{j}",
                )

        # ---- captain and vice ----------------------------------------------
        self.cap = self._bin("cap", ij)
        self.vice = self._bin("vice", ij)
        for j in range(T):
            self.model += pulp.lpSum(self.cap[i, j] for i in range(n)) == 1, f"one_captain_{j}"
            self.model += pulp.lpSum(self.vice[i, j] for i in range(n)) == 1, f"one_vice_{j}"
        for i, j in ij:
            self.model += self.cap[i, j] <= self.start[i, j], f"cap_starts_{i}_{j}"
            self.model += self.vice[i, j] <= self.start[i, j], f"vice_starts_{i}_{j}"
            self.model += self.cap[i, j] + self.vice[i, j] <= 1, f"cap_ne_vice_{i}_{j}"

        # ---- transfers, purchase prices, bank ------------------------------
        self.buy = self._bin("buy", ij)
        self.sell = self._bin("sell", ij)
        self.purchase = {
            (i, j): self._var(f"pp_{i}_{j}", 0, self.MP[i]) for i, j in ij
        }
        # Nothing can be sold in a gameweek where nothing is held. Pre-season
        # that is the whole of the first gameweek, which removes n integer
        # variables from the GW1 problem.
        sellable = [
            (i, j) for i, j in ij if j > 0 or self.prior_own[i]
        ]
        self.sale_value = {
            (i, j): self._var(f"sv_{i}_{j}", 0, int(self.price[i, j]), "Integer")
            for i, j in sellable
        }
        self.sale_used = {
            (i, j): self._var(f"su_{i}_{j}", 0, int(self.price[i, j]))
            for i, j in sellable
        }
        self.bank = {
            j: self._var(f"bank_{j}", 0, self.rs.budget_tenths * 4)
            for j in range(T)
        }

        for i, j in ij:
            prev_own = self.own[i, j - 1] if j else self.prior_own[i]
            prev_pp = self.purchase[i, j - 1] if j else self.prior_pp[i]
            px = int(self.price[i, j])

            self.model += (
                self.own[i, j] - prev_own == self.buy[i, j] - self.sell[i, j],
                f"flow_{i}_{j}",
            )
            self.model += self.buy[i, j] + self.sell[i, j] <= 1, f"no_churn_{i}_{j}"
            fh = chip_at("freehit", j)
            if _live(fh):
                self.model += self.buy[i, j] <= 1 - fh, f"fh_no_buy_{i}_{j}"
                self.model += self.sell[i, j] <= 1 - fh, f"fh_no_sell_{i}_{j}"

            # purchase price follows the player
            self.model += self.purchase[i, j] <= self.MP[i] * self.own[i, j], f"pp_owned_{i}_{j}"
            self.model += (
                self.purchase[i, j] >= px - self.MP[i] * (1 - self.buy[i, j]),
                f"pp_buy_lo_{i}_{j}",
            )
            self.model += (
                self.purchase[i, j] <= px + self.MP[i] * (1 - self.buy[i, j]),
                f"pp_buy_hi_{i}_{j}",
            )
            keep = self.own[i, j] - self.buy[i, j]  # 1 iff held from the previous gameweek
            self.model += (
                self.purchase[i, j] - prev_pp <= self.MP[i] * (1 - keep),
                f"pp_keep_hi_{i}_{j}",
            )
            self.model += (
                prev_pp - self.purchase[i, j] <= self.MP[i] * (1 - keep),
                f"pp_keep_lo_{i}_{j}",
            )

            # 50% sell-on fee, floored to 0.1m, in exact integer tenths.
            if (i, j) in self.sale_value:
                self.model += self.sale_value[i, j] <= px * prev_own, f"sv_cap_price_{i}_{j}"
                self.model += 2 * self.sale_value[i, j] <= prev_pp + px, f"sv_cap_fee_{i}_{j}"
                self.model += self.sale_used[i, j] <= self.sale_value[i, j], f"su_cap_{i}_{j}"
                self.model += self.sale_used[i, j] <= px * self.sell[i, j], f"su_sold_{i}_{j}"
            else:
                self.sell[i, j].upBound = 0

        for j in range(T):
            prev_bank = self.bank[j - 1] if j else self.prior_bank
            self.model += (
                self.bank[j]
                == prev_bank
                + pulp.lpSum(self.sale_used[i, j] for i in range(n) if (i, j) in self.sale_used)
                - pulp.lpSum(int(self.price[i, j]) * self.buy[i, j] for i in range(n)),
                f"bank_{j}",
            )
            fh = chip_at("freehit", j)
            if _live(fh):
                self.model += (
                    pulp.lpSum(int(self.price[i, j]) * self.play[i, j] for i in range(n))
                    <= prev_bank
                    + pulp.lpSum(
                        self.sale_value[i, j] for i in range(n) if (i, j) in self.sale_value
                    )
                    + self.max_squad_cost[j] * (1 - fh),
                    f"fh_budget_{j}",
                )

        # ---- free transfers and hits ---------------------------------------
        self.n_transfers = {j: pulp.lpSum(self.buy[i, j] for i in range(n)) for j in range(T)}
        self.paid = {
            j: self._var(f"paid_{j}", 0, rs.squad_size, "Integer")
            for j in range(T)
        }
        self.ft = {
            j: self._var(f"ft_{j}", rs.free_per_gw, rs.max_banked_ft, "Integer")
            for j in range(1, T)
        }
        nohit = {j: self._var(f"nohit_{j}", 0, 1, "Binary") for j in range(T)}
        M = rs.squad_size

        for j in range(T):
            free = chip_at("wildcard", j) + chip_at("freehit", j) + (1 if (j == 0 and self.free_gw0) else 0)
            avail_ft = self.ft[j] if j else self.prior_ft
            self.model += self.n_transfers[j] <= rs.transfer_cap_per_gw + M * free, f"tcap_{j}"
            self.model += self.paid[j] <= M * (1 - free), f"paid_free_{j}"
            self.model += self.paid[j] >= self.n_transfers[j] - avail_ft - M * free, f"paid_lo_{j}"
            self.model += self.paid[j] <= self.n_transfers[j] - avail_ft + M * nohit[j], f"paid_hi_{j}"
            self.model += self.paid[j] <= M * (1 - nohit[j]), f"paid_zero_{j}"

        for j in range(1, T):
            prev_ft = self.ft[j - 1] if j - 1 else self.prior_ft
            free_prev = (
                chip_at("wildcard", j - 1)
                + chip_at("freehit", j - 1)
                + (1 if (j - 1 == 0 and self.free_gw0) else 0)
            )
            self.model += (
                self.ft[j]
                <= prev_ft - self.n_transfers[j - 1] + self.paid[j - 1] + rs.free_per_gw + M * free_prev,
                f"ft_carry_{j}",
            )
            # Wildcard/Free Hit retain banked free transfers but do not mint extras.
            self.model += self.ft[j] <= prev_ft + rs.free_per_gw, f"ft_retain_{j}"

        # ---- objective ------------------------------------------------------
        self.model += self._objective(), "declared_objective"
        allvars = list(self.model.variables())
        # PuLP normalises cat="Binary" to Integer with 0/1 bounds, so binaries
        # have to be identified by their bounds rather than their category.
        binary = [
            v for v in allvars
            if v.cat == pulp.LpInteger and v.lowBound == 0 and v.upBound in (0, 1)
        ]
        self.stats = ModelStats(
            n_variables=len(allvars),
            n_binary=len(binary),
            n_integer=sum(1 for v in allvars if v.cat == pulp.LpInteger) - len(binary),
            n_constraints=self.model.numConstraints(),
            build_seconds=time.perf_counter() - t0,
        )

    # -- objective -----------------------------------------------------------

    def _objective(self):
        rs, n, T = self.rs, self.n, self.T
        w = self.cfg.autosubs
        terms = []
        self.bb_extra: dict = {}
        self.cap_tc: dict = {}
        self.vice_term: dict = {}
        # Chip uplifts are products of a binary chip with a linear expression.
        # When every xPts is non-negative the whole uplift collapses to one
        # variable per gameweek instead of one per player, which is the same
        # optimum with a far smaller branch-and-bound tree. With a negative
        # xPts anywhere the aggregate form could clip a genuinely negative
        # uplift to zero, so fall back to the per-player linearisation.
        aggregate_chips = bool(self.xp.min() >= 0.0)
        if not aggregate_chips:
            self.notes.append(
                "Negative xPts present: chip uplifts use the per-player linearisation, "
                "which is slower to solve."
            )

        for j in range(T):
            d = self.discount[j]
            bb = self.chip.get(("bboost", j), 0)
            tc = self.chip.get(("3xc", j), 0)
            col = self.xp[:, j]
            # Ceilings for the chip uplifts. Triple Captain can only ever add
            # one player's xPts; Bench Boost only ever four. Bounding by the
            # whole universe's xPts, as a naive big-M would, leaves an LP
            # relaxation that plays every chip fractionally for free.
            max_one = max(1.0, float(col.max()))
            n_bench = rs.squad_size - rs.starting_xi
            max_bench = max(1.0, float(np.sort(col)[-n_bench:].sum()))

            # starting XI
            terms += [d * float(self.xp[i, j]) * self.start[i, j] for i in range(n)]
            # captain: doubling a player who blanks doubles zero, so the uplift
            # is exactly xPts with no minutes term needed.
            terms += [
                d * (rs.captain_multiplier - 1) * float(self.xp[i, j]) * self.cap[i, j]
                for i in range(n)
            ]

            # ---- Triple Captain: one extra captain multiple ----------------
            if _live(tc):
                gain = rs.triple_captain_multiplier - rs.captain_multiplier
                captain_xp = pulp.lpSum(float(self.xp[i, j]) * self.cap[i, j] for i in range(n))
                if aggregate_chips:
                    v = self._var(f"tcv_{j}", 0)
                    self.model += v <= max_one * tc, f"tcv_chip_{j}"
                    self.model += v <= captain_xp, f"tcv_cap_{j}"
                    terms.append(d * gain * v)
                else:
                    for i in range(n):
                        v = self._var(f"captc_{i}_{j}", 0, 1)
                        self.cap_tc[i, j] = v
                        self.model += v <= self.cap[i, j], f"captc_cap_{i}_{j}"
                        self.model += v <= tc, f"captc_tc_{i}_{j}"
                        self.model += v >= self.cap[i, j] + tc - 1, f"captc_and_{i}_{j}"
                        terms.append(d * gain * float(self.xp[i, j]) * v)

            # ---- vice-captain: P(captain blanks) x xPts(vice) ---------------
            # A [0, 1] continuous expression times a binary. Exact, and it does
            # not aggregate: both factors are decisions.
            blank = pulp.lpSum(
                (1.0 - float(self.pplay[i, j])) * self.cap[i, j] for i in range(n)
            )
            for i in range(n):
                v = self._var(f"vt_{i}_{j}", 0, 1)
                self.vice_term[i, j] = v
                self.model += v <= self.vice[i, j], f"vt_vice_{i}_{j}"
                self.model += v <= blank, f"vt_blank_{i}_{j}"
                self.model += v >= blank - (1 - self.vice[i, j]), f"vt_and_{i}_{j}"
                terms.append(d * (rs.captain_multiplier - 1) * float(self.xp[i, j]) * v)

            # ---- bench: fixed autosub weights, 1.0 under Bench Boost --------
            mean_w = sum(w.outfield) / 3.0
            bb_gap = []
            for i in range(n):
                benched = self.play[i, j] - self.start[i, j]
                if self.p.players[i].position is Position.GKP:
                    base = w.gk * benched
                elif self.cfg.model_bench_order:
                    base = (
                        (w.outfield[0] - w.outfield[1]) * self.b1[i, j]
                        + (w.outfield[1] - w.outfield[2]) * self.b12[i, j]
                        + w.outfield[2] * benched
                    )
                else:
                    base = mean_w * benched
                terms.append(d * float(self.xp[i, j]) * base)
                if _live(bb):
                    gap = float(self.xp[i, j]) * (benched - base)
                    if aggregate_chips:
                        bb_gap.append(gap)
                    else:
                        v = self._var(f"bbx_{i}_{j}", 0, 1)
                        self.bb_extra[i, j] = v
                        self.model += v <= bb, f"bbx_chip_{i}_{j}"
                        self.model += v <= benched - base, f"bbx_gap_{i}_{j}"
                        self.model += v >= (benched - base) - (1 - bb), f"bbx_and_{i}_{j}"
                        terms.append(d * float(self.xp[i, j]) * v)
            if _live(bb) and aggregate_chips:
                v = self._var(f"bbv_{j}", 0)
                self.model += v <= max_bench * bb, f"bbv_chip_{j}"
                self.model += v <= pulp.lpSum(bb_gap), f"bbv_gap_{j}"
                terms.append(d * v)

            terms.append(d * rs.hit_cost * self.paid[j])  # hit_cost is negative

        return pulp.lpSum(terms)

    # -- solve ---------------------------------------------------------------

    def solve(self) -> tuple[HorizonPlan, ModelStats]:
        solver, backend_name = _make_solver(self.cfg, self.notes)
        t0 = time.perf_counter()
        status = self.model.solve(solver)
        elapsed = time.perf_counter() - t0
        label = pulp.LpStatus[status]
        if label == "Infeasible":
            raise InfeasibleError(
                "No squad satisfies the constraints. Common causes: a budget too "
                "small for the cheapest legal 15, an ownable mask that leaves fewer "
                "than 3 forwards, or a held squad that already breaks the 3-per-club limit."
            )
        if label not in ("Optimal", "Not Solved"):
            raise RuntimeError(f"solver returned status {label!r}")
        if not self._has_incumbent():
            # "Not Solved" covers both "stopped at the gap with an incumbent"
            # and "time expired before ANY feasible plan was found". In the
            # second case PuLP leaves every variable at 0 -- a vacuous
            # "solution" with objective 0.0, not None -- and extracting it
            # would hand downstream code an empty squad it treats as real.
            raise NoIncumbentError(
                f"time limit ({self.cfg.solver.time_limit_s}s) expired before the "
                f"solver found any feasible plan. Raise the limit, loosen "
                f"mip_gap_rel, or shrink the horizon/candidate pool."
            )
        gap, label = self._diagnostics(label, backend_name)
        return self._extract(label, backend_name, elapsed, gap), self.stats

    def _has_incumbent(self) -> bool:
        """Whether the solver actually loaded a feasible solution.

        Preferred check: HiGHS's own primal_solution_status, which is
        authoritative. Fallback for CBC (whose vacuous case also leaves every
        variable at zero): a loaded plan must own at least one player in the
        first gameweek, since sum(own) == 15 is a hard constraint.
        """
        hm = getattr(self.model, "solverModel", None)
        if hm is not None and hasattr(hm, "getInfo"):
            try:
                return int(hm.getInfo().primal_solution_status) != 0
            except Exception:  # noqa: BLE001 - fall through to the value check
                pass
        return any(
            (v := pulp.value(self.own[i, 0])) is not None and v > 0.5
            for i in range(self.n)
        )

    def _diagnostics(self, label: str, backend: str) -> tuple[float | None, str]:
        """Pull the true solver status and MIP gap out of HiGHS.

        A plan produced at a 30% gap is a guess with a bound attached, not an
        optimum, and the caller has to be able to see which they got. PuLP's
        status string alone does not distinguish them.
        """
        model = getattr(self.model, "solverModel", None)
        if backend != "HiGHS" or model is None:
            return None, label
        try:
            gap = float(model.getInfo().mip_gap)
            status = str(model.modelStatusToString(model.getModelStatus()))
        except Exception:  # noqa: BLE001  # pragma: no cover - highspy versions differ
            return None, label
        tol = self.cfg.solver.mip_gap_rel or 0.0
        if gap > max(tol, 1e-6) * 1.001:
            self.notes.append(
                f"Stopped at a {gap:.2%} optimality gap ({status}); this plan is "
                "the best found, not a proven optimum. Raise time_limit_s, prune "
                "the universe with max_candidates_per_position, or fix the chip "
                "schedule to close it."
            )
        return gap, status

    def _extract(
        self, status: str, backend: str, elapsed: float, gap: float | None
    ) -> HorizonPlan:
        p = self.p
        codes = [row.code for row in p.players]

        def on(var) -> bool:
            return _live(var) and pulp.value(var) is not None and pulp.value(var) > 0.5

        decisions: list[GwDecision] = []
        prev_own = {i for i in range(self.n) if self.prior_own[i]}
        for j, gw in enumerate(self.gws):
            own = {i for i in range(self.n) if on(self.own[i, j])}
            play = {i for i in range(self.n) if on(self.play[i, j])}
            xi = {i for i in range(self.n) if on(self.start[i, j])}
            chip = next((c for c in CHIP_NAMES if on(self.chip.get((c, j), 0))), None)

            bench_gk = [i for i in play - xi if p.players[i].position is Position.GKP]
            bench_out = [i for i in play - xi if p.players[i].position is not Position.GKP]
            if self.cfg.model_bench_order:
                slot1 = [i for i in bench_out if on(self.b1[i, j])]
                slot12 = [i for i in bench_out if on(self.b12[i, j])]
                rest = [i for i in bench_out if i not in slot12]
                order = slot1 + [i for i in slot12 if i not in slot1] + rest
            else:
                order = sorted(bench_out, key=lambda i: (-float(self.xp[i, j]), int(codes[i])))
            bench = tuple(codes[i] for i in bench_gk + order)

            # Under a solver time limit HiGHS can return an integer-feasible
            # incumbent in which a constraint like sum(cap)==1 is satisfied at
            # a tolerance that rounds every cap variable to 0. A missing
            # armband is then repaired deterministically -- highest expected
            # points in the XI, vice the next -- rather than crashing on
            # next() of an empty generator.
            captain = next((i for i in range(self.n) if on(self.cap[i, j])), None)
            vice = next((i for i in range(self.n) if on(self.vice[i, j])), None)
            if captain is None or vice is None or captain == vice:
                ranked = sorted(xi, key=lambda i: (-float(self.xp[i, j]), int(codes[i])))
                if captain is None:
                    captain = ranked[0]
                if vice is None or vice == captain:
                    vice = next(i for i in ranked if i != captain)

            ins = sorted(own - prev_own, key=lambda i: int(codes[i]))
            outs = sorted(prev_own - own, key=lambda i: int(codes[i]))
            decisions.append(
                GwDecision(
                    gw=p.gws[j],
                    squad=tuple(sorted((codes[i] for i in own), key=int)),
                    fielded=tuple(sorted((codes[i] for i in play), key=int)),
                    starting_xi=tuple(sorted((codes[i] for i in xi), key=int)),
                    bench=bench,
                    captain=codes[captain],
                    vice_captain=codes[vice],
                    chip=chip,
                    transfers_in=tuple(codes[i] for i in ins),
                    transfers_out=tuple(codes[i] for i in outs),
                    free_transfers_available=0,
                    hits=0,
                    bank_after=Money(0),
                    squad_value=Money(sum(int(self.price[i, j]) for i in own)),
                )
            )
            prev_own = own

        plan = HorizonPlan(
            season=str(p.season),
            mode=self.cfg.mode,
            decisions=tuple(decisions),
            objective=float(pulp.value(self.model.objective)),
            solver=backend,
            status=status,
            solve_seconds=elapsed,
            mip_gap=gap,
            notes=tuple(self.notes),
        )
        # The MILP's bank and hit variables are only pushed to their true values
        # when the budget binds, so the reported ledger comes from the exact
        # integer-tenths replay instead. The objective-agreement test is what
        # catches any disagreement between the two.
        return _with_exact_ledger(p, plan)


def _with_exact_ledger(problem: HorizonProblem, plan: HorizonPlan) -> HorizonPlan:
    rs = problem.ruleset
    idx = problem.index_of
    bought_at: dict[PlayerCode, Money] = dict(problem.state.holdings)
    bank = problem.initial_bank
    ft = problem.initial_free_transfers
    out = []
    for j, d in enumerate(plan.decisions):
        free_gw = d.chip in _FREE_CHIPS or (
            j == 0 and problem.state.is_preseason and rs.unlimited_before_first_deadline
        )
        if d.chip != "freehit":
            for code in d.transfers_out:
                bank = bank + _sell(bought_at.pop(code), problem, idx[code], j)
            for code in d.transfers_in:
                price = Money(int(problem.price_tenths[idx[code], j]))
                bank = bank - price
                bought_at[code] = price
        n_t = len(d.transfers_in)
        used = 0 if free_gw else min(n_t, ft)
        hits = 0 if free_gw else n_t - used
        out.append(
            GwDecision(
                gw=d.gw,
                squad=d.squad,
                fielded=d.fielded,
                starting_xi=d.starting_xi,
                bench=d.bench,
                captain=d.captain,
                vice_captain=d.vice_captain,
                chip=d.chip,
                transfers_in=d.transfers_in,
                transfers_out=d.transfers_out,
                free_transfers_available=ft,
                hits=hits,
                bank_after=bank,
                squad_value=d.squad_value,
            )
        )
        ft = min(rs.max_banked_ft, ft - used + rs.free_per_gw)
    return HorizonPlan(
        season=plan.season,
        mode=plan.mode,
        decisions=tuple(out),
        objective=plan.objective,
        solver=plan.solver,
        status=plan.status,
        solve_seconds=plan.solve_seconds,
        mip_gap=plan.mip_gap,
        notes=plan.notes,
    )


def _sell(bought: Money, problem: HorizonProblem, i: int, j: int) -> Money:
    from fpl_edge.types import selling_price

    return selling_price(bought, Money(int(problem.price_tenths[i, j])))


def _make_solver(config: OptimizerConfig, notes: list[str]):
    cfg = config.solver
    if cfg.backend is SolverBackend.HIGHS:
        try:
            solver = pulp.HiGHS(
                msg=cfg.msg,
                timeLimit=cfg.time_limit_s,
                gapRel=cfg.mip_gap_rel,
                threads=cfg.threads,
                random_seed=cfg.seed,
            )
            if solver.available():
                return solver, "HiGHS"
            notes.append("HiGHS reported itself unavailable; fell back to CBC.")
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - environment
            notes.append(f"HiGHS failed to initialise ({exc}); fell back to CBC.")
    return (
        pulp.PULP_CBC_CMD(
            msg=cfg.msg,
            timeLimit=cfg.time_limit_s,
            gapRel=cfg.mip_gap_rel,
            threads=cfg.threads,
            options=[f"randomSeed {cfg.seed}"],
        ),
        "CBC",
    )
