"""What to do at the upcoming deadline, what it costs, and what lost.

A transfer recommendation that says only "sell A, buy B" is not usable. The
question a manager is actually asking is "is that worth four points?", and
answering it needs three numbers: the value of doing nothing, the value of the
best move, and the value of the *next* best move. This module produces all
three, from the optimiser, using the optimiser's own scorer.

How the alternatives are generated without touching the MILP
------------------------------------------------------------
:class:`~fpl_edge.opt.problem.HorizonProblem` already carries an ``ownable``
mask -- "may this player be in the squad this gameweek". Constraining a candidate
move to a *specific* squad is therefore just a mask: make everyone un-ownable
except the fifteen that move implies, and the solver returns the best XI,
captain, bench order and chip for exactly that squad, scored by exactly the same
objective as the free solve. The comparison is like-for-like by construction, and
there is no second implementation of the objective to drift.

The candidate moves are screened cheaply first (affordable, position-compatible,
inside the three-per-club limit, and a positive horizon xPts upgrade) because a
MILP solve per candidate over 592 players is not free. The screen is a filter on
which moves get *evaluated*, never on which wins: everything that survives is
solved and scored properly.

The objective
-------------
``RANK_UTILITY`` is what this engine is for and is the default. It needs the
simulator's :class:`~fpl_edge.opt.interfaces.RankUtilityProvider`, which is not
built yet, so asking for it raises
:class:`~fpl_edge.opt.interfaces.RankUtilityUnavailableError`. That is the
correct behaviour and it is not a placeholder to be filled in with means: the
whole premise of the project is that maximising expected points is the wrong
objective, so silently answering an unavailable rank question with a points
answer would be the single worst bug this module could contain.
``EXPECTED_POINTS`` is available, must be asked for by name, and every rendering
of a recommendation made under it says so.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np

from fpl_edge.myteam.forecast import PointsForecastUnavailableError
from fpl_edge.myteam.state import MyTeamState, PlayerIndex, ReconstructionError
from fpl_edge.opt import (
    ChipState,
    HorizonPlan,
    HorizonProblem,
    InfeasibleError,
    ObjectiveMode,
    OptimizerConfig,
    Ruleset,
    SquadState as OptSquadState,
    build_problem,
    solve_horizon,
)
from fpl_edge.opt.interfaces import RankUtilityUnavailableError
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Money, PlayerCode, Position, Season, selling_price

#: How many screened candidate moves get a full MILP solve. Raising it costs one
#: solve each and can only improve the answer; the default is a speed choice, and
#: it is reported on the recommendation so a reader knows the search was bounded.
DEFAULT_CANDIDATES = 12


class NoSquadError(ReconstructionError):
    """Asked for a transfer recommendation without knowing the current squad."""


@dataclass(frozen=True, slots=True)
class Move:
    """One candidate action at the deadline, solved and scored."""

    out: tuple[int, ...]
    into: tuple[int, ...]
    objective: float
    hits: int
    bank_after: Money
    plan: HorizonPlan
    label: str = ""
    infeasible_reason: str = ""

    @property
    def n_transfers(self) -> int:
        return len(self.into)

    @property
    def hit_points(self) -> int:
        return self.hits * 4

    @property
    def is_roll(self) -> bool:
        return self.n_transfers == 0

    def describe(self, index: PlayerIndex) -> str:
        if self.is_roll:
            return "roll the transfer (no move)"
        name = lambda c: index.name.get(int(c), str(c))  # noqa: E731
        pairs = ", ".join(
            f"{name(o)} -> {name(i)}" for o, i in zip(sorted(self.out), sorted(self.into))
        )
        return pairs


@dataclass(frozen=True, slots=True)
class HitVerdict:
    """One move's hit, judged against the rank break-even rather than against 4.

    ``rank_objectives.md`` §5: a hit costs 4 points with certainty, buys ``g``
    expected points, and changes effective weekly volatility from ``s`` to
    ``s'``. The break-even is ``g* = 4 + L(S' - S)/S``, not 4 -- behind, a hit
    that *loses* expected points can be correct; ahead, a variance-buying hit
    must clear a much higher bar while a variance-shedding one gets cheap.
    """

    label: str
    hits: int
    hit_points: int
    #: Expected-points gain over rolling, GROSS of the hit. That is the quantity
    #: §5's threshold is defined on; the objective's own value already nets the
    #: hit and (under RANK_MV) the variance term, so neither is comparable to g*.
    expected_gain: float
    #: ``g*`` -- the break-even total gain in this state.
    breakeven_gain: float
    #: Effective weekly SD after the move, from the relative-variance delta the
    #: rank coefficients imply. Equals the incumbent ``s`` when unknown.
    s_weekly_after: float
    justified: bool
    #: Value of the free transfer the hit forfeits, netted off the gain (§5's
    #: closing caveat). Zero when the banked-FT term is off.
    ft_option_value: float = 0.0
    #: The move being judged, so a verdict is readable and checkable on its own.
    into: tuple[int, ...] = ()
    out: tuple[int, ...] = ()

    def describe(self) -> str:
        verdict = "JUSTIFIED" if self.justified else "NOT justified"
        return (
            f"{self.label or 'chosen'}: -{self.hit_points} for {self.expected_gain:+.2f} xP "
            f"(net of {self.ft_option_value:.2f} FT option) against a rank break-even of "
            f"{self.breakeven_gain:+.2f} -- {verdict}. "
            f"Points logic would demand +4.00; the state moves the bar to "
            f"{self.breakeven_gain:+.2f} (s {self.s_weekly_after:.2f}/wk after the move)."
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "hits": self.hits,
            "hit_points": self.hit_points,
            "expected_gain": self.expected_gain,
            "breakeven_gain": self.breakeven_gain,
            "s_weekly_after": self.s_weekly_after,
            "ft_option_value": self.ft_option_value,
            "justified": self.justified,
        }


@dataclass(frozen=True, slots=True)
class TransferRecommendation:
    """The move to make, what it costs, and the ones that lost."""

    season: str
    gw: GwId
    mode: ObjectiveMode
    horizon: tuple[GwId, ...]
    chosen: Move
    roll: Move | None
    alternatives: tuple[Move, ...]
    free_transfers: int
    #: True before the first deadline, when transfers are unlimited and free and
    #: the "how many free transfers do I have" question does not apply.
    unlimited_transfers: bool = False
    notes: tuple[str, ...] = ()
    n_candidates_screened: int = 0
    n_candidates_solved: int = 0
    solve_seconds: float = 0.0

    # -- rank layer (empty unless the recommendation was solved rank-aware) ---

    #: The ``(D, tau)`` state the recommendation was solved at, with its
    #: provenance. §7.2 is why this is carried rather than logged: a verdict
    #: quoted without its state overreaches, and the study's own example of that
    #: mistake is in this repo.
    rank_state: object | None = None
    #: One verdict per move that carries a hit, judged against §5's ``g*``.
    hit_verdicts: tuple[HitVerdict, ...] = ()
    #: F1 paired-CRN validation of the shortlist, if a simulator was supplied.
    #: Baseline is the chosen move; every other entry carries a paired
    #: ``Delta P(top 10k)`` and its standard error.
    alternatives_with_delta_p: tuple[object, ...] = ()
    #: Value of the free transfers the chosen plan leaves banked past the
    #: horizon. Zero when the banked-FT term is off.
    banked_ft_value: float = 0.0

    def rank_summary(self) -> str:
        """The state, the posture it implies, and what it changed."""
        if self.rank_state is None:
            return (
                "No rank state: this recommendation was solved without one, so "
                "its risk posture is whatever the objective's fixed coefficients "
                "encode rather than a response to where you stand."
            )
        from fpl_edge.rank.policy import should_gamble, theta

        state = self.rank_state
        posture = (
            "behind on expectation -- variance is a GOOD and theta prices it positively"
            if should_gamble(state)
            else "ahead on expectation -- variance is a COST and theta prices it negatively"
        )
        return f"{state.describe()}\n  theta = {theta(state):+.5f} per point^2; {posture}."

    @property
    def gain_over_roll(self) -> float | None:
        if self.roll is None:
            return None
        return self.chosen.objective - self.roll.objective

    def hit_verdict(self) -> str:
        """Whether the recommended move justifies the points it costs.

        The objective already has the hit subtracted, so a move that carries one
        and still wins has paid for itself *in the model's terms*. Saying that
        out loud, with the margin, is the point: a 0.3-point edge over rolling is
        not a reason to take a -4, it is noise in a forecast.
        """
        if self.unlimited_transfers:
            return (
                "Free: this is before the first deadline of the season, so you can "
                "change as many players as you like at no cost."
            )
        if self.chosen.hits == 0:
            return "No hit: the move fits inside your free transfers."
        gain = self.gain_over_roll
        if gain is None:
            return f"Costs {self.chosen.hit_points} points; nothing to compare it against."
        cost = self.chosen.hit_points
        if gain <= 0:
            return (
                f"Costs {cost} points and does NOT beat rolling ({gain:+.2f}). "
                "Do not take this hit."
            )
        if gain < 1.0:
            return (
                f"Costs {cost} points and beats rolling by only {gain:+.2f} over the "
                f"horizon. That margin is inside the forecast's own error. Roll."
            )
        return (
            f"Costs {cost} points and still beats rolling by {gain:+.2f} over the "
            f"horizon, so the hit is already paid for in this objective."
        )

    def render(self, index: PlayerIndex) -> str:
        headline = (
            f"Recommended squad for GW{int(self.gw)} — "
            f"{self.chosen.n_transfers} change(s) from what you have"
            if self.unlimited_transfers
            else f"Recommended for GW{int(self.gw)}: {self.chosen.describe(index)}"
        )
        budget = (
            "unlimited transfers before the first deadline"
            if self.unlimited_transfers
            else f"{self.free_transfers} free transfer(s) available"
        )
        lines = [headline]
        if self.unlimited_transfers and not self.chosen.is_roll:
            lines.append("  " + self.chosen.describe(index))
        lines += [
            f"  {budget}; {self.chosen.n_transfers} change(s) proposed; "
            f"{self.chosen.hit_points} point hit.",
            f"  {self.hit_verdict()}",
            f"  bank after: {self.chosen.bank_after}",
        ]
        if self.roll is not None and not self.chosen.is_roll:
            keep = "keeping your squad" if self.unlimited_transfers else "rolling"
            lines.append(
                f"  objective {self.chosen.objective:.2f} vs {self.roll.objective:.2f} "
                f"for {keep} ({self.gain_over_roll:+.2f})"
            )
        if self.alternatives:
            lines.append("")
            lines.append("Alternatives that lost:")
            for alt in self.alternatives:
                delta = alt.objective - self.chosen.objective
                hit = f", -{alt.hit_points}" if alt.hits else ""
                lines.append(
                    f"  {alt.describe(index):<44} {alt.objective:8.2f} ({delta:+.2f}{hit})"
                )
        lines.append("")
        lines.append(
            f"Objective: {self.mode.value} over "
            f"GW{int(self.horizon[0])}-{int(self.horizon[-1])}. "
            f"{self.n_candidates_solved} of {self.n_candidates_screened} screened "
            f"moves were solved in full ({self.solve_seconds:.1f}s)."
        )
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


# -- candidate generation ----------------------------------------------------


def prune_keeping(
    problem: HorizonProblem, max_per_position: int | None, keep: Sequence[int]
) -> HorizonProblem:
    """Prune the universe, but never drop a player in ``keep``.

    :meth:`HorizonProblem.prune` protects the players in ``state.holdings``,
    which is exactly right in general and exactly wrong before the first
    deadline: there the optimiser's state is legitimately empty (transfers are
    unlimited and the whole budget is unspent), so the manager's actual fifteen
    are not protected and can be pruned out from under the comparison. Every
    alternative is then measured against a squad that is not in the universe.
    """
    if max_per_position is None:
        return problem
    protected = {int(c) for c in keep} | {int(c) for c in problem.state.holdings}
    total = problem.xpts.sum(axis=1)
    chosen: set[int] = {
        i for i, p in enumerate(problem.players) if int(p.code) in protected
    }
    for pos in Position:
        idx = [i for i, p in enumerate(problem.players) if p.position is pos]
        idx.sort(key=lambda i: (-total[i], int(problem.players[i].code)))
        chosen.update(idx[:max_per_position])
    order = sorted(chosen)
    return HorizonProblem(
        season=problem.season,
        gws=problem.gws,
        players=tuple(problem.players[i] for i in order),
        price_tenths=problem.price_tenths[order],
        xpts=problem.xpts[order],
        p_play=problem.p_play[order],
        ownable=problem.ownable[order],
        state=problem.state,
        ruleset=problem.ruleset,
    )


def _mask_to_squad(problem: HorizonProblem, squad: Sequence[int]) -> HorizonProblem:
    """A copy of the problem in which only ``squad`` may be owned.

    Uses the existing ``ownable`` mask rather than adding a constraint to the
    MILP, so the alternative is solved by exactly the same model as the free
    optimum. The state is left alone: the transfer accounting, sell-on fees and
    hit costs are still computed by the optimiser from the real holdings.
    """
    want = {int(c) for c in squad}
    keep = np.array([int(p.code) in want for p in problem.players], dtype=bool)
    if int(keep.sum()) != len(want):
        missing = want - {int(p.code) for p in problem.players}
        raise InfeasibleError(f"players missing from the universe: {sorted(missing)}")
    ownable = problem.ownable & keep[:, None]
    return HorizonProblem(
        season=problem.season,
        gws=problem.gws,
        players=problem.players,
        price_tenths=problem.price_tenths,
        xpts=problem.xpts,
        p_play=problem.p_play,
        ownable=ownable,
        state=problem.state,
        ruleset=problem.ruleset,
    )


def screen_moves(
    problem: HorizonProblem,
    state: MyTeamState,
    *,
    limit: int = DEFAULT_CANDIDATES,
    max_transfers: int = 2,
) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Cheap shortlist of single (and paired) transfers worth solving properly.

    Ranked by the crude thing a MILP would compute exactly: horizon xPts gained,
    minus the hit if the move needs one. Only a shortlist -- everything returned
    is then solved and scored for real, and the screen's ranking has no say in
    which wins.
    """
    idx = problem.index_of
    held = [int(p.code) for p in (state.picks or ())]
    if not held:
        raise NoSquadError("cannot screen transfers without a current squad")

    total_xp = problem.xpts.sum(axis=1)
    price_first = problem.price_tenths[:, 0]
    by_code = {int(p.code): p for p in problem.players}
    rs = problem.ruleset

    club_count: dict[int, int] = {}
    for code in held:
        club = int(by_code[code].team_code)
        club_count[club] = club_count.get(club, 0) + 1

    # Money available if we sell exactly one player: bank + his selling price.
    bank = state.bank_tenths
    sale = {
        code: selling_price(
            Money(state.bought_at[code]), Money(int(price_first[idx[PlayerCode(code)]]))
        ).tenths
        for code in held
    }

    owned = set(held)
    scored: list[tuple[float, tuple[int, ...], tuple[int, ...]]] = []
    for out_code in held:
        out_i = idx[PlayerCode(out_code)]
        out_pos = by_code[out_code].position
        out_club = int(by_code[out_code].team_code)
        budget = bank + sale[out_code]
        for in_i, row in enumerate(problem.players):
            in_code = int(row.code)
            if in_code in owned or row.position is not out_pos:
                continue
            if not bool(problem.ownable[in_i, 0]):
                continue
            if int(price_first[in_i]) > budget:
                continue
            club = int(row.team_code)
            after = club_count.get(club, 0) + (0 if club == out_club else 1)
            if after > rs.max_per_club:
                continue
            gain = float(total_xp[in_i] - total_xp[out_i])
            if gain <= 0:
                continue
            scored.append((gain, (out_code,), (in_code,)))

    scored.sort(key=lambda row: (-row[0], row[1], row[2]))
    shortlist = [(o, i) for _gain, o, i in scored[:limit]]

    if max_transfers >= 2 and len(scored) >= 2:
        # One paired move: the two best single upgrades that do not collide.
        # A full pairwise search is O(n^2) solves for a decision that is almost
        # always one transfer; this is enough to show the -8 on the table.
        best: list[tuple[float, int, int]] = []
        used_out: set[int] = set()
        used_in: set[int] = set()
        for gain, o, i in scored:
            if o[0] in used_out or i[0] in used_in:
                continue
            best.append((gain, o[0], i[0]))
            used_out.add(o[0])
            used_in.add(i[0])
            if len(best) == 2:
                break
        if len(best) == 2:
            pair = ((best[0][1], best[1][1]), (best[0][2], best[1][2]))
            if pair not in shortlist:
                shortlist.append(pair)
    return shortlist


# -- the recommendation ------------------------------------------------------


def is_before_first_deadline(state: MyTeamState) -> bool:
    """True while the squad can still be rebuilt from scratch for free.

    Transfers before the first deadline of the season are unlimited and free
    (``transfers.unlimited_before_first_deadline``). So the honest GW1 question
    is not "which one player should I change?" but "is this the squad you want,
    and if not, here is a better one, at no cost". Treating GW1 like any other
    week would invent -4 hits that the game would never charge.
    """
    from fpl_edge.rules import rules

    return int(state.gw) == 1 and bool(
        rules().get("transfers.unlimited_before_first_deadline")
    )


def build_state(state: MyTeamState) -> OptSquadState:
    """The optimiser's view of the current holdings, with purchase prices.

    Before the first deadline this deliberately reports *no* holdings, which is
    what :class:`~fpl_edge.opt.problem.SquadState` means by pre-season: the full
    budget is available and the first gameweek's transfers are unlimited. The
    squad the manager has entered is still used -- as the baseline every
    alternative is measured against -- but it is not a constraint, because the
    game does not make it one yet.
    """
    if state.picks is None:
        raise NoSquadError(
            "no current squad to transfer from. Enter it once with `fpl myteam set`."
        )
    played: dict[str, list[GwId]] = {}
    for chip, gw in state.chips_used:
        played.setdefault(str(chip), []).append(gw)
    chips = ChipState(played={k: tuple(v) for k, v in played.items()})
    if is_before_first_deadline(state):
        return OptSquadState(holdings={}, bank=Money(0), free_transfers=0, chips=chips)
    return OptSquadState(
        holdings={PlayerCode(p.code): Money(state.bought_at[p.code]) for p in state.picks},
        bank=Money(state.bank_tenths),
        free_transfers=int(state.free_transfers),
        chips=chips,
    )


def recommend(
    snapshot: Snapshot,
    state: MyTeamState,
    *,
    season: str,
    gws: Sequence[int],
    points_forecast: object | None,
    price_forecast: object | None = None,
    mode: ObjectiveMode = ObjectiveMode.RANK_UTILITY,
    rank_utility: object | None = None,
    rank_mv: object | None = None,
    validator: object | None = None,
    config: OptimizerConfig | None = None,
    candidates: int = DEFAULT_CANDIDATES,
    max_candidates_per_position: int | None = 40,
) -> TransferRecommendation:
    """Solve the upcoming deadline and rank the alternatives.

    ``mode`` defaults to ``RANK_UTILITY`` because that is the objective this
    engine exists to optimise. With no ``rank_utility`` provider the optimiser
    raises, and this function lets that propagate untouched. Passing
    ``ObjectiveMode.EXPECTED_POINTS`` is how a caller says, in writing, that they
    want the surrogate.

    ``ObjectiveMode.RANK_MV`` is the implemented rank objective and takes
    ``rank_mv`` -- :class:`~fpl_edge.rank.coefficients.RankCoefficients` built
    from a :class:`~fpl_edge.rank.state.RankState`. The resulting
    recommendation additionally carries the state it was solved at, a §5 hit
    verdict for every move that costs points, and the banked-FT option value.

    ``validator`` is an optional paired simulator (a
    :class:`~fpl_edge.sim.engine.SeasonSimulator`). When supplied, the chosen
    move and its alternatives are re-run on common random numbers and the
    recommendation carries paired ``Delta P(top 10k)`` with standard errors --
    F1 in the role §8.2 assigns it.
    """
    if state.picks is None:
        raise NoSquadError(
            f"the {season} GW{int(state.gw)} squad is unknown, so there is nothing "
            "to transfer from. FPL publishes picks only after a gameweek starts, "
            "and the pre-deadline endpoint needs the account password. Enter the "
            "15 once with `fpl myteam set` or the bot's /setsquad."
        )
    if points_forecast is None:
        raise PointsForecastUnavailableError(
            "no PointsForecast was supplied. The optimiser needs one row per "
            "(code, gw) with xpts and p_play; build one from the points model "
            "with fpl_edge.myteam.forecast.SampledPointsForecast, or pass a "
            "committed table with TablePointsForecast. Refusing to substitute a "
            "made-up projection: the whole recommendation would then be the "
            "output of a model nobody chose."
        )
    if mode is ObjectiveMode.RANK_UTILITY and rank_utility is None:
        # Checked here rather than inside the solver so the refusal costs
        # nothing. Building the problem means running the points model, and
        # spending five minutes on a forecast we are about to refuse to use is
        # a bad way to say no.
        raise RankUtilityUnavailableError(
            "ObjectiveMode.RANK_UTILITY needs a RankUtilityProvider (see "
            "fpl_edge.opt.interfaces.RankUtilityProvider), and none was supplied. "
            "Refusing to run: falling back to expected points would return means "
            "while reporting a rank objective, which is the failure this mode "
            "exists to prevent. Pass ObjectiveMode.EXPECTED_POINTS explicitly if "
            "the surrogate is what you want -- it is a genuinely different "
            "recommendation, not an approximation of this one."
        )
    if mode is ObjectiveMode.RANK_MV and rank_mv is None:
        # Same reasoning as above: refuse before paying for the points model.
        from fpl_edge.opt import RankInputsUnavailableError

        raise RankInputsUnavailableError(
            "ObjectiveMode.RANK_MV needs RankCoefficients (see "
            "fpl_edge.rank.coefficients.build_rank_coefficients), and none was "
            "supplied. It takes a RankState -- where you stand against the top-10k "
            "pace, and how many gameweeks are left -- plus per-player variances and "
            "the near-threshold cohort's ownership and captaincy shares. Refusing to "
            "run: without them the objective collapses to expected points while still "
            "reporting a rank mode."
        )
    if price_forecast is None:
        from fpl_edge.opt import StaticPriceForecast

        price_forecast = StaticPriceForecast()

    cfg = config or OptimizerConfig(
        mode=mode, max_candidates_per_position=max_candidates_per_position
    )
    if cfg.mode is not mode:
        mode = cfg.mode

    horizon = [GwId(int(g)) for g in gws]
    problem = build_problem(
        snapshot,
        Season(season),
        horizon,
        price_forecast=price_forecast,
        points_forecast=points_forecast,
        state=build_state(state),
        ruleset=Ruleset.from_registry(),
    )
    # Prune once, here, so every candidate solve sees an identical universe --
    # and keep the manager's fifteen whatever the optimiser's state says.
    held_codes = [int(p.code) for p in state.picks]
    problem = prune_keeping(problem, cfg.max_candidates_per_position, held_codes)
    # The per-solve config must not prune again: it would re-derive the keep set
    # from state.holdings and undo the protection above.
    cfg = replace(cfg, max_candidates_per_position=None)

    started = time.perf_counter()
    notes: list[str] = []

    # RANK_UTILITY without a provider raises out of here, by design.
    free_plan = solve_horizon(problem, cfg, rank_utility=rank_utility, rank_mv=rank_mv)
    chosen = _move_from(problem, free_plan, state)

    held = [int(p.code) for p in state.picks]
    roll_plan = _solve_squad(problem, cfg, held, rank_utility, rank_mv)
    roll = (
        _move_from(problem, roll_plan, state, label="roll")
        if roll_plan is not None
        else None
    )

    shortlist = screen_moves(problem, state, limit=candidates)
    solved: list[Move] = []
    for out, into in shortlist:
        squad = [c for c in held if c not in out] + list(into)
        plan = _solve_squad(problem, cfg, squad, rank_utility, rank_mv)
        if plan is None:
            continue
        solved.append(_move_from(problem, plan, state))

    pool = [m for m in [chosen, roll, *solved] if m is not None]
    # Deduplicate on the actual move, keeping the best objective for each.
    best_by_move: dict[tuple[tuple[int, ...], tuple[int, ...]], Move] = {}
    for move in pool:
        key = (tuple(sorted(move.out)), tuple(sorted(move.into)))
        if key not in best_by_move or move.objective > best_by_move[key].objective:
            best_by_move[key] = move
    ranked = sorted(best_by_move.values(), key=lambda m: -m.objective)
    winner = ranked[0]
    alternatives = tuple(ranked[1:])

    if mode is ObjectiveMode.EXPECTED_POINTS:
        notes.append(
            "EXPECTED_POINTS is a surrogate. It ignores ownership, variance and "
            "covariance, so it cannot see that a differential is worth more than "
            "its mean when you are chasing a rank. Not the objective this engine "
            "is for."
        )
    if getattr(points_forecast, "name", None):
        notes.append(f"points forecast: {points_forecast.name}")
    notes.extend(free_plan.notes)

    rank_state = getattr(rank_mv, "state", None)
    ft_value = _banked_ft_value(problem, winner.plan, cfg)
    hit_verdicts = _hit_verdicts(
        problem, cfg, [winner, *alternatives], roll, rank_state, rank_mv, ft_value
    )
    deltas = _paired_validation(validator, [winner, *alternatives], notes)

    return TransferRecommendation(
        season=season,
        gw=GwId(int(horizon[0])),
        mode=mode,
        horizon=tuple(horizon),
        chosen=winner,
        roll=roll,
        alternatives=alternatives,
        free_transfers=int(state.free_transfers),
        unlimited_transfers=is_before_first_deadline(state),
        notes=tuple(dict.fromkeys(notes)),
        n_candidates_screened=len(shortlist),
        n_candidates_solved=len(solved),
        solve_seconds=time.perf_counter() - started,
        rank_state=rank_state,
        hit_verdicts=hit_verdicts,
        alternatives_with_delta_p=deltas,
        banked_ft_value=ft_value,
    )


# -- rank layer helpers ------------------------------------------------------


def _banked_ft_value(
    problem: HorizonProblem, plan: HorizonPlan, cfg: OptimizerConfig
) -> float:
    if cfg.ft_value_list is None:
        return 0.0
    from fpl_edge.opt.scoring import banked_ft_value

    return banked_ft_value(problem, plan, cfg)


def _expected_points(problem: HorizonProblem, plan: HorizonPlan, cfg: OptimizerConfig) -> float:
    """The plan's value under plain expected points, whatever mode solved it.

    ``g*`` is a threshold on expected-points gain. The RANK_MV objective is a
    certainty equivalent that has already priced variance and already netted the
    hit, so comparing it to ``g*`` would double-count both.
    """
    from fpl_edge.opt import score_plan

    return score_plan(problem, plan, replace(cfg, mode=ObjectiveMode.EXPECTED_POINTS))


def _relative_variance_delta(
    problem: HorizonProblem, move: Move, rank_mv: object
) -> float:
    """Change in variance-against-the-bar from this move, at the first gameweek.

    ``(1 - 2 share) sigma^2`` summed over the players coming in, minus the same
    over those going out -- the same first-order term the objective prices, so
    the hit verdict and the objective are reasoning about one quantity rather
    than two. Positive means the move buys volatility relative to the field.
    """
    import numpy as np

    codes = np.asarray(rank_mv.codes)  # type: ignore[attr-defined]
    row_of = {int(c): i for i, c in enumerate(codes)}
    gws = [int(g) for g in rank_mv.gws]  # type: ignore[attr-defined]
    if not gws or int(problem.gws[0]) not in gws:
        return 0.0
    j = gws.index(int(problem.gws[0]))
    var = np.asarray(rank_mv.variance)      # type: ignore[attr-defined]
    own = np.asarray(rank_mv.own_share)     # type: ignore[attr-defined]

    def contribution(code: int) -> float:
        i = row_of.get(int(code))
        if i is None:
            return 0.0
        return float((1.0 - 2.0 * own[i, j]) * var[i, j])

    return sum(contribution(c) for c in move.into) - sum(
        contribution(c) for c in move.out
    )


def _hit_verdicts(
    problem: HorizonProblem,
    cfg: OptimizerConfig,
    moves: Sequence[Move],
    roll: Move | None,
    rank_state: object | None,
    rank_mv: object | None,
    ft_value: float,
) -> tuple[HitVerdict, ...]:
    """A §5 verdict for every move that costs points.

    Silent when there is no rank state: ``g*`` is a function of ``L = D + m tau``
    and without a state the only threshold available is the naive 4, which the
    existing :meth:`TransferRecommendation.hit_verdict` already reports. Making
    up a state to produce a number would be worse than saying nothing.
    """
    if rank_state is None or roll is None:
        return ()
    import math

    from fpl_edge.rank.policy import hit_is_justified

    baseline = _expected_points(problem, roll.plan, cfg)
    horizon = problem.n_gws
    out: list[HitVerdict] = []
    for move in moves:
        if move.hits <= 0:
            continue
        # Gross of the hit: score_plan has already subtracted it, so add it back.
        gain = (_expected_points(problem, move.plan, cfg) + move.hit_points) - baseline
        s_after = rank_state.s_weekly
        if rank_mv is not None:
            delta_var = _relative_variance_delta(problem, move, rank_mv)
            s_after = math.sqrt(max(rank_state.s_weekly**2 + delta_var, 1e-9))
        justified, g_star = hit_is_justified(
            gain,
            rank_state,
            s_weekly_after=s_after,
            hold_weeks=min(horizon, rank_state.tau),
            ft_option_value=ft_value,
        )
        out.append(
            HitVerdict(
                label=move.label or f"{len(move.into)}-transfer move",
                hits=move.hits,
                hit_points=move.hit_points,
                expected_gain=gain,
                breakeven_gain=g_star,
                s_weekly_after=s_after,
                justified=justified,
                ft_option_value=ft_value,
                into=tuple(int(c) for c in move.into),
                out=tuple(int(c) for c in move.out),
            )
        )
    return tuple(out)


def _paired_validation(
    validator: object | None, moves: Sequence[Move], notes: list[str]
) -> tuple[object, ...]:
    """F1 on the shortlist, or nothing at all.

    Any failure degrades to "not validated" with a note rather than taking the
    recommendation down: the F2 answer stands on its own, and §8.2 makes the
    simulator a check on it, not a precondition for it.
    """
    if validator is None or not moves:
        return ()
    from fpl_edge.rank.validate import squad_plan_from_horizon, validate_plans

    universe = getattr(validator, "universe", None)
    if universe is None:
        notes.append(
            "validator supplied without a .universe; skipping F1 paired validation."
        )
        return ()
    try:
        plans = [
            squad_plan_from_horizon(m.plan, universe, label=m.label or f"move {k + 1}")
            for k, m in enumerate(moves)
        ]
        return tuple(
            validate_plans(
                validator, plans, labels=[m.label or f"move {k + 1}" for k, m in enumerate(moves)]
            )
        )
    except Exception as exc:  # noqa: BLE001 - a failed check must not eat the answer
        notes.append(f"F1 paired validation unavailable ({exc}); reporting F2 only.")
        return ()


def _solve_squad(
    problem: HorizonProblem,
    cfg: OptimizerConfig,
    squad: Sequence[int],
    rank_utility: object | None,
    rank_mv: object | None = None,
) -> HorizonPlan | None:
    """Best plan available while owning exactly ``squad`` in the first gameweek.

    Returns None when the constrained squad cannot be fielded legally (an
    unaffordable move, or one that breaks the club limit) -- an infeasible
    alternative is information, not an error, and it simply does not appear in
    the ranking.
    """
    try:
        return solve_horizon(
            _mask_to_squad(problem, squad), cfg,
            rank_utility=rank_utility, rank_mv=rank_mv,
        )
    except InfeasibleError:
        return None


def _move_from(
    problem: HorizonProblem, plan: HorizonPlan, state: MyTeamState, *, label: str = ""
) -> Move:
    """The change relative to the squad the manager actually owns.

    Diffed against ``state``, not read off ``plan.decisions[0].transfers_*``.
    Before the first deadline the optimiser's own state is empty, so its
    "transfers in" is all fifteen players; what the manager needs to see is which
    of *their* fifteen changes.
    """
    first = plan.decisions[0]
    current = frozenset(int(p.code) for p in (state.picks or ()))
    after = frozenset(int(c) for c in first.squad)
    return Move(
        out=tuple(sorted(current - after)),
        into=tuple(sorted(after - current)),
        objective=float(plan.objective),
        hits=int(first.hits),
        bank_after=first.bank_after,
        plan=plan,
        label=label,
    )
