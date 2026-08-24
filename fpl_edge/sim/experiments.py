"""The experiments that decide whether this engine is worth running.

Run with ``uv run python -m fpl_edge.sim.experiments``.

Three questions, in order of how much they matter:

1. **Is the field correlated with me?** If not, the whole squad-sampling
   apparatus is expensive decoration and a two-distribution comparison would do.
2. **Does rank utility ever disagree with expected points?** If the
   expected-points argmax and the rank-utility argmax always coincide, the
   project's premise is wrong and we should say so loudly rather than quietly
   shipping a more complicated way to get the same answer.
3. **Does the simulated field look like the real one?** Checked against the
   verified warehouse anchors and the published rank ladder in
   :mod:`fpl_edge.sim.calibration`.

STATUS: RESEARCH, not in the production import closure (reachability audit 2026-08-20, docs/platform/AUDIT_2026-08-20.md). Kept deliberately: the §9 divergence/breakeven studies re-run from here when calibration shifts. Nothing imports this from production code, and anything that starts to should say so in ROADMAP.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from fpl_edge.models.contracts import RankUtilityConfig
from fpl_edge.sim.calibration import validate_field, validate_points_model
from fpl_edge.sim.engine import SeasonSimulator, SquadPlan, greedy_squad
from fpl_edge.sim.field import FieldConfig
from fpl_edge.sim.rank import Counterfactual
from fpl_edge.sim.squad import (
    MAX_PLAY,
    MIN_PLAY,
    SQUAD_BY_POSITION,
    SQUAD_SIZE,
    XI_SIZE,
    PlayerUniverse,
    Squad,
    pick_best_xi,
)
from fpl_edge.sim.synthetic import build_synthetic_world
from fpl_edge.sim.utility import rank_utility, rank_utility_of
from fpl_edge.types import Season

#: The user's stated setting: primary target top 10k, stretch top 1k, with a
#: real but not overwhelming penalty on catastrophic seasons.
BALANCED = RankUtilityConfig(target_rank=10_000, stretch_rank=1_000, risk_lambda=0.35,
                             field_size=5_896_644)


@dataclass
class World:
    universe: PlayerUniverse
    snapshot: Any
    points_model: Any
    ownership_model: Any
    xp: np.ndarray
    eo: np.ndarray
    captaincy: np.ndarray


def build_world(n_xp_sims: int = 1_500, seed: int = 11) -> World:
    """Warehouse-backed universe plus the per-gameweek expected points."""
    snap, pm, om = build_synthetic_world()
    u = pm.universe
    season = Season("2026-27")
    tot = np.zeros(u.n_players)
    for gw in range(1, 6):
        tot += pm.simulate(snap, season, gw, n_sims=n_xp_sims, seed=seed).mean()
    xp = tot / 5
    own = om.forecast(snap, season, 1, expected_points=xp)
    return World(u, snap, pm, om, xp,
                 own["eo_overall"].to_numpy(), own["captaincy_share"].to_numpy())


def live_world(*, n_xp_sims: int = 2_000, gw: int = 1, **kw) -> tuple[World, Any]:
    """The same World, built from the shipped models rather than the stand-ins.

    Returns the World and the :class:`~fpl_edge.sim.live.LiveWorld` it came
    from, because the latter carries the provenance -- as-of instant, days to
    the deadline, field size -- that every reported number has to be stamped
    with.

    ``World.eo`` is ownership, not effective ownership: it is what "differential"
    is measured against and what the field sampler consumes. The EO vector is on
    ``LiveWorld.eo``.

    ``World.xp`` is the *season-average* expected points, because the decisions
    compared here are season-long. ``LiveWorld.xp`` keeps the next-gameweek
    vector the ownership model was given.
    """
    from fpl_edge.sim.live import open_live_world

    lw = open_live_world(gw=gw, n_xp_sims=n_xp_sims, **kw)
    world = World(lw.universe, lw.snapshot, lw.points_model, lw.ownership_model,
                  lw.season_xp, lw.own_mean, lw.captaincy)
    return world, lw


def make_simulator(world: World, *, n_sims: int, n_rivals: int,
                   gws=tuple(range(1, 39)), seed: int = 20_260_821) -> SeasonSimulator:
    return SeasonSimulator(
        world.universe, world.points_model, world.ownership_model, world.snapshot,
        Season("2026-27"), gws, n_sims=n_sims, seed=seed,
        field_config=FieldConfig(n_rivals=n_rivals),
    )


# ---------------------------------------------------------------------------
# 1. Correlation
# ---------------------------------------------------------------------------

def optimal_squad(universe: PlayerUniverse, score: np.ndarray,
                  *, budget_tenths: int = 1000, ownership_penalty: float = 0.0,
                  eo: np.ndarray | None = None) -> Squad:
    """The exact maximiser of starting-XI ``score``, subject to the FPL rules.

    A greedy squad is not good enough to be the baseline for a divergence
    experiment: if the "expected-points-optimal" squad is not actually optimal,
    then every swap away from it is confounded by the greedy's own mistakes.
    This is a small binary program (squad, XI and armband chosen jointly) solved
    with the CBC solver that ships with PuLP.

    It is deliberately *not* the project's optimizer -- that belongs to the
    optimizer team and has to handle transfers, hits, chips and a rank
    objective. This maximises a linear score for experiment setup only.
    """
    import pulp

    n = universe.n_players
    obj_score = np.asarray(score, dtype=float).copy()
    if ownership_penalty and eo is not None:
        obj_score = obj_score - ownership_penalty * np.asarray(eo, dtype=float)

    prob = pulp.LpProblem("squad", pulp.LpMaximize)
    x = [pulp.LpVariable(f"x{i}", cat="Binary") for i in range(n)]   # in squad
    y = [pulp.LpVariable(f"y{i}", cat="Binary") for i in range(n)]   # in XI
    z = [pulp.LpVariable(f"z{i}", cat="Binary") for i in range(n)]   # captain
    prob += pulp.lpSum(obj_score[i] * (y[i] + z[i]) for i in range(n))
    prob += pulp.lpSum(x) == SQUAD_SIZE
    prob += pulp.lpSum(y) == XI_SIZE
    prob += pulp.lpSum(z) == 1
    prob += pulp.lpSum(int(universe.price_tenths[i]) * x[i] for i in range(n)) <= budget_tenths
    for i in range(n):
        prob += y[i] <= x[i]
        prob += z[i] <= y[i]
    for p, need in SQUAD_BY_POSITION.items():
        idx = np.flatnonzero(universe.position == p)
        prob += pulp.lpSum(x[int(i)] for i in idx) == need
        prob += pulp.lpSum(y[int(i)] for i in idx) >= MIN_PLAY[p]
        prob += pulp.lpSum(y[int(i)] for i in idx) <= MAX_PLAY[p]
    for club in np.unique(universe.team_code):
        idx = np.flatnonzero(universe.team_code == club)
        prob += pulp.lpSum(x[int(i)] for i in idx) <= 3
    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"squad ILP did not solve: {pulp.LpStatus[status]}")

    chosen = [i for i in range(n) if x[i].value() > 0.5]
    squad = pick_best_xi(chosen, score, universe)
    squad.validate(universe)
    return squad


def overlap_pair(world: World, strength: float = 4.0):
    """Two squads of near-identical expected points but very different overlap.

    Both are built by the same greedy rule on expected points, one nudged
    *toward* what the field owns and one *away* from it. Comparing a template to
    a deliberately bad squad would prove nothing; the point is to hold quality
    roughly fixed and vary only overlap.
    """
    u, xp, eo = world.universe, world.xp, world.eo
    return (optimal_squad(u, xp, ownership_penalty=-strength, eo=eo),
            optimal_squad(u, xp, ownership_penalty=strength, eo=eo))


def correlation_report(sim: SeasonSimulator, world: World) -> dict[str, float]:
    """Measure Cov(my score, field score) for a template and for a differential squad.

    The prediction the design makes is specific and falsifiable: a squad that
    overlaps heavily with the field must correlate more strongly with it than
    one that does not. If both came out the same, the field would not be
    responding to squad composition and the model would be broken.
    """
    template, contrarian = overlap_pair(world)

    out: dict[str, float] = {}
    for name, sq in (("template", template), ("differential", contrarian)):
        d = sim.evaluate(SquadPlan(sq, label=name))
        assert sim.rival_totals is not None
        riv = sim.rival_totals
        # Average pairwise correlation with an individual rival, not just with
        # the field's mean: the mean is dominated by the common factor, while an
        # individual rival is what I am actually being ranked against.
        rc = _mean_pairwise_corr(d.my_scores, riv)
        out[f"{name}_corr_field_mean"] = d.correlation_with_field()
        out[f"{name}_corr_single_rival"] = rc
        out[f"{name}_beta_on_field"] = d.beta_on_field()
        out[f"{name}_overlap"] = _overlap(sq, sim, world)
        out[f"{name}_mean_points"] = d.expected_points()
        out[f"{name}_p_top10k"] = d.p_top(10_000)
    return out


def _mean_pairwise_corr(mine: np.ndarray, rivals: np.ndarray, n: int = 400) -> float:
    idx = np.linspace(0, rivals.shape[0] - 1, min(n, rivals.shape[0])).astype(int)
    r = rivals[idx].astype(np.float64)
    r = (r - r.mean(axis=1, keepdims=True)) / np.maximum(r.std(axis=1, keepdims=True), 1e-9)
    m = (mine - mine.mean()) / max(mine.std(), 1e-9)
    return float((r * m[None, :]).mean(axis=1).mean())


def _overlap(squad, sim: SeasonSimulator, world: World) -> float:
    """Expected number of my XI that a random rival also starts, in GW1."""
    gw = sim.gws[0]
    sq = sim._squads[gw]
    start = sq.start_share(world.universe.n_players)
    return float(start[np.array(squad.starters)].sum())


def independence_counterfactual(sim: SeasonSimulator, world: World,
                                seed: int = 3) -> dict[str, float]:
    """What the answer would have been if the field were sampled independently.

    Reuses exactly the same simulated field scores but shuffles them across
    simulations, which destroys the coupling to my own draw while leaving every
    marginal distribution untouched. Any difference in P(top 10k) is caused by
    correlation and nothing else.
    """
    assert sim.rival_totals is not None
    rng = np.random.default_rng(seed)
    template, contrarian = overlap_pair(world)
    from fpl_edge.sim.rank import rank_from_scores

    shuffled = sim.rival_totals[:, rng.permutation(sim.n_sims)]
    out = {}
    for name, sq in (("template", template), ("differential", contrarian)):
        mine = sim.score_plan(SquadPlan(sq))
        r_true = rank_from_scores(mine, sim.rival_totals,
                                  field_size=sim.field_config.field_size)
        r_ind = rank_from_scores(mine, shuffled, field_size=sim.field_config.field_size)
        out[f"{name}_p_top10k_correlated"] = float((r_true <= 10_000).mean())
        out[f"{name}_p_top10k_independent"] = float((r_ind <= 10_000).mean())
    return out


# ---------------------------------------------------------------------------
# 2. Divergence between expected points and rank utility
# ---------------------------------------------------------------------------

def _swap_candidates(world: World, squad: Squad, max_xp_cost_per_gw: float,
                     per_slot: int = 2) -> list[tuple[int, int]]:
    """Same-position, budget- and club-feasible swaps that give up little xPts.

    For every starter, the ``per_slot`` cheapest-to-lose alternatives in each
    direction of ownership: the least-owned (more differential) and the
    most-owned (more template). Holding the expected-points cost small is the
    whole point -- a differential that costs a point a gameweek costs 38 over a
    season and no amount of decorrelation pays that back.
    """
    u, xp, eo = world.universe, world.xp, world.eo
    budget_left = 1000 - squad.price(u)
    out: list[tuple[int, int]] = []
    for out_i in squad.starters:
        p = int(u.position[out_i])
        cap_price = int(u.price_tenths[out_i]) + budget_left
        club: dict[int, int] = {}
        for k in squad.all_indices:
            if k != out_i:
                club[int(u.team_code[k])] = club.get(int(u.team_code[k]), 0) + 1
        cand = [
            j for j in range(u.n_players)
            if int(u.position[j]) == p and j not in squad.all_indices
            and u.price_tenths[j] <= cap_price
            and club.get(int(u.team_code[j]), 0) < 3
            and xp[out_i] - max_xp_cost_per_gw <= xp[j] <= xp[out_i] + 1e-9
        ]
        if not cand:
            continue
        cand.sort(key=lambda j: eo[j])
        for in_i in cand[:per_slot] + cand[-per_slot:]:
            if (out_i, in_i) not in out:
                out.append((out_i, in_i))
    return out


def divergence_experiment(sim: SeasonSimulator, world: World,
                          config: RankUtilityConfig = BALANCED,
                          max_xp_cost_per_gw: float = 0.35) -> dict:
    """Find a decision where maximising expected points is the wrong call.

    The baseline is the *exact* expected-points optimum, not a greedy
    approximation -- otherwise every "improvement" could just be the greedy's
    own mistake being undone. Every candidate gives up expected points by
    construction, so the expected-points objective ranks the baseline first,
    unambiguously. If any candidate raises P(rank <= target) by more than two
    paired standard errors, the two objectives disagree and the premise holds.

    Candidates run in *both* directions of ownership, because which way rank
    utility wants to move depends on where the baseline already sits relative to
    the target: an underdog needs variance and therefore differentials, a
    favourite needs safety and therefore the template.
    """
    u, xp, eo = world.universe, world.xp, world.eo
    base_squad = optimal_squad(u, xp)
    base_plan = SquadPlan(base_squad, label="xPts-optimal (exact)")
    base = sim.evaluate(base_plan)
    base_u = rank_utility_of(base, config)

    rows: list[dict[str, Any]] = []
    for out_i, in_i in _swap_candidates(world, base_squad, max_xp_cost_per_gw):
        plan = SquadPlan(base_squad.replace(out_i, in_i, u),
                         label=f"{u.web_name[out_i]}->{u.web_name[in_i]}")
        d = sim.evaluate(plan)
        cf = Counterfactual(a=d, b=base)
        ru = rank_utility_of(d, config)
        rows.append({
            "out": str(u.web_name[out_i]), "in": str(u.web_name[in_i]),
            "eo_out": float(eo[out_i]), "eo_in": float(eo[in_i]),
            "direction": "differential" if eo[in_i] < eo[out_i] else "template",
            "d_xpts_per_gw": float(xp[in_i] - xp[out_i]),
            "d_mean_points": cf.delta_points(),
            "se_d_mean_points": cf.se_delta_points(),
            "d_p_top10k": cf.delta_p_top(config.target_rank),
            "se_d_p_top10k": cf.se_delta_p_top(config.target_rank),
            "d_p_top1k": cf.delta_p_top(config.stretch_rank),
            "se_d_p_top1k": cf.se_delta_p_top(config.stretch_rank),
            "d_utility": ru.utility - base_u.utility,
            "corr_with_field": d.correlation_with_field(),
        })

    rows.sort(key=lambda r: -r["d_p_top10k"])
    divergent = [
        r for r in rows
        if r["d_mean_points"] < 0 and r["d_p_top10k"] > 2.0 * r["se_d_p_top10k"]
    ]
    ladder = sim.field_rank_ladder()
    n = sim.field_config.field_size
    assert sim.rival_totals is not None
    threshold = float(np.quantile(sim.rival_totals, 1.0 - config.target_rank / n,
                                  axis=0).mean())
    return {
        "target_rank": config.target_rank,
        "baseline": {
            "label": base_plan.label,
            "mean_points": base.expected_points(),
            "sd_points": float(base.my_scores.std()),
            "p_top10k": base_u.p_target,
            "p_top1k": base_u.p_stretch,
            "utility": base_u.utility,
            "median_rank": base.median_rank(),
            "corr_with_field": base.correlation_with_field(),
            "field_mean": ladder["mean"],
            "target_threshold_score": threshold,
        },
        "candidates": rows,
        "divergent": divergent,
        "premise_holds": bool(divergent),
    }


def divergence_across_targets(sim: SeasonSimulator, world: World,
                              targets=(100_000, 10_000, 1_000, 100)) -> list[dict]:
    """Does the *direction* of the disagreement depend on the target?

    Run the identical set of candidate swaps against several target ranks. The
    expected-points ranking is the same every time -- expected points does not
    know what a target is. If the rank-utility ranking changes with the target,
    that is the sharpest possible statement that the two objectives are not the
    same objective.
    """
    u, xp = world.universe, world.xp
    base_squad = optimal_squad(u, xp)
    base_scores = sim.score_plan(SquadPlan(base_squad))
    from fpl_edge.sim.rank import rank_from_scores

    assert sim.rival_totals is not None
    n = sim.field_config.field_size
    base_ranks = rank_from_scores(base_scores, sim.rival_totals, field_size=n)

    swaps = _swap_candidates(world, base_squad, 0.35)
    evaluated = []
    for out_i, in_i in swaps:
        s = sim.score_plan(SquadPlan(base_squad.replace(out_i, in_i, u)))
        evaluated.append((out_i, in_i, s, rank_from_scores(s, sim.rival_totals, field_size=n)))

    out: list[dict[str, Any]] = []
    for target in targets:
        best: dict[str, Any] | None = None
        for out_i, in_i, s, r in evaluated:
            d_p = float((r <= target).mean() - (base_ranks <= target).mean())
            d_pts = float((s - base_scores).mean())
            if best is None or d_p > best["d_p_target"]:
                best = {
                    "target": target,
                    "swap": f"{u.web_name[out_i]}->{u.web_name[in_i]}",
                    "direction": ("differential"
                                  if world.eo[in_i] < world.eo[out_i] else "template"),
                    "eo_out": float(world.eo[out_i]), "eo_in": float(world.eo[in_i]),
                    "d_p_target": d_p,
                    "se_d_p_target": float(
                        np.std((r <= target).astype(float)
                               - (base_ranks <= target).astype(float), ddof=1)
                        / np.sqrt(len(r))
                    ),
                    "d_mean_points": d_pts,
                    "base_p_target": float((base_ranks <= target).mean()),
                }
        if best is not None:
            out.append(best)
    return out


def breakeven_scan(sim: SeasonSimulator, world: World,
                   config: RankUtilityConfig = BALANCED, n: int = 12) -> dict:
    """How many expected points is rank utility willing to pay for a swap?

    Takes the starter with the widest choice of near-equal replacements and
    walks through them in order of expected points sacrificed. The
    expected-points objective always prefers zero sacrifice. Where
    ``d_p_top10k`` changes sign is the price the rank objective is willing to
    pay, and it is the most directly useful number here for the optimizer.
    """
    u, xp, eo = world.universe, world.xp, world.eo
    base_squad = optimal_squad(u, xp)
    base = sim.evaluate(SquadPlan(base_squad))
    base_u = rank_utility_of(base, config)

    budget_left = 1000 - base_squad.price(u)
    best_slot, best_cand = None, []
    for out_i in base_squad.starters:
        p = int(u.position[out_i])
        club: dict[int, int] = {}
        for k in base_squad.all_indices:
            if k != out_i:
                club[int(u.team_code[k])] = club.get(int(u.team_code[k]), 0) + 1
        cand = [
            j for j in range(u.n_players)
            if int(u.position[j]) == p and j not in base_squad.all_indices
            and u.price_tenths[j] <= int(u.price_tenths[out_i]) + budget_left
            and club.get(int(u.team_code[j]), 0) < 3
            and xp[j] <= xp[out_i] + 1e-9
        ]
        cand.sort(key=lambda j: -xp[j])
        if best_slot is None or len(cand) > len(best_cand):
            best_slot, best_cand = out_i, cand

    assert best_slot is not None
    rows = []
    for in_i in best_cand[:n]:
        d = sim.evaluate(SquadPlan(base_squad.replace(best_slot, in_i, u)))
        cf = Counterfactual(a=d, b=base)
        ru = rank_utility_of(d, config)
        rows.append({
            "in": str(u.web_name[in_i]), "eo_in": float(eo[in_i]),
            "d_xpts_per_gw": float(xp[in_i] - xp[best_slot]),
            "d_mean_points": cf.delta_points(),
            "d_p_top10k": cf.delta_p_top(10_000),
            "se_d_p_top10k": cf.se_delta_p_top(10_000),
            "d_utility": ru.utility - base_u.utility,
            "corr_with_field": d.correlation_with_field(),
        })
    return {"out": str(u.web_name[best_slot]), "eo_out": float(eo[best_slot]),
            "baseline_corr": base.correlation_with_field(),
            "baseline_p_top10k": base_u.p_target, "rows": rows}


def captaincy_divergence(sim: SeasonSimulator, world: World,
                         config: RankUtilityConfig = BALANCED, top_k: int = 6) -> dict:
    """The cleanest possible test: one gameweek, one captaincy decision.

    Everything else -- squad, transfers, the other 37 gameweeks -- is held
    identical, and both candidates run on the same simulations against the same
    field. The only difference is the armband in GW1.
    """
    u, xp = world.universe, world.xp
    sq = optimal_squad(u, xp)
    base = SquadPlan(sq, label=f"C: {u.web_name[sq.captain]} (xPts-max)")
    base_d = sim.evaluate(base)
    base_u = rank_utility_of(base_d, config)
    gw = sim.gws[0]
    field_cap = sim._squads[gw].captain_share(u.n_players)

    rows: list[dict[str, Any]] = []
    order = sorted(sq.starters, key=lambda i: -xp[i])
    for cand in order[:top_k]:
        if cand == sq.captain:
            continue
        plan = base.with_captain(gw, cand, label=f"C: {u.web_name[cand]}",
                                 revert_gw=sim.gws[1])
        d = sim.evaluate(plan)
        cf = Counterfactual(a=d, b=base_d)
        ru = rank_utility_of(d, config)
        rows.append({
            "captain": str(u.web_name[cand]),
            "xp": float(xp[cand]),
            "field_captaincy_share": float(field_cap[cand]),
            "d_mean_points": cf.delta_points(),
            "se_d_mean_points": cf.se_delta_points(),
            "d_p_top10k": cf.delta_p_top(10_000),
            "se_d_p_top10k": cf.se_delta_p_top(10_000),
            "d_utility": ru.utility - base_u.utility,
        })
    rows.sort(key=lambda r: -r["d_utility"])
    return {
        "baseline_captain": str(u.web_name[sq.captain]),
        "baseline_captain_xp": float(xp[sq.captain]),
        "baseline_captain_field_share": float(field_cap[sq.captain]),
        "baseline_p_top10k": base_u.p_target,
        "candidates": rows,
    }


# ---------------------------------------------------------------------------
# 2d. The objective is a configuration, not a constant
# ---------------------------------------------------------------------------

def utility_under_configs(sim: SeasonSimulator, world: World,
                          configs: dict[str, RankUtilityConfig]) -> list[dict]:
    """The same candidate set scored under several rank-utility configurations.

    ``USER.rank_utility`` is a decision, and it has been changed at least once
    (from top-10k-balanced to top-1k-aggressive). Rather than hardcode one, this
    reports the best swap under each, so the effect of the configuration on the
    *decision* -- not just on the score -- is visible.
    """
    u, xp = world.universe, world.xp
    base_squad = optimal_squad(u, xp)
    base_scores = sim.score_plan(SquadPlan(base_squad))
    from fpl_edge.sim.rank import rank_from_scores

    assert sim.rival_totals is not None
    n = sim.field_config.field_size
    base_ranks = rank_from_scores(base_scores, sim.rival_totals, field_size=n)
    candidates = [
        (out_i, in_i, rank_from_scores(
            sim.score_plan(SquadPlan(base_squad.replace(out_i, in_i, u))),
            sim.rival_totals, field_size=n))
        for out_i, in_i in _swap_candidates(world, base_squad, 0.35)
    ]

    out = []
    for name, cfg in configs.items():
        base_u = rank_utility(base_ranks, cfg, field_size=cfg.field_size or n)
        best = None
        for out_i, in_i, ranks in candidates:
            ru = rank_utility(ranks, cfg, field_size=cfg.field_size or n)
            if best is None or ru.utility > best[0].utility:
                best = (ru, out_i, in_i)
        if best is None:
            continue
        ru, out_i, in_i = best
        out.append({
            "config": name, "target": cfg.target_rank, "stretch": cfg.stretch_rank,
            "risk_lambda": cfg.risk_lambda,
            "base_utility": base_u.utility, "base_p_target": base_u.p_target,
            "base_se_utility": base_u.se_utility,
            "best_swap": f"{u.web_name[out_i]}->{u.web_name[in_i]}",
            "direction": "differential" if world.eo[in_i] < world.eo[out_i] else "template",
            "d_utility": ru.utility - base_u.utility,
            "d_p_target": ru.p_target - base_u.p_target,
            "swap_beats_baseline": bool(ru.utility > base_u.utility),
        })
    return out


def field_size_sensitivity(sim: SeasonSimulator, plan: SquadPlan,
                           sizes: tuple[int, ...]) -> list[dict]:
    """How much P(top 10k) moves with the registration count.

    ``total_players`` was 5,896,644 at rule capture and 5,950,733 on the live API
    the same day, and it keeps climbing to the deadline. Rank is
    ``1 + p * N``, so a larger field makes every absolute threshold harder; this
    quantifies by how much rather than waving at it.
    """
    from fpl_edge.sim.rank import rank_from_scores

    assert sim.rival_totals is not None
    mine = sim.score_plan(plan)
    out = []
    for n in sizes:
        ranks = rank_from_scores(mine, sim.rival_totals, field_size=n)
        out.append({
            "field_size": n,
            "p_top10k": float((ranks <= 10_000).mean()),
            "p_top1k": float((ranks <= 1_000).mean()),
            "median_rank": float(np.median(ranks)),
        })
    return out


# ---------------------------------------------------------------------------
# 3. Monte Carlo error
# ---------------------------------------------------------------------------

def convergence_study(world: World, sizes=(500, 1_000, 2_000, 4_000),
                      n_rivals: int = 10_000, gws=tuple(range(1, 39))) -> list[dict]:
    """How many simulations are actually needed for P(top 10k) to be usable."""
    xp = world.xp
    out = []
    for n in sizes:
        sim = make_simulator(world, n_sims=n, n_rivals=n_rivals, gws=gws)
        t0 = time.perf_counter()
        sim.prepare()
        prep = time.perf_counter() - t0
        sq = greedy_squad(world.universe, xp, xp)
        t1 = time.perf_counter()
        d = sim.evaluate(SquadPlan(sq, label="xPts-optimal"))
        ev = time.perf_counter() - t1
        out.append({
            "n_sims": n, "n_rivals": n_rivals,
            "prepare_seconds": prep, "evaluate_seconds": ev,
            "p_top10k": d.p_top(10_000), "se_top10k": d.se_p_top(10_000),
            "p_top1k": d.p_top(1_000), "se_top1k": d.se_p_top(1_000),
            "p_top100": d.p_top(100), "se_top100": d.se_p_top(100),
            "mean_points": d.expected_points(),
        })
    return out


# ---------------------------------------------------------------------------

def _fmt(x) -> str:
    return f"{x:,.4f}" if isinstance(x, float) and abs(x) < 100 else f"{x:,.1f}"


def main(n_sims: int = 4_000, n_rivals: int = 10_000, *, live: bool = False,
         convergence: bool = True) -> None:  # pragma: no cover
    np.set_printoptions(suppress=True)
    print("=" * 78)
    print("FPL-EDGE SIMULATOR: correlation, divergence and field validation")
    print("=" * 78)

    t0 = time.perf_counter()
    if live:
        world, lw = live_world()
        print(f"\nLIVE models: decomposed points (Dixon-Coles x GBM minutes x per-90 "
              f"rates) and OwnershipForecaster")
        print(f"  as_of {lw.as_of:%Y-%m-%d %H:%MZ}   GW{lw.ownership_model.forecast_gw} "
              f"deadline {lw.deadline:%Y-%m-%d %H:%MZ}   "
              f"T-{lw.days_to_deadline:.2f}d   field {lw.field_size:,}")
        print(f"  ownership path: {lw.ownership_model.frame['path'].iloc[0]}")
    else:
        world = build_world()
        print("\nSYNTHETIC stand-in models (fpl_edge.sim.synthetic)")
    print(f"universe: {world.universe.n_players} players   "
          f"built in {time.perf_counter() - t0:.1f}s")

    print("\n--- 1. POINTS MODEL LEVEL vs REAL WAREHOUSE DATA " + "-" * 29)
    _got, lines = validate_points_model(
        world.points_model, world.snapshot, "2026-27", world.ownership_model
    )
    for line in lines:
        print("  " + line)

    sim = make_simulator(world, n_sims=n_sims, n_rivals=n_rivals)
    t0 = time.perf_counter()
    sim.prepare(verbose=False)
    print("\n--- 2. FULL REST-OF-SEASON SIMULATION " + "-" * 40)
    print(f"  38 gameweeks x {n_sims:,} sims x {n_rivals:,} rivals "
          f"in {time.perf_counter() - t0:.1f}s")
    for k, v in sim.diagnostics.items():
        print(f"    {k:28s} {v:,.3f}")
    print("  GW1 ownership reproduction:")
    for k, v in sim.realised_ownership_error(sim.gws[0]).items():
        print(f"    {k:28s} {v:.5f}")

    print("\n--- 3. FIELD SHAPE vs PUBLISHED RANK LADDER " + "-" * 34)
    fg, flines = validate_field(sim)
    for line in flines:
        print("  " + line)
    for k in sorted(fg):
        if k.startswith("rank_") or k in ("mean", "sd"):
            print(f"    {k:28s} {fg[k]:,.1f}")

    print("\n--- 4. CORRELATION BETWEEN MY SCORE AND THE FIELD " + "-" * 28)
    for k, v in correlation_report(sim, world).items():
        print(f"    {k:34s} {_fmt(v)}")
    print("  counterfactual: same marginals, coupling destroyed")
    for k, v in independence_counterfactual(sim, world).items():
        print(f"    {k:34s} {v:.5f}")

    print("\n--- 5. CAPTAINCY: xPts-max vs rank-utility-max " + "-" * 31)
    cap = captaincy_divergence(sim, world)
    print(f"  baseline captain {cap['baseline_captain']} "
          f"(xP {cap['baseline_captain_xp']:.2f}/gw, field share "
          f"{cap['baseline_captain_field_share']:.1%}), "
          f"P(top10k) {cap['baseline_p_top10k']:.4f}")
    print(f"    {'captain':16s} {'xP':>6s} {'field%':>7s} {'dPts':>8s} {'dP10k':>9s} "
          f"{'se':>8s} {'dU':>9s}")
    for r in cap["candidates"]:
        print(f"    {r['captain']:16s} {r['xp']:6.2f} {r['field_captaincy_share']:7.1%} "
              f"{r['d_mean_points']:8.2f} {r['d_p_top10k']:+9.5f} "
              f"{r['se_d_p_top10k']:8.5f} {r['d_utility']:+9.5f}")

    print("\n--- 6. SQUAD SWAPS: xPts-max vs rank-utility-max " + "-" * 29)
    div = divergence_experiment(sim, world)
    b = div["baseline"]
    print(f"  baseline {b['label']}: mean {b['mean_points']:.1f} pts "
          f"(sd {b['sd_points']:.1f}), field mean {b['field_mean']:.1f}, "
          f"top-{div['target_rank']:,} needs ~{b['target_threshold_score'] or float('nan'):.0f}")
    print(f"  P(top10k) {b['p_top10k']:.4f}, P(top1k) {b['p_top1k']:.4f}, "
          f"U {b['utility']:.4f}, median rank {b['median_rank']:,.0f}, "
          f"corr with field {b['corr_with_field']:.4f}")
    print(f"    {'out':14s} {'in':14s} {'dir':12s} {'eo_out':>7s} {'eo_in':>6s} "
          f"{'dxP/gw':>7s} {'dPts':>7s} {'dP10k':>9s} {'se':>8s} {'corr':>7s} {'dU':>9s}")
    for r in div["candidates"]:
        print(f"    {r['out']:14s} {r['in']:14s} {r['direction']:12s} "
              f"{r['eo_out']:7.1%} {r['eo_in']:6.1%} {r['d_xpts_per_gw']:7.3f} "
              f"{r['d_mean_points']:7.2f} {r['d_p_top10k']:+9.5f} "
              f"{r['se_d_p_top10k']:8.5f} {r['corr_with_field']:7.4f} "
              f"{r['d_utility']:+9.5f}")
    print(f"\n  PREMISE HOLDS: {div['premise_holds']}  "
          f"({len(div['divergent'])} of {len(div['candidates'])} swaps lower expected "
          f"points but raise P(top {div['target_rank']:,}) by >2 paired standard errors)")
    for r in div["divergent"][:3]:
        print(f"    {r['out']} -> {r['in']}  ({r['direction']}): "
              f"{r['d_mean_points']:+.2f} season points, "
              f"P(top 10k) {b['p_top10k']:.4f} -> {b['p_top10k'] + r['d_p_top10k']:.4f} "
              f"({r['d_p_top10k']:+.4f} +- {r['se_d_p_top10k']:.4f})")

    print("\n--- 6b. DOES THE DISAGREEMENT DEPEND ON THE TARGET? " + "-" * 26)
    print(f"    {'target':>9s} {'best swap':30s} {'dir':12s} {'base P':>8s} "
          f"{'dP':>9s} {'se':>8s} {'dPts':>8s}")
    for r in divergence_across_targets(sim, world):
        print(f"    {r['target']:9,d} {r['swap']:30s} {r['direction']:12s} "
              f"{r['base_p_target']:8.4f} {r['d_p_target']:+9.5f} "
              f"{r['se_d_p_target']:8.5f} {r['d_mean_points']:8.2f}")

    print("\n--- 6c. BREAK-EVEN: what is differentiation worth in xPts? " + "-" * 18)
    bs = breakeven_scan(sim, world)
    print(f"  replacing {bs['out']} (owned {bs['eo_out']:.1%}); baseline "
          f"P(top10k) {bs['baseline_p_top10k']:.4f}, corr with field "
          f"{bs['baseline_corr']:.4f}")
    print(f"    {'in':16s} {'eo_in':>6s} {'dxP/gw':>8s} {'dPts':>8s} {'dP10k':>9s} "
          f"{'se':>8s} {'corr':>7s} {'dU':>9s}")
    for r in bs["rows"]:
        print(f"    {r['in']:16s} {r['eo_in']:6.1%} {r['d_xpts_per_gw']:8.3f} "
              f"{r['d_mean_points']:8.2f} {r['d_p_top10k']:+9.5f} "
              f"{r['se_d_p_top10k']:8.5f} {r['corr_with_field']:7.4f} "
              f"{r['d_utility']:+9.5f}")

    if not convergence:
        return
    print("\n--- 7. MONTE CARLO CONVERGENCE " + "-" * 47)
    print(f"    {'n_sims':>8s} {'prep_s':>8s} {'eval_s':>8s} {'P(10k)':>9s} {'se':>8s} "
          f"{'P(1k)':>9s} {'se':>8s} {'P(100)':>9s} {'se':>8s}")
    for r in convergence_study(world, n_rivals=n_rivals):
        print(f"    {r['n_sims']:8,d} {r['prepare_seconds']:8.1f} {r['evaluate_seconds']:8.2f} "
              f"{r['p_top10k']:9.5f} {r['se_top10k']:8.5f} "
              f"{r['p_top1k']:9.5f} {r['se_top1k']:8.5f} "
              f"{r['p_top100']:9.5f} {r['se_top100']:8.5f}")


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="use the shipped points and ownership models instead of the "
                         "development stand-ins")
    ap.add_argument("--n-sims", type=int, default=4_000)
    ap.add_argument("--n-rivals", type=int, default=10_000)
    ap.add_argument("--no-convergence", action="store_true")
    a = ap.parse_args()
    main(a.n_sims, a.n_rivals, live=a.live, convergence=not a.no_convergence)
