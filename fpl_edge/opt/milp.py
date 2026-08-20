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
from fpl_edge.opt.interfaces import RankInputsUnavailableError, RankUtilityUnavailableError
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
    rank_mv: object | None = None,
    return_stats: bool = False,
) -> HorizonPlan | tuple[HorizonPlan, ModelStats]:
    """Optimise a squad plan over the problem's gameweek horizon.

    ``rank_mv`` is a :class:`~fpl_edge.rank.coefficients.RankCoefficients`,
    required by :data:`ObjectiveMode.RANK_MV` and ignored otherwise.
    """
    _guard_mode(config, rank_utility=rank_utility, rank_mv=rank_mv)
    problem = problem.prune(
        config.max_candidates_per_position, protect=config.locked | config.banned
    )
    builder = _Builder(problem, config, rank=rank_mv)
    plan, stats = builder.solve()
    return (plan, stats) if return_stats else plan


#: No-good cut criteria for :func:`enumerate_plans`. The two the public SOTA's
#: users actually run (``solver_state_of_art.md`` §6.5).
CUT_CRITERIA = ("this_gw_transfer_in_out", "this_gw_lineup", "chip_gws")


def enumerate_plans(
    problem: HorizonProblem,
    config: OptimizerConfig,
    *,
    k: int = 3,
    criterion: str = "this_gw_transfer_in_out",
    difference: int = 1,
    rank_utility: object | None = None,
    rank_mv: object | None = None,
) -> list[HorizonPlan]:
    """The best plan and the ``k-1`` best genuinely-different alternatives.

    Adopted from the public SOTA's ``num_iterations`` (§1 of
    ``solver_state_of_art.md``, mechanics in §6.5): solve, add a **no-good cut**
    forbidding the incumbent's decision, re-solve. Each returned plan therefore
    differs from every earlier one by at least ``difference`` units of the
    chosen criterion, and the objective values are directly comparable because
    they all come from one model.

    This is the deliverable a weekly report can actually use. A single argmax
    hides whether it won by 0.05 points or by 3, and a rank-aware objective
    makes that worse rather than better: near the switch boundary two very
    different squads can score within noise of each other, and the honest output
    is both of them plus the margin. :func:`fpl_edge.rank.validate.validate_plans`
    then re-runs the list on common random numbers and attaches a paired
    ``Delta P(top 10k)`` with a standard error -- which is the number that
    decides, not the MILP's surrogate objective.

    Note what this is NOT: the SOTA's ``randomized`` heuristic, which perturbs
    the projections and resamples argmaxes. §6 of the SOTA doc records why that
    is not adopted -- an ensemble of argmaxes of perturbed means is a weaker and
    biased substitute for evaluating plans under a distribution, and we have a
    real distribution to evaluate them under.
    """
    if criterion not in CUT_CRITERIA:
        raise ValueError(f"unknown cut criterion {criterion!r}; expected one of {CUT_CRITERIA}")
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")
    if difference < 1:
        raise ValueError(f"difference must be at least 1, got {difference}")

    _guard_mode(config, rank_utility=rank_utility, rank_mv=rank_mv)
    problem = problem.prune(
        config.max_candidates_per_position, protect=config.locked | config.banned
    )
    builder = _Builder(problem, config, rank=rank_mv)

    plans: list[HorizonPlan] = []
    # Notes accumulate across re-solves: the build-time notes are shared by
    # every plan, the cut notes are the running record of what each plan was
    # forbidden from repeating, and per-solve notes (gap warnings) belong only
    # to the plan that produced them. Keeping the first two and discarding the
    # third is what makes each returned plan's ``notes`` an honest account of
    # how it was reached.
    keep = list(builder.notes)
    for iteration in range(k):
        builder.notes[:] = keep
        try:
            plan, _ = builder.solve()
        except (InfeasibleError, NoIncumbentError):
            if not plans:
                raise
            break
        plans.append(plan)
        if iteration + 1 < k:
            builder.notes[:] = keep
            builder.add_no_good_cut(plan, criterion, iteration, difference)
            keep = list(builder.notes)
    return plans


def _guard_mode(
    config: OptimizerConfig, *, rank_utility: object | None, rank_mv: object | None
) -> None:
    """The mode preconditions, shared by every entry point."""
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
            "RANK_UTILITY puts the simulator inside the argmax loop (formulation F1), "
            "and docs/platform/rank_objectives.md section 8.2 concludes it does not "
            "belong there: F1 is a VALIDATOR, not a searcher. Section 7.4 is the "
            "measurement -- F1's Monte Carlo could not separate 0 of 6 candidate swaps "
            "at 2 SE, because the objective is a sum of indicators whose gradients are "
            "zero and whose noise swamps exactly the small deltas that decide real "
            "transfers. The implemented rank objective is ObjectiveMode.RANK_MV "
            "(formulation F2): per-player coefficients mu + theta(1-2*share)*sigma^2 "
            "with theta(D, tau) set by fpl_edge.rank.policy. Choose with RANK_MV, then "
            "check the shortlist with fpl_edge.rank.validate.validate_plans(), which "
            "runs this simulator on common random numbers and reports PAIRED "
            "delta P(top 10k) -- the one thing F1 is genuinely best at."
        )
    if config.mode is ObjectiveMode.RANK_MV and rank_mv is None:
        raise RankInputsUnavailableError(
            "ObjectiveMode.RANK_MV needs RankCoefficients (see "
            "fpl_edge.rank.coefficients.build_rank_coefficients): a RankState, "
            "per-player variances, and the near-threshold cohort's ownership and "
            "captaincy shares. Refusing to run: dropping the rank terms would leave "
            "expected points reported under a rank objective's name."
        )


class _Builder:
    def __init__(
        self,
        problem: HorizonProblem,
        config: OptimizerConfig,
        *,
        rank: object | None = None,
    ) -> None:
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
        self.rank = rank
        # The two coefficient matrices the objective is written against. Under
        # EXPECTED_POINTS both are exactly ``problem.xpts``, so every term below
        # is unchanged, term for term, from before RANK_MV existed.
        self.obj_coef, self.cap_coef = self._objective_coefficients()

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

    def _objective_coefficients(self) -> tuple[np.ndarray, np.ndarray]:
        """``(lineup, captain)`` per-player coefficients for the declared mode.

        ``EXPECTED_POINTS`` returns ``problem.xpts`` twice: a player is worth his
        expected points whether he starts or wears the armband, and the armband
        is priced by the multiplier in the objective rather than by a different
        coefficient.

        ``RANK_MV`` returns the two F2 matrices, which differ from each other
        and from xPts:

            lineup:  ``mu_p + theta (1 - 2 own_share_p)     sigma_p^2``
            captain: ``mu_p + theta (1 - 2 captain_share_p) sigma_p^2``

        The captaincy matrix carries the flipped-variance term against the
        *captaincy* share, per ``rank_objectives.md`` §4: the armband competes
        with the cohort's armbands, not with their squads, and captaincy is far
        more concentrated than ownership, so the two crossovers sit in very
        different places. Cross-player covariance is not represented here at
        all; it enters through Sigma inside theta (see
        :meth:`RankCoefficients.covariance_note`).
        """
        if self.cfg.mode is not ObjectiveMode.RANK_MV:
            return self.xp, self.xp
        if self.rank is None:  # pragma: no cover - solve_horizon guards this
            raise RankInputsUnavailableError("RANK_MV requires RankCoefficients")
        lineup, captain = self.rank.align(self.p)  # type: ignore[attr-defined]
        self.notes.append(self.rank.describe())    # type: ignore[attr-defined]
        self.notes.append(self.rank.pace_note())   # type: ignore[attr-defined]
        self.notes.append(self.rank.covariance_note())  # type: ignore[attr-defined]
        return np.asarray(lineup, dtype=np.float64), np.asarray(captain, dtype=np.float64)

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
        # Caller-supplied chip scheduling windows, on top of the rule registry's
        # availability windows. These only ever REMOVE options (SOTA §1.7):
        # `allowed_chip_gws` restricts where a chip may fire, `no_chip_gws`
        # blocks whole gameweeks, and `forced_chip_gws` additionally requires
        # that the chip fire in exactly one of the named weeks.
        allowed_gws = {
            str(c): {int(g) for g in gws}
            for c, gws in (self.cfg.allowed_chip_gws or {}).items()
        }
        forced_gws = {
            str(c): {int(g) for g in gws}
            for c, gws in (self.cfg.forced_chip_gws or {}).items()
        }
        blocked_gws = {int(g) for g in self.cfg.no_chip_gws}
        for name in (*allowed_gws, *forced_gws):
            if name not in CHIP_NAMES:
                raise ValueError(f"unknown chip {name!r}; expected one of {CHIP_NAMES}")

        self.chip = {}
        for chip in CHIP_NAMES:
            windows = allowed_gws.get(chip)
            forced = forced_gws.get(chip)
            for j, gw in enumerate(self.gws):
                if chip not in self.cfg.allowed_chips:
                    continue
                half = rs.chip_half_of(chip, gw)
                if half is None:
                    continue  # outside the availability window (this is the GW1 WC/FH lockout)
                if p.state.chips.remaining_in_half(chip, rs, half) < 1:
                    continue
                if gw in blocked_gws:
                    continue
                if windows is not None and gw not in windows:
                    continue
                if forced is not None and gw not in forced:
                    continue
                self.chip[chip, j] = self._var(f"chip_{chip}_{j}", 0, 1, "Binary")

        for chip, weeks in forced_gws.items():
            live = [v for (c, j), v in self.chip.items() if c == chip]
            if not live:
                raise InfeasibleError(
                    f"forced_chip_gws requires {chip!r} in one of {sorted(weeks)}, but "
                    "none of those gameweeks is inside the horizon, inside the chip's "
                    "availability window, and unspent. Nothing can satisfy that."
                )
            self.model += pulp.lpSum(live) == 1, f"chip_{chip}_forced"

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

        # Locked and banned players (SOTA §6.3). Bounds rather than constraints:
        # the same statement, one fewer row, and the LP relaxation sees it
        # immediately. A locked player who is not ownable, or a banned player
        # who is locked, is a contradiction the caller must resolve, so both are
        # raised rather than silently dropped.
        known = {int(row.code) for row in p.players}
        for code in sorted(self.cfg.locked):
            if int(code) not in known:
                raise ValueError(
                    f"locked player {code} is not in the universe. If pruning removed "
                    "him, note that solve_horizon already adds locked and banned "
                    "players to the safe-list, so this is a universe problem."
                )
        for code in sorted(self.cfg.banned):
            if int(code) not in known:
                raise ValueError(f"banned player {code} is not in the universe")
        locked_idx = [i for i, row in enumerate(p.players) if int(row.code) in self.cfg.locked]
        banned_idx = [i for i, row in enumerate(p.players) if int(row.code) in self.cfg.banned]
        for i in locked_idx:
            for j in range(T):
                if not p.ownable[i, j]:
                    raise InfeasibleError(
                        f"player {p.players[i].code} is locked but not ownable in "
                        f"GW{self.gws[j]}"
                    )
                self.own[i, j].lowBound = 1
        for i in banned_idx:
            for j in range(T):
                self.own[i, j].upBound = 0
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
        # With the banked-FT potential switched on, the carryover chain is
        # extended one step past the horizon: ft[T] is the number of free
        # transfers the plan leaves BEHIND it. That variable is the entire point
        # of the term -- without it, leftover transfers are worth zero and the
        # model spends them all in the last modelled gameweek, which is a
        # truncation artefact rather than a decision (SOTA §6.1).
        self._ft_last = T if self.cfg.ft_value_list is not None else T - 1
        self.ft = {
            j: self._var(f"ft_{j}", rs.free_per_gw, rs.max_banked_ft, "Integer")
            for j in range(1, self._ft_last + 1)
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

        for j in range(1, self._ft_last + 1):
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
        # Under RANK_MV a coefficient can be negative even where xPts is not:
        # a high-ownership player with large variance carries a negative
        # variance credit when theta > 0. So the aggregation test is on the
        # coefficients that actually appear in the objective, not on xPts.
        aggregate_chips = bool(min(self.obj_coef.min(), self.cap_coef.min()) >= 0.0)
        if not aggregate_chips:
            self.notes.append(
                "Negative objective coefficients present: chip uplifts use the "
                "per-player linearisation, which is slower to solve."
            )

        for j in range(T):
            d = self.discount[j]
            bb = self.chip.get(("bboost", j), 0)
            tc = self.chip.get(("3xc", j), 0)
            col = self.obj_coef[:, j]
            # Ceilings for the chip uplifts. Triple Captain can only ever add
            # one player's coefficient; Bench Boost only ever four. Bounding by
            # the whole universe, as a naive big-M would, leaves an LP
            # relaxation that plays every chip fractionally for free.
            max_one = max(1.0, float(self.cap_coef[:, j].max()))
            n_bench = rs.squad_size - rs.starting_xi
            max_bench = max(1.0, float(np.sort(col)[-n_bench:].sum()))

            # starting XI
            terms += [d * float(self.obj_coef[i, j]) * self.start[i, j] for i in range(n)]
            # captain: doubling a player who blanks doubles zero, so the uplift
            # is exactly the coefficient with no minutes term needed. Under
            # RANK_MV this is where the flipped-variance term against CAPTAINCY
            # share enters, which is why it reads cap_coef and the XI reads
            # obj_coef.
            terms += [
                d * (rs.captain_multiplier - 1) * float(self.cap_coef[i, j]) * self.cap[i, j]
                for i in range(n)
            ]

            # ---- Triple Captain: one extra captain multiple ----------------
            if _live(tc):
                gain = rs.triple_captain_multiplier - rs.captain_multiplier
                captain_xp = pulp.lpSum(
                    float(self.cap_coef[i, j]) * self.cap[i, j] for i in range(n)
                )
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
                        terms.append(d * gain * float(self.cap_coef[i, j]) * v)

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
                # The vice inherits the armband when the captain blanks, so he
                # is priced on the captaincy coefficient, not the lineup one.
                terms.append(d * (rs.captain_multiplier - 1) * float(self.cap_coef[i, j]) * v)

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
                terms.append(d * float(self.obj_coef[i, j]) * base)
                if _live(bb):
                    gap = float(self.obj_coef[i, j]) * (benched - base)
                    if aggregate_chips:
                        bb_gap.append(gap)
                    else:
                        v = self._var(f"bbx_{i}_{j}", 0, 1)
                        self.bb_extra[i, j] = v
                        self.model += v <= bb, f"bbx_chip_{i}_{j}"
                        self.model += v <= benched - base, f"bbx_gap_{i}_{j}"
                        self.model += v >= (benched - base) - (1 - bb), f"bbx_and_{i}_{j}"
                        terms.append(d * float(self.obj_coef[i, j]) * v)
            if _live(bb) and aggregate_chips:
                v = self._var(f"bbv_{j}", 0)
                self.model += v <= max_bench * bb, f"bbv_chip_{j}"
                self.model += v <= pulp.lpSum(bb_gap), f"bbv_gap_{j}"
                terms.append(d * v)

            terms.append(d * rs.hit_cost * self.paid[j])  # hit_cost is negative

        terms += self._banked_ft_terms()
        return pulp.lpSum(terms)

    def _banked_ft_terms(self) -> list:
        """The telescoping banked-free-transfer potential (SOTA §1.3, §6.1).

        ``V(s)`` is the value of *holding* s free transfers, built by
        accumulating the marginal values in ``ft_value_list``. The objective
        adds ``V(ft[j]) - V(ft[j-1])`` per gameweek, so banking a transfer earns
        the marginal state value once and spending it pays exactly that back --
        a potential function, not a standing bonus. Undiscounted the sum
        telescopes to ``V(ft_terminal) - V(ft_initial)``, which is precisely the
        horizon-truncation correction it is there to be; with a discount the
        per-gameweek form additionally prefers banking *late*, which is also
        right, because a transfer banked in the last modelled week is the one
        most likely to survive into the unmodelled future.

        ``V`` is concave over the reachable range (marginal values 2.0, 1.6,
        1.3, 1.1 from state 1), so it is represented exactly at integer points
        by the lower envelope of its chords -- no one-hot binaries needed, which
        keeps the branch-and-bound tree the size it was.
        """
        if self.cfg.ft_value_list is None:
            return []
        lo, hi = self.rs.free_per_gw, self.rs.max_banked_ft
        values = self.cfg.ft_state_values(lo, hi)
        self.notes.append(
            "Banked-FT terminal value is ON: "
            + ", ".join(f"V({s})={values[s]:.2f}" for s in sorted(values))
            + ". Community-tuned constants (solver_state_of_art.md §1.3), not "
            "recalibrated against our simulator."
        )

        potential: dict[int, object] = {}
        for j in self.ft:
            v = self._var(f"ftv_{j}", None, values[hi])
            for s in range(lo, hi):
                slope = values[s + 1] - values[s]
                self.model += (
                    v <= values[s] + slope * (self.ft[j] - s),
                    f"ftv_chord_{j}_{s}",
                )
            potential[j] = v

        terms = []
        for j in sorted(potential):
            prev = potential[j - 1] if (j - 1) in potential else values[
                min(max(self.prior_ft, lo), hi)
            ]
            # Discount by the gameweek the transfer is banked INTO, which for
            # ft[j] is horizon index j (or the terminal step, which reuses the
            # last modelled discount).
            d = self.discount[min(j, self.T - 1)]
            terms.append(d * (potential[j] - prev))
        return terms

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

    def add_no_good_cut(
        self, plan: HorizonPlan, criterion: str, tag: int, difference: int = 1
    ) -> None:
        """Forbid ``plan``'s decision, so the next solve must find another.

        A no-good cut on a set ``A`` of binaries that the incumbent set to 1 is
        ``sum(A) <= |A| - difference``: any new solution must turn at least
        ``difference`` of them off. That is a valid cut only because every
        member of ``A`` really is 1 in the incumbent, so it removes exactly the
        incumbent (and its near-neighbours) and no optimum better than it.

        The criteria differ in what "another plan" means, which is a real
        choice: cutting on transfers gives alternative *moves*, cutting on the
        lineup gives alternative *teams*, cutting on chips gives alternative
        *schedules*. An empty set is not cuttable -- there is nothing to switch
        off -- so a criterion that produces one falls back to the lineup, the
        same auto-fallback the public solver uses when a forced Free Hit leaves
        it no transfers to cut on.
        """
        d = plan.decisions[0]
        idx = self.p.index_of
        chosen: list[object] = []
        label = criterion

        if criterion == "this_gw_transfer_in_out":
            chosen = [self.buy[idx[c], 0] for c in d.transfers_in]
            chosen += [self.sell[idx[c], 0] for c in d.transfers_out if (idx[c], 0) in self.sale_value]
        elif criterion == "chip_gws":
            chosen = [
                self.chip[c, j]
                for j, dec in enumerate(plan.decisions)
                for c in CHIP_NAMES
                if dec.chip == c and (c, j) in self.chip
            ]

        if not chosen:
            label = "this_gw_lineup"
            chosen = [self.start[idx[c], 0] for c in d.starting_xi]

        k = min(int(difference), len(chosen))
        self.model += (
            pulp.lpSum(chosen) <= len(chosen) - k,
            f"nogood_{label}_{tag}",
        )
        self.notes.append(
            f"no-good cut #{tag + 1} on {label}: at least {k} of "
            f"{len(chosen)} incumbent decisions must change."
        )

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
                order = sorted(
                    bench_out, key=lambda i: (-float(self.obj_coef[i, j]), int(codes[i]))
                )
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
                # Repaired on the armband's own coefficient, so a RANK_MV repair
                # picks the rank-optimal captain rather than the max-EV one.
                ranked = sorted(xi, key=lambda i: (-float(self.cap_coef[i, j]), int(codes[i])))
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
