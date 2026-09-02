"""``fpl recommend`` -- the transfer recommendation for YOUR fifteen.

``fpl solve`` is a from-scratch ideal-squad builder (heritage:
``scripts/gw1_squad.py``): no current-squad anchor, no transfer budget. The
question a manager actually asks at a deadline -- "what should I do with MY
15?" -- is answered by :func:`fpl_edge.myteam.recommend.recommend`, which
reconstructs the current squad, loads the committed points forecast
(``data/warehouse/forecast.parquet``, the artefact ``fpl solve`` commits), and
solves the free optimum, the roll, and every screened candidate move with the
same MILP and the same objective. This command is that machinery on the CLI,
wired exactly as the chat tool ``suggest_transfers`` wires it, and it commits
the answer as ``data/warehouse/transfer_plan.json`` -- the artefact the
dashboard's solver card renders.

``fpl solve`` stays the fuel producer: it fits the models and commits the
forecast this command consumes. No forecast, no recommendation -- the command
exits non-zero naming `uv run fpl solve` as the fix rather than inventing a
projection.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import typer

from fpl_edge.store.warehouse import DEFAULT_DB

UTC = dt.timezone.utc

TRANSFER_PLAN_NAME = "transfer_plan.json"


def register(app: typer.Typer) -> None:
    app.command("recommend")(recommend_cmd)


def _serialize_move(move) -> dict[str, Any]:
    """One solved move, in the artefact's vocabulary. Money as .tenths."""
    return {
        "out": [int(c) for c in move.out],
        "in": [int(c) for c in move.into],
        "n_transfers": int(move.n_transfers),
        "hits": int(move.hits),
        "hit_points": int(move.hit_points),
        "objective": float(move.objective),
        "chip": str(move.chip or ""),
        "label": str(move.label or ""),
    }


def serialize_recommendation(
    rec,
    *,
    generated_at: dt.datetime,
    max_candidates: int,
    seconds: float,
) -> dict[str, Any]:
    """The transfer_plan.json payload, pure and testable without a MILP.

    ``chosen`` carries the first-gameweek decision (captain, vice, XI) straight
    from the plan's own :class:`~fpl_edge.opt.plan.GwDecision` -- the read side
    never re-derives a lineup. Money serialises as tenths; the objective stays
    in the mode's own currency, named by ``objective_mode``.
    """
    d0 = rec.chosen.plan.decisions[0]
    chosen = _serialize_move(rec.chosen)
    chosen.update({
        "bank_after_tenths": int(rec.chosen.bank_after.tenths),
        "captain": int(d0.captain),
        "vice_captain": int(d0.vice_captain),
        "starting_xi": [int(c) for c in d0.starting_xi],
    })
    return {
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "season": str(rec.season),
        "gw": int(rec.gw),
        "horizon_gws": [int(g) for g in rec.horizon],
        "objective_mode": str(rec.mode.value),
        "free_transfers": int(rec.free_transfers),
        "unlimited_transfers": bool(rec.unlimited_transfers),
        "chosen": chosen,
        "roll": ({"objective": float(rec.roll.objective)}
                 if rec.roll is not None else None),
        "gain_over_roll": (float(rec.gain_over_roll)
                           if rec.gain_over_roll is not None else None),
        "alternatives": [_serialize_move(m) for m in rec.alternatives[:5]],
        "hit_verdicts": [v.to_dict() for v in rec.hit_verdicts],
        "notes": [str(n) for n in rec.notes],
        "n_candidates_screened": int(rec.n_candidates_screened),
        "n_candidates_solved": int(rec.n_candidates_solved),
        "solve_seconds": float(rec.solve_seconds),
        "bounds": (
            f"candidates capped at {int(max_candidates)}/position, "
            f"{seconds:.0f}s per MILP; a capped solve is best-found, "
            f"not a proven optimum"
        ),
    }


def recommend_cmd(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="Path to the DuckDB warehouse."),
    season: str = typer.Option("2026-27", "--season", help="Season in FPL's 2026-27 form."),
    horizon: int = typer.Option(5, "--horizon", help="Gameweeks the objective sums over."),
    seconds: float = typer.Option(
        60.0, "--seconds", help="MILP time limit per solve (free optimum, roll, "
                                "and each candidate move)."
    ),
    max_candidates: int = typer.Option(
        25, "--max-candidates",
        help="Player-universe cap per position for every MILP.",
    ),
    candidates: int = typer.Option(
        8, "--candidates", help="Screened candidate moves that get a full solve."
    ),
    commit: bool = typer.Option(
        True, "--commit/--no-commit",
        help="Persist data/warehouse/transfer_plan.json, the artefact the "
             "dashboard's solver card renders.",
    ),
) -> None:
    """The engine's transfer recommendation for the user's own squad.

    Reconstructs the current 15, loads the committed forecast, and solves the
    free optimum vs rolling vs every screened candidate move by the same MILP.
    """
    # Heavy imports live here so `fpl --help` stays fast.
    import pandas as pd

    from fpl_edge.myteam.forecast import (
        PointsForecastUnavailableError,
        TablePointsForecast,
    )
    from fpl_edge.myteam.recommend import NoSquadError, recommend
    from fpl_edge.myteam.report import current_state
    from fpl_edge.myteam.state import PlayerIndex
    from fpl_edge.opt import ObjectiveMode, OptimizerConfig, SolverConfig
    from fpl_edge.store import Warehouse

    now = dt.datetime.now(UTC)
    horizon = max(1, min(int(horizon), 8))

    root = Path(__file__).resolve().parents[2]
    fc_path = root / "data" / "warehouse" / "forecast.parquet"
    if not fc_path.exists():
        typer.echo(
            "No transfer recommendation: no points forecast is configured.\n"
            f"Expected {fc_path}.\n"
            "Fix: run `uv run fpl solve` -- it fits the models and commits "
            "data/warehouse/forecast.parquet, the exact artefact the weekly "
            "report's Transfers section reads."
        )
        raise typer.Exit(code=2)
    points_forecast = TablePointsForecast(
        frame=pd.read_parquet(fc_path), name="table:forecast.parquet"
    )

    with Warehouse.read_copy(db) as wh:
        typer.echo("Reconstructing squad…")
        try:
            state = current_state(wh, season, now)
        except Exception as exc:  # noqa: BLE001 - a dead endpoint must not raise
            typer.echo(
                "Could not reconstruct your squad from the FPL endpoints: "
                f"{type(exc).__name__}: {exc}\nNo recommendation is offered — "
                "guessing at the squad would make every line below it fiction."
            )
            raise typer.Exit(code=2) from exc
        snapshot = wh.snapshot_at(now)
        index = PlayerIndex.from_snapshot(snapshot, season)
        try:
            gw = int(snapshot.next_gw(season))
        except Exception:  # noqa: BLE001 - no calendar; use the state's own gw
            gw = int(state.gw)
        gws = list(range(gw, gw + horizon))

        cfg = OptimizerConfig(
            mode=ObjectiveMode.EXPECTED_POINTS,
            max_candidates_per_position=int(max_candidates),
            solver=SolverConfig(time_limit_s=float(seconds), mip_gap_rel=5e-3),
        )
        typer.echo(
            f"Solving GW{gws[0]}..{gws[-1]} — free optimum, roll, and "
            f"{int(candidates)} candidate moves (≤{seconds:.0f}s each)…"
        )
        try:
            rec = recommend(
                snapshot,
                state,
                season=season,
                gws=gws,
                points_forecast=points_forecast,
                # The surrogate, stated in writing — the same configuration the
                # weekly report uses until the rank simulator ships a provider.
                mode=ObjectiveMode.EXPECTED_POINTS,
                config=cfg,
                candidates=int(candidates),
            )
        except PointsForecastUnavailableError as exc:
            typer.echo(
                "No transfer recommendation: no points forecast is configured.\n\n"
                f"{exc}\n\n"
                "Fix: run `uv run fpl solve` -- it fits the models and commits "
                "data/warehouse/forecast.parquet, the exact artefact the weekly "
                "report's Transfers section reads."
            )
            raise typer.Exit(code=2) from exc
        except NoSquadError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=2) from exc

    typer.echo(rec.render(index))

    if commit:
        payload = serialize_recommendation(
            rec, generated_at=now, max_candidates=int(max_candidates),
            seconds=float(seconds),
        )
        out = root / "data" / "warehouse" / TRANSFER_PLAN_NAME
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, out)
        typer.echo(f"\ntransfer plan committed: {out} "
                   f"(mode {payload['objective_mode']})")
