"""``fpl solve`` -- the horizon solve, reachable at last.

Until this command existed the rank objective was unreachable in production:
``ObjectiveMode.RANK_MV``, the ``fpl_edge/rank`` machinery and the whole
``sim`` package sat behind a script that read committed fixtures
(``scripts/rank_gw1_solve.py``) and a GW1-only artefact script
(``scripts/gw1_squad.py``). The engine's stated objective could not be run by
its user. This command solves the coming gameweeks in BOTH objectives against
the live warehouse, prints the squads and their diff, and persists the plan
artefact the weekly report renders.

Evidence discipline (see ``fpl_edge/rank/assemble.py``): variance is measured
from four seasons of real scoring, ownership is FPL's own marginals with the
provenance saying exactly that, captaincy is a labelled lower bound from the
external EO feed or explicitly zero, and the rank state's provenance records
whether its deficit is an identity (pre-season), a supplied override, or a
stylised default -- never a silent guess.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import typer

from fpl_edge.store.warehouse import DEFAULT_DB


def register(app: typer.Typer) -> None:
    app.command("solve")(solve)


def solve(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="Path to the DuckDB warehouse."),
    season: str = typer.Option("2026-27", "--season", help="Season in FPL's 2026-27 form."),
    gw: int = typer.Option(None, "--gw", help="Defaults to the next open gameweek."),
    horizon: int = typer.Option(5, "--horizon", help="Gameweeks in the solve window."),
    seconds: float = typer.Option(
        300.0, "--seconds", help="MILP time limit per objective."
    ),
    n_sims: int = typer.Option(
        1000, "--n-sims", help="Simulation draws per gameweek for the forecast."
    ),
    mode: str = typer.Option(
        "both", "--mode", help="both | rank | points. 'both' prints the diff."
    ),
    deficit: float = typer.Option(
        None, "--deficit",
        help="Points behind the top-10k pace. Omitted: 0 pre-season (an "
             "identity), else you are asserting you are level with the pace "
             "and the artefact records that as an assumption.",
    ),
    commit: bool = typer.Option(
        True, "--commit/--no-commit",
        help="Persist the plan artefact the weekly report renders.",
    ),
) -> None:
    """Solve the horizon in the rank and points objectives, side by side.

    The two objectives agreeing is a finding; them disagreeing is the whole
    reason this engine exists. Either way you see it rather than being told.
    """
    if mode not in ("both", "rank", "points"):
        raise typer.BadParameter("mode must be both, rank or points")

    # Heavy imports live here so `fpl --help` stays fast.
    from fpl_edge.models.minutes import GBMMinutesModel, TrainingSetBuilder
    from fpl_edge.models.points.model import DecomposedPointsModel
    from fpl_edge.models.points.shares import estimate_rates
    from fpl_edge.models.team_goals import DixonColesModel
    from fpl_edge.myteam.forecast import SampledPointsForecast
    from fpl_edge.opt import (
        ObjectiveMode,
        OptimizerConfig,
        SolverConfig,
        StaticPriceForecast,
        build_problem,
        solve_horizon,
    )
    from fpl_edge.rank import RankState, build_rank_coefficients, theta
    from fpl_edge.rank.assemble import (
        HISTORY_SEASONS,
        cohort_shares,
        player_variances,
        points_moments,
    )
    from fpl_edge.store import Warehouse
    from fpl_edge.types import GwId

    with Warehouse.read_copy(db) as wh:
        now = dt.datetime.now(dt.timezone.utc)
        snap_now = wh.snapshot_at(now)
        target = int(gw) if gw is not None else int(snap_now.next_gw(season))
        deadline = snap_now.deadline(season, target)
        snap = wh.snapshot_at(deadline if deadline > now else now)
        gws = [GwId(g) for g in range(target, target + int(horizon))]
        typer.echo(f"Solving GW{target}..{gws[-1]} for {season} "
             f"(deadline {deadline:%Y-%m-%d %H:%M}Z).")

        typer.echo("Fitting models (goals, minutes, rates)...")
        goals = DixonColesModel()
        goals.fit(snap, season)
        ts = TrainingSetBuilder(snapshot_at=wh.snapshot_at, catalog=snap).build(
            list(HISTORY_SEASONS)
        )
        mins = GBMMinutesModel().fit(ts)
        rates = estimate_rates(snap, list(HISTORY_SEASONS))
        model = DecomposedPointsModel(goal_model=goals, minutes_model=mins, rates=rates)

        problem = build_problem(
            snap, season, gws,
            price_forecast=StaticPriceForecast(),
            points_forecast=SampledPointsForecast(model, n_sims=n_sims, seed=20260821),
            state=None,
        )

        plans: dict[str, object] = {}
        configs: dict[str, OptimizerConfig] = {}
        notes: list[str] = []
        coef = None

        if mode in ("both", "points"):
            configs["expected_points"] = OptimizerConfig(
                mode=ObjectiveMode.EXPECTED_POINTS,
                max_candidates_per_position=45,
                solver=SolverConfig(time_limit_s=seconds, mip_gap_rel=0.01),
            )

        if mode in ("both", "rank"):
            moments = points_moments(wh)
            variance = player_variances(problem, moments, problem.p_play)
            own, cap, provenance, share_notes = cohort_shares(
                problem, snap, wh, season, target
            )
            notes.extend(share_notes)
            tau = max(1, 39 - target)
            if deficit is None and target == 1:
                d, d_note = 0.0, "pre-season: D=0 and tau=38 are identities"
            elif deficit is None:
                d, d_note = 0.0, (
                    f"deficit NOT MEASURED (no top-10k pace series yet); "
                    f"assuming level with the pace at GW{target}. Pass "
                    f"--deficit to assert your real position."
                )
            else:
                d, d_note = float(deficit), f"deficit supplied by the caller: {deficit:+.1f}"
            notes.append(d_note)
            from fpl_edge.rank.policy import BALANCED

            state = RankState.stylised(
                deficit=d, tau=tau, m_weekly=BALANCED.m, s_weekly=BALANCED.s,
                notes=(d_note,),
            )
            typer.echo(f"Rank state: {state.describe()}")
            typer.echo(f"theta = {theta(state):+.6f} per point^2")
            coef = build_rank_coefficients(
                problem, state, variance=variance,
                own_share=own, captain_share=cap, provenance=provenance,
            )
            configs["rank_mv"] = OptimizerConfig(
                mode=ObjectiveMode.RANK_MV,
                max_candidates_per_position=45,
                solver=SolverConfig(time_limit_s=seconds, mip_gap_rel=0.01),
            )

        for label, cfg in configs.items():
            typer.echo(f"Solving {label} (limit {seconds:.0f}s)...")
            plans[label] = solve_horizon(
                problem, cfg,
                rank_mv=coef if cfg.mode is ObjectiveMode.RANK_MV else None,
            )

        players = snap.selectable(season)
        name = dict(zip(players["code"].astype(int), players["web_name"]))
        for label, plan in plans.items():
            d0 = plan.decisions[0]
            typer.echo(f"\n--- {label} " + "-" * (60 - len(label)))
            typer.echo(f"objective {plan.objective:.2f}  status {plan.status}  "
                 f"gap {'n/a' if plan.mip_gap is None else f'{plan.mip_gap:.1e}'}")
            typer.echo("XI:    " + ", ".join(name.get(int(c), str(c)) for c in d0.starting_xi))
            typer.echo("bench: " + ", ".join(name.get(int(c), str(c)) for c in d0.bench))
            typer.echo(f"captain {name.get(int(d0.captain), d0.captain)}  "
                 f"chip {d0.chip or 'none'}")

        if len(plans) == 2:
            a = {int(c) for c in plans["expected_points"].decisions[0].squad}
            b = {int(c) for c in plans["rank_mv"].decisions[0].squad}
            typer.echo("\n--- DIFF (rank_mv vs expected_points) " + "-" * 24)
            typer.echo(f"squad: {len(b - a)} of 15 differ")
            if b - a:
                typer.echo("  rank-only:   " + ", ".join(sorted(name.get(c, str(c)) for c in b - a)))
            if a - b:
                typer.echo("  points-only: " + ", ".join(sorted(name.get(c, str(c)) for c in a - b)))
            ca = int(plans["expected_points"].decisions[0].captain)
            cb = int(plans["rank_mv"].decisions[0].captain)
            typer.echo(f"captain: {name.get(ca, ca)} -> {name.get(cb, cb)}"
                 + ("  (unchanged)" if ca == cb else "  CHANGED"))
        for note in notes:
            typer.echo(f"note: {note}")

        if commit:
            # The rank plan when it exists -- it is the engine's objective --
            # else the points plan. The artefact records which.
            label = "rank_mv" if "rank_mv" in plans else "expected_points"
            plan = plans[label]
            d0 = plan.decisions[0]
            artefact = {
                "generated_at": now.isoformat(),
                "snapshot_as_of": (deadline if deadline > now else now).isoformat(),
                "season": season,
                "horizon_gws": [int(g) for g in gws],
                "objective_mode": label,
                "objective": float(plan.objective),
                "n_sims": int(n_sims),
                "solver": f"status={plan.status} gap={plan.mip_gap}",
                "notes": notes,
                "gw1": {
                    "squad": [int(c) for c in d0.squad],
                    "starting_xi": [int(c) for c in d0.starting_xi],
                    "bench": [int(c) for c in d0.bench],
                    "captain": int(d0.captain),
                    "vice_captain": int(d0.vice_captain),
                    "chip": d0.chip,
                    "bank_after": int(d0.bank_after.tenths),
                },
            }
            root = Path(__file__).resolve().parents[2]
            out = root / "data" / "warehouse" / "gw1_plan.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(artefact, indent=2))
            typer.echo(f"\nplan committed: {out} (mode {label})")

            # Persist the forecast the plan was solved against, straight from
            # the problem's own arrays (zero extra simulation). The weekly
            # report's transfer section reads this, so the plan and the
            # transfer advice share ONE source of truth -- they used to read
            # different ones, which is how the report could show a full squad
            # while claiming no forecast was configured.
            import pandas as pd

            frames = []
            for k, g in enumerate(problem.gws):
                frames.append(pd.DataFrame({
                    "code": [int(pl.code) for pl in problem.players],
                    "gw": int(g),
                    "xpts": problem.xpts[:, k],
                    "p_play": problem.p_play[:, k],
                }))
            fc = pd.concat(frames, ignore_index=True)
            fc_path = root / "data" / "warehouse" / "forecast.parquet"
            fc.to_parquet(fc_path, index=False)
            typer.echo(f"forecast committed: {fc_path} "
                       f"({len(fc)} rows, GW{int(problem.gws[0])}-{int(problem.gws[-1])})")
