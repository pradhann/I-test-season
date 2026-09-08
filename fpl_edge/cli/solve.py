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
from typing import TYPE_CHECKING

import typer

from fpl_edge.store.warehouse import DEFAULT_DB

if TYPE_CHECKING:
    import pandas as pd


#: The currencies ``forecast.parquet`` may be denominated in.
#:
#: ``engine``             the engine's own points model (Dixon-Coles goals x GBM
#:                        minutes x rates, sampled) -- the historical default.
#: ``consensus``          the equal-weight provider mean, ``sem_projection_consensus``,
#:                        the number every other dashboard surface shows.
#: ``consensus_weighted`` the earned-weight blend, ``sem_projection_consensus_weighted``.
#:
#: On 2026-09-07 the engine model ran ~40% hot against the provider consensus in
#: every position (GW4 means: GK 1.42 vs 0.97, DEF 2.00 vs 1.46, MID 1.97 vs
#: 1.44, FWD 2.28 vs 1.33) and rated a GK away at Chelsea 29.2 xPts over GW4-8
#: against a four-provider 12.1. The squad-anchored solver was optimising a
#: currency the owner never saw. A consensus-denominated forecast lets the
#: solver and the dashboard argue in the same units.
FORECAST_SOURCES = ("engine", "consensus", "consensus_weighted")

#: Sidecar written beside forecast.parquet: the mode, the row split, the
#: coverage per gameweek. The parquet carries the same facts per row.
FORECAST_META_NAME = "forecast.meta.json"

_CONSENSUS_VIEW = {
    "consensus": "sem_projection_consensus",
    "consensus_weighted": "sem_projection_consensus_weighted",
}


def engine_forecast_frame(problem) -> pd.DataFrame:
    """The problem's own arrays as a long (code, gw) table. Zero simulation."""
    import pandas as pd

    frames = []
    for k, g in enumerate(problem.gws):
        frames.append(pd.DataFrame({
            "code": [int(pl.code) for pl in problem.players],
            "gw": int(g),
            "xpts": problem.xpts[:, k],
            "p_play": problem.p_play[:, k],
        }))
    return pd.concat(frames, ignore_index=True)


def consensus_forecast_frame(wh, *, season: str, as_of, gws, source: str) -> pd.DataFrame:
    """Provider consensus for the horizon, one row per (code, gw) the providers cover.

    ``xpts`` is the view's ``xpts_mean`` verbatim. ``p_appear`` is the mean of
    the providers' own P(any minutes) where at least one serves it, else null
    -- the caller decides what fills a null, this function never does. Rows
    whose ``xpts_mean`` is null (the weighted view with no earned provider on
    the player) are dropped: not covered is not covered.
    """
    view = _CONSENSUS_VIEW[source]
    extra = (", c.n_weighted_sources, c.weights_fit_id"
             if source == "consensus_weighted" else "")
    lo, hi = int(min(gws)), int(max(gws))
    frame = wh.sql(
        f"""
        WITH c AS (
            SELECT gw, code, xpts_mean, n_sources{extra.replace('c.', '')}
            FROM {view}(?::TIMESTAMPTZ)
            WHERE season = ? AND gw BETWEEN ? AND ? AND xpts_mean IS NOT NULL
        ), pa AS (
            SELECT gw, code, AVG(p_appear) AS p_appear
            FROM sem_projections(?::TIMESTAMPTZ)
            WHERE season = ? AND gw BETWEEN ? AND ? AND p_appear IS NOT NULL
            GROUP BY gw, code
        )
        SELECT c.gw, c.code, c.xpts_mean AS xpts, pa.p_appear, c.n_sources{extra}
        FROM c LEFT JOIN pa ON pa.gw = c.gw AND pa.code = c.code
        """,
        [as_of, season, lo, hi, as_of, season, lo, hi],
    )
    want = {int(g) for g in gws}
    frame = frame[frame["gw"].astype(int).isin(want)].copy()
    frame["code"] = frame["code"].astype(int)
    frame["gw"] = frame["gw"].astype(int)
    if "p_appear" in frame.columns:
        frame["p_appear"] = frame["p_appear"].astype(float).clip(lower=0.0, upper=1.0)
    return frame.reset_index(drop=True)


def build_forecast(problem, *, source: str = "engine", wh=None, season: str | None = None,
                   as_of=None) -> tuple[pd.DataFrame, dict]:
    """The forecast table for ``forecast.parquet`` plus its provenance record.

    Every row is denominated in exactly ONE source and says which:

    * ``engine``: every row from the problem's arrays, ``source = "engine"``.
    * ``consensus*``: a (code, gw) the providers cover takes the view's
      ``xpts_mean`` (``source = "consensus"``); one they do not is FILLED
      from the engine arrays (``source = "engine_fill"``). ``p_play`` on a
      consensus row is the providers' mean ``p_appear`` when any serves it,
      else the engine minutes model -- ``p_play_source`` records which.

    Never a scale, never a blend: a row's xpts is one source's number, and
    the ``forecast_source`` column names the mode the whole table was built
    under so a reader can tell "engine" from "engine_fill inside a consensus
    forecast" at a glance.
    """
    import numpy as np
    import pandas as pd

    if source not in FORECAST_SOURCES:
        raise ValueError(f"forecast source must be one of {FORECAST_SOURCES}, not {source!r}")
    eng = engine_forecast_frame(problem)
    eng["code"] = eng["code"].astype(int)
    eng["gw"] = eng["gw"].astype(int)
    gws = [int(g) for g in problem.gws]

    if source == "engine":
        out = eng.assign(source="engine", forecast_source="engine",
                         p_play_source="engine", n_sources=0)
        meta = _forecast_meta(out, source=source, gws=gws)
        return out[_FORECAST_COLUMNS], meta

    if wh is None or season is None or as_of is None:
        raise ValueError("a consensus forecast needs the warehouse, season and as_of")
    cons = consensus_forecast_frame(wh, season=season, as_of=as_of, gws=gws, source=source)
    cons = cons[cons["code"].isin(set(eng["code"]))]
    merged = eng.merge(
        cons.rename(columns={"xpts": "xpts_cons"}), on=["code", "gw"], how="left",
    )
    covered = merged["xpts_cons"].notna().to_numpy()
    p_served = covered & merged["p_appear"].notna().to_numpy()
    out = pd.DataFrame({
        "code": merged["code"].astype(int),
        "gw": merged["gw"].astype(int),
        "xpts": np.where(covered, merged["xpts_cons"].to_numpy(dtype=float),
                         merged["xpts"].to_numpy(dtype=float)),
        "p_play": np.where(p_served, merged["p_appear"].to_numpy(dtype=float),
                           merged["p_play"].to_numpy(dtype=float)),
        "source": np.where(covered, "consensus", "engine_fill"),
        "forecast_source": source,
        "p_play_source": np.where(p_served, "consensus", "engine"),
        "n_sources": merged["n_sources"].fillna(0).astype(int),
    })
    meta = _forecast_meta(out, source=source, gws=gws)
    if source == "consensus_weighted" and "weights_fit_id" in merged.columns:
        fits = merged["weights_fit_id"].dropna().unique().tolist()
        meta["weights_fit_id"] = fits[0] if len(fits) == 1 else fits
    return out[_FORECAST_COLUMNS], meta


_FORECAST_COLUMNS = ["code", "gw", "xpts", "p_play", "source", "forecast_source",
                     "p_play_source", "n_sources"]


def _forecast_meta(frame, *, source: str, gws: list[int]) -> dict:
    by_source = frame["source"].value_counts().to_dict()
    n = len(frame)
    fill = int(by_source.get("engine_fill", 0))
    per_gw = {}
    for g, grp in frame.groupby("gw"):
        counts = grp["source"].value_counts().to_dict()
        per_gw[str(int(g))] = {k: int(v) for k, v in counts.items()}
    return {
        "forecast_source": source,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "gws": gws,
        "rows": n,
        "rows_by_source": {k: int(v) for k, v in by_source.items()},
        "engine_fill_share": (fill / n) if n else None,
        "p_play_from_consensus_rows": int((frame["p_play_source"] == "consensus").sum()),
        "coverage_by_gw": per_gw,
    }


def describe_forecast(meta: dict) -> str:
    """One line a human can read: the mode and how much of it is the fill."""
    rows = meta["rows_by_source"]
    parts = [f"{k}={v}" for k, v in sorted(rows.items())]
    share = meta.get("engine_fill_share")
    fill = ("" if share is None or share == 0
            else f"; engine_fill {share:.1%} of rows")
    gws_fill = [g for g, c in meta.get("coverage_by_gw", {}).items()
                if c.get("engine_fill") and not c.get("consensus")]
    tail = f"; GW{','.join(gws_fill)} entirely engine_fill" if gws_fill else ""
    return f"forecast source {meta['forecast_source']}: {', '.join(parts)}{fill}{tail}"


def _commit_forecast(problem, root, *, source: str = "engine", wh=None,
                     season: str | None = None, as_of=None) -> pd.DataFrame:
    """Persist the forecast the plan is (or would be) solved against.

    The squad-anchored solver (``fpl recommend``) and the weekly report read
    this, so the plan and the transfer advice share ONE source of truth.
    Committed BEFORE the MILP: on 2026-09-07 a 30s solve found no incumbent
    and the fitted forecast was thrown away with the plan. The sidecar
    ``forecast.meta.json`` carries the provenance the CLI prints.
    """
    fc, meta = build_forecast(problem, source=source, wh=wh, season=season, as_of=as_of)
    fc_path = root / "data" / "warehouse" / "forecast.parquet"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    fc.to_parquet(fc_path, index=False)
    (fc_path.parent / FORECAST_META_NAME).write_text(json.dumps(meta, indent=2))
    typer.echo(f"forecast committed: {fc_path} "
               f"({len(fc)} rows, GW{int(problem.gws[0])}-{int(problem.gws[-1])})")
    typer.echo(describe_forecast(meta))
    return fc


def _problem_with_forecast(problem, frame):
    """The same problem, its xpts/p_play read back from a committed table.

    Used when the solve runs in a consensus currency: the MILP must optimise
    the numbers the artefact says it optimised, never the engine's while the
    parquet says consensus.
    """
    import dataclasses

    from fpl_edge.opt.problem import _pivot

    return dataclasses.replace(
        problem,
        xpts=_pivot(frame, problem.players, problem.gws, "xpts", float),
        p_play=_pivot(frame, problem.players, problem.gws, "p_play", float),
    )


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
    forecast_source: str = typer.Option(
        "engine", "--forecast-source",
        help="engine | consensus | consensus_weighted. The currency the committed "
             "forecast (and any plan solved here) is denominated in. 'engine' is "
             "the engine's own points model; 'consensus' the equal-weight provider "
             "mean every dashboard surface shows; 'consensus_weighted' the "
             "earned-weight blend. Consensus modes FILL uncovered (player, gw) "
             "rows from the engine model and label them engine_fill; nothing is "
             "scaled or blended within a row.",
    ),
    forecast_only: bool = typer.Option(
        False, "--forecast-only",
        help="Fit the models and commit forecast.parquet, then stop: no MILP. "
             "The forecast is the fit, not the plan; the daily refresh uses this "
             "so a solver that finds no incumbent can never lose the forecast.",
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
    if forecast_source not in FORECAST_SOURCES:
        raise typer.BadParameter(
            f"forecast-source must be one of {', '.join(FORECAST_SOURCES)}"
        )

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
        now = dt.datetime.now(dt.UTC)
        snap_now = wh.snapshot_at(now)
        target = int(gw) if gw is not None else int(snap_now.next_gw(season))
        deadline = snap_now.deadline(season, target)
        snap = wh.snapshot_at(max(now, deadline))
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

        fc_kwargs = {"source": forecast_source, "wh": wh, "season": season,
                     "as_of": snap.as_of}
        root = Path(__file__).resolve().parents[2]
        if forecast_only:
            _commit_forecast(problem, root, **fc_kwargs)
            typer.echo("forecast-only: models fitted and forecast committed; no plan solved.")
            return

        # Commit first (a solve with no incumbent must not lose the fit), and
        # in a consensus currency re-read the arrays so the MILP optimises
        # exactly what the parquet says it did.
        committed = _commit_forecast(problem, root, **fc_kwargs) if commit else None
        if forecast_source != "engine":
            if committed is None:
                committed, _ = build_forecast(problem, **fc_kwargs)
            problem = _problem_with_forecast(problem, committed)

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
                "snapshot_as_of": (max(now, deadline)).isoformat(),
                "season": season,
                "horizon_gws": [int(g) for g in gws],
                "objective_mode": label,
                "objective": float(plan.objective),
                "n_sims": int(n_sims),
                "forecast_source": forecast_source,
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
            out = root / "data" / "warehouse" / "gw1_plan.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(artefact, indent=2))
            typer.echo(f"\nplan committed: {out} (mode {label})")
