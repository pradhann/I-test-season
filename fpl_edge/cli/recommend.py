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
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import typer

from fpl_edge.store.warehouse import DEFAULT_DB

UTC = dt.UTC

TRANSFER_PLAN_NAME = "transfer_plan.json"

#: The chips in the optimiser's vocabulary (OptimizerConfig.allowed_chips).
CHIPS = ("wildcard", "freehit", "bboost", "3xc")

_BANNED_OUTSIDE_UNIVERSE = re.compile(r"banned player (\d+) is not in the universe")


def parse_codes(raw: str, *, flag: str) -> frozenset[int]:
    """``"123,456"`` -> ``{123, 456}``; anything else is a usage error."""
    out: set[int] = set()
    for piece in (raw or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        if not piece.isdigit():
            raise typer.BadParameter(
                f"{flag} takes comma-separated player codes; {piece!r} is not one",
                param_hint=flag,
            )
        out.add(int(piece))
    return frozenset(out)


def register(app: typer.Typer) -> None:
    app.command("recommend")(recommend_cmd)


def forecast_provenance(frame, gws=None) -> dict[str, Any]:
    """What currency the committed forecast is in, read off its own rows.

    ``fpl solve --forecast-source`` stamps every row with ``forecast_source``
    (the mode) and ``source`` (``engine`` | ``consensus`` | ``engine_fill``).
    A parquet written before those columns existed was written by the engine
    model -- the only writer there ever was -- so it reads as ``engine`` with
    an unmeasured fill share (None, never 0.0). Restricted to the horizon
    gameweeks when given, so the share describes the rows the solve used.
    """
    sub = frame
    if gws is not None and "gw" in frame.columns:
        want = {int(g) for g in gws}
        sub = frame[frame["gw"].astype(int).isin(want)]
    if "forecast_source" not in sub.columns or "source" not in sub.columns:
        return {"forecast_source": "engine", "engine_fill_share": None,
                "rows_by_source": None}
    modes = sub["forecast_source"].dropna().unique().tolist()
    mode = modes[0] if len(modes) == 1 else "mixed"
    counts = {str(k): int(v) for k, v in sub["source"].value_counts().items()}
    n = int(sum(counts.values()))
    fill = counts.get("engine_fill", 0)
    return {
        "forecast_source": str(mode),
        "engine_fill_share": (fill / n) if n else None,
        "rows_by_source": counts,
    }


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
    chips_allowed: bool = True,
    max_hits: int = -1,
    unconstrained=None,
    generated_at: dt.datetime,
    max_candidates: int,
    seconds: float,
    constraints: dict[str, Any] | None = None,
    forecast: dict[str, Any] | None = None,
    squad_before: Sequence[int] | None = None,
    squad_source: str | None = None,
) -> dict[str, Any]:
    """The transfer_plan.json payload, pure and testable without a MILP.

    ``chosen`` carries the first-gameweek decision (captain, vice, XI) straight
    from the plan's own :class:`~fpl_edge.opt.plan.GwDecision` -- the read side
    never re-derives a lineup. Money serialises as tenths; the objective stays
    in the mode's own currency, named by ``objective_mode``; the forecast that
    currency was computed from is named by ``forecast_source`` (``engine`` |
    ``consensus`` | ``consensus_weighted``) with ``forecast_engine_fill_share``
    saying how much of it the engine model filled in -- so a gain reads
    "vs rolling, consensus forecast", not just "vs rolling".
    """
    fc = dict(forecast or {})
    d0 = rec.chosen.plan.decisions[0]
    chosen = _serialize_move(rec.chosen)
    chosen.update({
        "bank_after_tenths": int(rec.chosen.bank_after.tenths),
        "captain": int(d0.captain),
        "vice_captain": int(d0.vice_captain),
        "starting_xi": [int(c) for c in d0.starting_xi],
    })
    before = sorted(int(c) for c in (squad_before or ()))
    # A plan is a statement about ONE squad. `out` and `in` are diffed against
    # the squad held when the solve ran, so a reader who applies them to a
    # different fifteen gets a squad the optimiser never scored. On 2026-09-08
    # this surfaced as a starting XI naming a player the dashboard's own squad
    # card did not list: the plan was solved before the account was connected
    # and the two surfaces disagreed in silence. Recording the squad here lets
    # every reader check rather than assume; brief.py refuses a plan whose
    # squad_before no longer matches what the manager holds.
    if before:
        after = sorted((set(before) - set(chosen["out"])) | set(chosen["in"]))
        stray = sorted(set(chosen["starting_xi"]) - set(after))
        if stray:
            raise ValueError(
                f"plan is self-inconsistent: starting XI names {stray}, which "
                f"the post-transfer squad does not contain. Writing it would "
                f"publish a lineup the optimiser never scored."
            )
    return {
        "squad_before": before,
        "squad_source": (str(squad_source) if squad_source else None),
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
        # Whether the optimiser was allowed to spend a chip; a plan that plays one
        # is a different decision from a transfer plan and renders as such.
        # The hit cap the headline honoured, and the optimiser's top move when
        # that cap displaced it: visible beside the headline, never silently gone.
        "max_hits": int(max_hits),
        "unconstrained": (None if unconstrained is None else {
            **_serialize_move(unconstrained),
            "gain_over_roll": (float(unconstrained.objective - rec.roll.objective)
                               if rec.roll is not None else None),
        }),
        "chips_allowed": bool(chips_allowed),
        # The currency's provenance: which forecast the objective summed.
        "forecast_source": (str(fc["forecast_source"])
                            if fc.get("forecast_source") is not None else None),
        "forecast_engine_fill_share": (
            float(fc["engine_fill_share"])
            if fc.get("engine_fill_share") is not None else None),
        "forecast_rows_by_source": (
            {str(k): int(v) for k, v in fc["rows_by_source"].items()}
            if fc.get("rows_by_source") else None),
        # What the caller asked for, verbatim, so the Planner can say what a
        # standing plan was solved under before offering it as guidance.
        "constraints": dict(constraints or {}),
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
    chips: bool = typer.Option(
        True, "--chips/--no-chips",
        help="Let the optimiser play a chip inside the horizon. Chips are the "
             "owner's decision, not the objective's: with them allowed, the first "
             "GW4 run proposed a 10-change wildcard for +51 xPts. The dashboard "
             "runs --no-chips and shows chip plans separately.",
    ),
    max_hits: int = typer.Option(
        -1, "--max-hits",
        help="Most points-hits the HEADLINE move may take; -1 = unconstrained. "
             "Hit-taking moves are still solved and kept as the unconstrained "
             "best, so the trade is visible. The dashboard runs --max-hits 0.",
    ),
    chip: list[str] = typer.Option(
        None, "--chip",
        help="Allow only this chip (repeatable; one of wildcard, freehit, "
             "bboost, 3xc). Implies --chips. Without --chip, --chips allows "
             "all four.",
    ),
    must_keep: str = typer.Option(
        "", "--must-keep",
        help="Comma-separated player codes the squad must own in every "
             "gameweek of the horizon (OptimizerConfig.locked).",
    ),
    ban: str = typer.Option(
        "", "--ban",
        help="Comma-separated player codes the squad may never own; a banned "
             "player you hold is sold in the first gameweek "
             "(OptimizerConfig.banned). A banned player outside the solver's "
             "candidate universe could never have been bought, so he is "
             "dropped from the ban with a note rather than failing the solve.",
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
    keep_codes = parse_codes(must_keep, flag="--must-keep")
    ban_codes = set(parse_codes(ban, flag="--ban"))
    overlap = keep_codes & ban_codes
    if overlap:
        typer.echo(f"players both kept and banned: {sorted(overlap)}")
        raise typer.Exit(code=2)
    chip_names = tuple(dict.fromkeys(c.strip().lower() for c in (chip or [])))
    bad_chips = [c for c in chip_names if c not in CHIPS]
    if bad_chips:
        typer.echo(f"unknown chip(s) {bad_chips}; known: {list(CHIPS)}")
        raise typer.Exit(code=2)
    if chip_names:
        chips = True

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
    fc_frame = pd.read_parquet(fc_path)
    points_forecast = TablePointsForecast(
        frame=fc_frame, name="table:forecast.parquet"
    )

    with Warehouse.read_copy(db) as wh:
        typer.echo("Reconstructing squad…")
        try:
            state = current_state(wh, season, now)
        except Exception as exc:
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
        provenance = forecast_provenance(fc_frame, gws)
        share = provenance.get("engine_fill_share")
        typer.echo(
            f"forecast source: {provenance['forecast_source']}"
            + ("" if share is None else f" (engine_fill {share:.1%} of horizon rows)")
        )

        cfg_kwargs: dict[str, object] = {
            "mode": ObjectiveMode.EXPECTED_POINTS,
            "max_candidates_per_position": int(max_candidates),
            "solver": SolverConfig(time_limit_s=float(seconds), mip_gap_rel=5e-3),
            "locked": frozenset(keep_codes),
        }
        if not chips:
            cfg_kwargs["allowed_chips"] = frozenset()
        elif chip_names:
            cfg_kwargs["allowed_chips"] = frozenset(chip_names)
        held_now = {int(p.code) for p in (state.picks or ())}
        extra_notes: list[str] = []
        if keep_codes:
            typer.echo(f"must keep: {sorted(keep_codes)}")
        if ban_codes:
            typer.echo(f"banned: {sorted(ban_codes)}"
                       + (f" (held now: {sorted(ban_codes & held_now)}, sold in GW{gws[0]})"
                          if ban_codes & held_now else ""))
        typer.echo(
            f"Solving GW{gws[0]}..{gws[-1]} — free optimum, roll, and "
            f"{int(candidates)} candidate moves (≤{seconds:.0f}s each)…"
        )
        try:
            while True:
                cfg = OptimizerConfig(**cfg_kwargs, banned=frozenset(ban_codes))
                try:
                    rec = recommend(
                        snapshot,
                        state,
                        season=season,
                        gws=gws,
                        points_forecast=points_forecast,
                        # The surrogate, stated in writing — the same
                        # configuration the weekly report uses until the rank
                        # simulator ships a provider.
                        mode=ObjectiveMode.EXPECTED_POINTS,
                        config=cfg,
                        candidates=int(candidates),
                    )
                    break
                except ValueError as exc:
                    # A banned player the pruned universe never contained: the
                    # ban is vacuous, not an error. Drop him, say so, go again
                    # (the retry costs a problem build, no MILP has run yet).
                    m = _BANNED_OUTSIDE_UNIVERSE.search(str(exc))
                    if not m or int(m.group(1)) not in ban_codes:
                        typer.echo(f"Could not solve: {exc}")
                        raise typer.Exit(code=2) from exc
                    gone = int(m.group(1))
                    ban_codes.discard(gone)
                    note = (f"ban on {gone} dropped: outside the top-"
                            f"{int(max_candidates)}-per-position universe, so "
                            f"the optimiser could never have bought him")
                    extra_notes.append(note)
                    typer.echo(note)
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
        # The headline must respect the hit cap (the chat tool's rule since
        # day one): the optimiser's top move stays on the table as the
        # unconstrained best, never hidden, never the headline by default.
        unconstrained = None
        if max_hits >= 0 and rec.chosen.hits > max_hits:
            import dataclasses
            ranked = [rec.chosen, *rec.alternatives]
            within = next((mv for mv in ranked if mv.hits <= max_hits), None)
            if within is None and rec.roll is not None:
                within = rec.roll
            if within is not None:
                unconstrained = rec.chosen
                others = tuple(mv for mv in ranked if mv is not within)
                rec = dataclasses.replace(rec, chosen=within, alternatives=others)
                typer.echo(
                    f"Headline held to max_hits={max_hits}: {within.describe(index)} "
                    f"(the unconstrained best took {unconstrained.hits} hit(s))"
                )
        payload = serialize_recommendation(
            rec, generated_at=now, max_candidates=int(max_candidates),
            seconds=float(seconds), chips_allowed=bool(chips),
            max_hits=int(max_hits), unconstrained=unconstrained,
            forecast=provenance,
            squad_before=sorted(held_now),
            squad_source=getattr(state.provenance, "name", None),
            constraints={
                "horizon": int(horizon),
                "max_hits": int(max_hits),
                "chips": (list(chip_names) if chip_names
                          else (list(CHIPS) if chips else [])),
                "must_keep": sorted(keep_codes),
                "ban": sorted(ban_codes),
                "seconds": float(seconds),
                "max_candidates": int(max_candidates),
                "candidates": int(candidates),
            },
        )
        fc_note = f"forecast source: {provenance['forecast_source']}"
        if share is not None:
            fc_note += f"; engine_fill {share:.1%} of horizon rows"
        payload["notes"] = [*payload["notes"], *extra_notes, fc_note]
        out = root / "data" / "warehouse" / TRANSFER_PLAN_NAME
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, out)
        typer.echo(f"\ntransfer plan committed: {out} "
                   f"(mode {payload['objective_mode']})")
