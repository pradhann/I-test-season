"""The five deadline-relative task bodies.

Moved out of ``fpl_edge/jobs/deadline_dag.py`` (ARCHITECTURE_REVIEW.md check 6,
C1 loop A, step B). ``pipelines/registry.py`` ran these five through
``from fpl_edge.jobs import deadline_dag as dag`` while ``deadline_dag`` needed
``registry.stale_window_of`` and ``registry.registry_due`` back, which is the
cycle. They belong on the pipelines side of the line: the registry is the only
thing that schedules them, and the tick in ``deadline_dag`` now imports them
the same way the registry does.

Each one is a decision plus a delivery, and every decision is deterministic:
a comparison against a named threshold, with the observations behind it
written to ``dag_observation`` on every run, fired or not. The model is never
in the loop before a decision, only after it in ``deadline_dag.polish_copy``.

Imports go down to ``pipelines.contracts`` and sideways to ``ingest``,
``myteam`` and ``opt``. This module must never import
``fpl_edge.pipelines.registry`` or ``fpl_edge.jobs``.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

from fpl_edge.pipelines.contracts import (
    ODDS_TASK,
    Step,
    TaskContext,
    TaskResult,
    _module_exists,
    run_step,
)

log = logging.getLogger("fpl_edge.pipelines.tasks")

UTC = dt.UTC

#: Where `fpl solve --commit` leaves the plan the T-4h delivery reads.
PLAN_GLOB = "gw*_plan.json"
PLAN_DIR = Path("data/warehouse")

#: The rung that also refreshes correct score / BTTS / team totals, which have
#: their own 200-credit monthly ledger and cost 42 a run. Once per gameweek,
#: at the earliest rung, so a failure has two later rungs' worth of time to be
#: noticed before the deadline.
ODDS_EXTRAS_AT = dt.timedelta(hours=36)

#: Net transfers per hour, on the watched set, that counts as a price move
#: worth waking someone for. A FIRST GUESS -- which is exactly why every run
#: writes its observations to dag_observation whether or not it fires. Tune
#: this against what actually preceded price changes, not against intuition.
VELOCITY_THRESHOLD = 12_000.0

#: How many of the most-owned players the radar watches in addition to whatever
#: is in the squad. Price moves on the template are actionable even when the
#: player is not owned: they move the price you will pay.
TOP_OWNED_N = 20


def _fmt_delta(target: dt.datetime | None, now: dt.datetime) -> str:
    if target is None:
        return "unknown"
    secs = (target.astimezone(UTC) - now.astimezone(UTC)).total_seconds()
    sign = "" if secs >= 0 else "-"
    secs = abs(secs)
    return f"{sign}{int(secs // 3600)}h{int((secs % 3600) // 60):02d}m"


def presser_projection_refresh(ctx: TaskContext) -> TaskResult:
    """T-30h: refetch what press conferences and projection sites just changed."""
    py = ctx.python
    steps = [
        run_step("ingest_live",
                 [py, "scripts/ingest_live.py", "--db", str(ctx.db_path)]),
        run_step("ingest_odds_fixtures",
                 [py, "scripts/ingest_odds.py", "--fixtures",
                  "--db", str(ctx.db_path)]),
        run_step("ingest_content",
                 [py, "-m", "fpl_edge.ingest.content.pipeline",
                  "--db", str(ctx.db_path), "ingest",
                  "--backfill-days", "2"]),
        # Refresh the cached fixture artefacts so the ticker's colours reflect
        # any midweek results and rescheduled fixtures the ingest above just
        # landed. Fits from a read copy, writes only parquets -- it cannot
        # contend with the other steps for the DB lock. This ran
        # `models.team_goals.ratings_cache` until that module was merged into
        # platform/scripts/fixtures/build.py; one fit now writes all three.
        run_step("fixture_ratings_build",
                 [py, "-m", "fpl_edge.platform.scripts.fixtures", "--build",
                  "--season", ctx.season, "--db", str(ctx.db_path)]),
    ]

    projections_cli = "fpl_edge.ingest.projections.cli"
    if _module_exists(projections_cli):
        steps.append(run_step(
            "ingest_projections",
            [py, "-m", projections_cli, "ingest", "--season", ctx.season,
             "--first-gw", str(ctx.gw), "--last-gw", str(ctx.gw + 5)],
        ))
    else:
        steps.append(Step(name="ingest_projections", ok=True, seconds=0.0,
                          detail="skipped: no projections CLI in this build yet"))

    with ctx.read() as wh:
        counts = wh.sql(
            "SELECT count(*) AS n, max(as_of) AS as_of FROM fact_player_state "
            "WHERE season = ? AND as_of = (SELECT max(as_of) FROM fact_player_state "
            "WHERE season = ?)",
            [ctx.season, ctx.season],
        )
        news = wh.sql(
            "SELECT p.web_name, s.news, s.status, s.chance_of_playing_next_round AS chance "
            "FROM fact_player_state s "
            "JOIN dim_player p ON p.season = s.season AND p.code = s.code "
            "WHERE s.season = ? AND s.news_added IS NOT NULL "
            "  AND s.news_added >= ? AND s.news IS NOT NULL AND s.news <> '' "
            "QUALIFY row_number() OVER (PARTITION BY s.code ORDER BY s.as_of DESC, "
            "        p.as_of DESC) = 1 "
            "ORDER BY s.news_added DESC LIMIT 15",
            [ctx.season, ctx.now.astimezone(UTC) - dt.timedelta(hours=48)],
        )

    n_players = int(counts["n"].iloc[0]) if len(counts) else 0
    snap_as_of = counts["as_of"].iloc[0] if len(counts) else None

    lines = [f"GW{ctx.gw} deadline in {_fmt_delta(ctx.deadline_utc, ctx.now)}.", ""]
    lines.append(f"Player snapshot: {n_players} rows"
                 + (f" as of {snap_as_of}" if snap_as_of is not None else ""))
    for s in steps:
        mark = "ok" if s.ok else "FAILED"
        lines.append(f"  {s.name}: {mark} ({s.seconds}s)"
                     + (f" -- {s.detail}" if s.detail and not s.ok else ""))
        if s.ok and s.detail.startswith("skipped:"):
            lines[-1] = f"  {s.name}: {s.detail}"
    lines.append("")
    if len(news):
        lines.append(f"Injury news in the last 48h ({len(news)}):")
        for r in news.itertuples(index=False):
            chance = "" if r.chance is None else f" [{int(r.chance)}%]"
            lines.append(f"  {r.web_name} ({r.status}){chance}: {str(r.news)[:120]}")
    else:
        lines.append("No injury news added in the last 48h.")

    # Open watchlist items, so every pre-deadline delivery reminds the user
    # what they said they wanted. Empty (or absent table) contributes nothing.
    from fpl_edge.interfaces.watchlist import digest_lines as _watchlist_lines

    with ctx.read() as wh:
        watch = _watchlist_lines(wh, ctx.season)
    if watch:
        lines.append("")
        lines.extend(watch)

    failed = [s.name for s in steps if not s.ok]
    detail = f"{len(steps) - len(failed)}/{len(steps)} steps ok, {len(news)} news items"
    if failed:
        detail += "; failed: " + ",".join(failed)
    return TaskResult(
        outcome="delivered", detail=detail, kind="digest",
        title=f"T-30h refresh, GW{ctx.gw}", body="\n".join(lines), steps=steps,
    )


def _held_codes(ctx: TaskContext) -> tuple[frozenset[int], str]:
    """Codes in the confirmed squad, from local state only.

    FPL_EDGE_DISABLE_PRIVATE is honoured by never reaching for the authenticated
    endpoints at all: this reads the locally confirmed squad record, which is
    what `fpl myteam confirm` wrote. With the flag set we do not even do that,
    so a test run cannot touch private state.
    """
    if os.environ.get("FPL_EDGE_DISABLE_PRIVATE", "") not in ("", "0"):
        return frozenset(), "private state disabled"
    try:
        from fpl_edge.config import owner_entry_id
        from fpl_edge.myteam.store import MyTeamStore

        # A scheduled task has no request, and this one is the operator's, so
        # it reads the owner's entry id. Through config rather than through
        # the user context: pipelines does not import platform, and this
        # needs the id rather than the whole context.
        store = MyTeamStore(owner_entry_id())
        record = store.confirmed(season=ctx.season)
        if record is None:
            return frozenset(), "no confirmed squad"
        return frozenset(int(c) for c in record.codes), "confirmed squad"
    except Exception as exc:  # noqa: BLE001 - the radar still runs on top-owned
        log.info("no local squad state (%s)", type(exc).__name__)
        return frozenset(), f"squad unavailable ({type(exc).__name__})"


def price_radar(ctx: TaskContext) -> TaskResult:
    """Nightly 02:00 UK: net-transfer velocity between the last two snapshots.

    Deterministic by construction -- the fire/no-fire decision is a comparison
    of a float against VELOCITY_THRESHOLD, auditable months later from the
    dag_observation rows this writes on every run.
    """
    with ctx.read() as wh:
        snaps = wh.sql(
            "SELECT DISTINCT as_of FROM fact_player_state WHERE season = ? "
            "ORDER BY as_of DESC LIMIT 2",
            [ctx.season],
        )
        if len(snaps) < 2:
            return TaskResult(
                outcome="no_source",
                detail=f"need two player-state snapshots, have {len(snaps)}",
            )
        newest = snaps["as_of"].iloc[0]
        prev = snaps["as_of"].iloc[1]
        df = wh.sql(
            "WITH a AS (SELECT code, transfers_in_event AS ti, transfers_out_event AS to_, "
            "                  selected_by_pct, price_tenths "
            "           FROM fact_player_state WHERE season = ? AND as_of = ?), "
            "     b AS (SELECT code, transfers_in_event AS ti, transfers_out_event AS to_ "
            "           FROM fact_player_state WHERE season = ? AND as_of = ?) "
            "SELECT a.code, p.web_name, a.selected_by_pct, a.price_tenths, "
            "       a.ti AS ti_now, a.to_ AS to_now, b.ti AS ti_prev, b.to_ AS to_prev "
            "FROM a JOIN b USING (code) "
            "JOIN dim_player p ON p.season = ? AND p.code = a.code "
            "QUALIFY row_number() OVER (PARTITION BY a.code ORDER BY p.as_of DESC) = 1",
            [ctx.season, newest, ctx.season, prev, ctx.season],
        )

    hours = (newest - prev).total_seconds() / 3600.0
    if hours <= 0:
        return TaskResult(outcome="no_source", detail="snapshot timestamps not ordered")

    # FPL's transfers_*_event counters reset to zero at each deadline. A window
    # that straddles one would read every player as a huge net OUTflow. Detect
    # it from the aggregate (which only ever grows within a gameweek) and stay
    # quiet rather than firing on an artefact.
    if float(df["ti_now"].sum()) < float(df["ti_prev"].sum()):
        return TaskResult(
            outcome="quiet",
            detail=f"gameweek transfer counters reset between {prev} and {newest}",
        )

    df["net_now"] = df["ti_now"].astype(float) - df["to_now"].astype(float)
    df["net_prev"] = df["ti_prev"].astype(float) - df["to_prev"].astype(float)
    df["velocity"] = (df["net_now"] - df["net_prev"]) / hours

    held, held_note = _held_codes(ctx)
    top_owned = set(
        df.nlargest(TOP_OWNED_N, "selected_by_pct")["code"].astype(int).tolist()
    )
    watched = set(held) | top_owned
    watch = df[df["code"].astype(int).isin(watched)].copy()

    # The tuning series, stored whether or not anything fires.
    obs = []
    for r in watch.itertuples(index=False):
        obs.append((int(r.code), "net_transfer_velocity_per_h", float(r.velocity)))
        obs.append((int(r.code), "net_transfers_event", float(r.net_now)))
        obs.append((int(r.code), "selected_by_pct", float(r.selected_by_pct or 0.0)))

    movers = watch[watch["velocity"].abs() >= VELOCITY_THRESHOLD].copy()
    movers = movers.reindex(movers["velocity"].abs().sort_values(ascending=False).index)

    detail = (
        f"window {hours:.1f}h, watched {len(watch)} "
        f"({len(held)} held via {held_note}), movers {len(movers)}, "
        f"threshold {VELOCITY_THRESHOLD:.0f}/h"
    )
    result = TaskResult(
        outcome="delivered" if len(movers) else "quiet", detail=detail, kind="alert",
        observations=obs,
    )
    if not len(movers):
        return result

    lines = [
        (
            f"Net-transfer velocity over the last {hours:.1f}h "
            f"(threshold {VELOCITY_THRESHOLD:,.0f}/h):"
        ),
        "",
    ]
    for r in movers.head(10).itertuples(index=False):
        arrow = "RISE risk" if r.velocity > 0 else "FALL risk"
        owned = " [owned]" if int(r.code) in held else ""
        lines.append(
            f"  {r.web_name}{owned}  {r.velocity:+,.0f}/h  "
            f"£{r.price_tenths / 10:.1f}m  {float(r.selected_by_pct or 0):.1f}% owned  {arrow}"
        )
    lines += ["", f"Snapshots: {prev} -> {newest}.",
              "Velocity is net transfers per hour, not FPL's own price algorithm."]
    result.title = f"Price radar: {len(movers)} mover(s)"
    result.body = "\n".join(lines)
    return result


def _freshest_plan(plan_dir: Path = PLAN_DIR) -> tuple[Path, dict] | None:
    best: tuple[dt.datetime, Path, dict] | None = None
    for path in sorted(plan_dir.glob(PLAN_GLOB)):
        try:
            obj = json.loads(path.read_text())
            gen = dt.datetime.fromisoformat(str(obj["generated_at"]))
        except Exception:  # noqa: BLE001 - a malformed artefact is not a plan
            continue
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=UTC)
        if best is None or gen > best[0]:
            best = (gen, path, obj)
    return None if best is None else (best[1], best[2])


def final_solve_delivery(ctx: TaskContext) -> TaskResult:
    """T-4h: deliver the freshest stored plan, or say honestly that there isn't one.

    This task NEVER solves. The MILP takes ~30 minutes; running it inside a tick
    would hold the process past the next launchd dispatch, and a solve that
    starts at T-4h and finishes at T-3h30 is a solve nobody asked for. The plan
    is produced by `make solve` (scripts/gw1_squad.py) and this reads the
    artefact -- so the failure mode is a stale plan clearly labelled stale,
    rather than a fresh-looking plan that missed the deadline.
    """
    found = _freshest_plan(PLAN_DIR)
    if found is None:
        return TaskResult(
            outcome="delivered", kind="alert",
            detail="no plan artefact found",
            title=f"GW{ctx.gw}: no solve to deliver",
            body=(
                f"Deadline in {_fmt_delta(ctx.deadline_utc, ctx.now)} and there is no "
                f"plan artefact in {PLAN_DIR}/{PLAN_GLOB}.\n\n"
                "Run `make solve` if you want the optimiser's squad before the deadline. "
                "Nothing is being guessed here."
            ),
        )

    path, plan = found
    gen = dt.datetime.fromisoformat(str(plan["generated_at"]))
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=UTC)
    age = ctx.now.astimezone(UTC) - gen

    key = f"gw{ctx.gw}"
    block = plan.get(key) or plan.get("gw1") or {}
    codes = list(block.get("squad") or [])
    names: dict[int, str] = {}
    if codes:
        with ctx.read() as wh:
            df = wh.sql(
                "SELECT code, web_name FROM dim_player WHERE season = ? "
                "QUALIFY row_number() OVER (PARTITION BY code ORDER BY as_of DESC) = 1",
                [ctx.season],
            )
        names = {int(r.code): str(r.web_name) for r in df.itertuples(index=False)}

    def nm(code) -> str:
        return names.get(int(code), f"code {code}")

    if age > dt.timedelta(hours=24):
        return TaskResult(
            outcome="delivered", kind="alert",
            detail=f"stale plan: {path.name} is {age.total_seconds() / 3600:.1f}h old",
            title=f"GW{ctx.gw}: no fresh solve",
            body=(
                f"Deadline in {_fmt_delta(ctx.deadline_utc, ctx.now)}.\n\n"
                f"The newest plan ({path.name}) was generated {gen.isoformat()}, "
                f"{age.total_seconds() / 3600:.1f} hours ago. That is older than the "
                "24h freshness bar, so it is NOT being presented as this deadline's "
                "recommendation. Prices, injuries and ownership have moved since.\n\n"
                f"Its captain was {nm(block.get('captain'))}. "
                "Re-run `make solve` to get a plan for these conditions."
            ),
        )

    xi = list(block.get("starting_xi") or [])
    bench = list(block.get("bench") or [])
    lines = [
        (
            f"Deadline in {_fmt_delta(ctx.deadline_utc, ctx.now)}. "
            f"Plan generated {gen.isoformat()} ({age.total_seconds() / 3600:.1f}h ago)."
        ),
        "",
        f"Captain: {nm(block.get('captain'))}   Vice: {nm(block.get('vice_captain'))}",
    ]
    if block.get("chip"):
        lines.append(f"Chip: {block['chip']}")
    if xi:
        lines += ["", "XI: " + ", ".join(nm(c) for c in xi)]
    if bench:
        lines.append("Bench: " + ", ".join(nm(c) for c in bench))
    lines += [
        "",
        (
            f"Objective {plan.get('objective_mode', '?')} = "
            f"{float(plan.get('objective', 0.0)):.1f} over GWs "
            f"{plan.get('horizon_gws')}; {plan.get('n_sims', '?')} sims."
        ),
        f"Source: {path}",
    ]
    return TaskResult(
        outcome="delivered", kind="report",
        detail=f"plan {path.name} age {age.total_seconds() / 3600:.1f}h",
        title=f"GW{ctx.gw} final plan, C: {nm(block.get('captain'))}",
        body="\n".join(lines),
    )


def _ingest_lineups_step(ctx: TaskContext) -> Step:
    """Land the Pulselive confirmed-lineup feed, isolated like every writer.

    A subprocess for the same reason presser's ingests are: it opens the
    warehouse for writing, and a hung lock or a network stall inside it must
    not take the scheduler with it. Tests monkeypatch THIS seam to stay
    offline; the check below reads whatever the warehouse ends up holding, so
    a failed refresh degrades to "whatever the last poll saw", honestly
    labelled by the step record.
    """
    module = "fpl_edge.ingest.lineups"
    if not _module_exists(module):
        return Step(name="ingest_lineups", ok=True, seconds=0.0,
                    detail="skipped: no lineups module in this build yet")
    return run_step(
        "ingest_lineups",
        [ctx.python, "-m", module, "--season", ctx.season, "--db", str(ctx.db_path)],
        timeout=300,
    )


def lineup_captain_check(ctx: TaskContext) -> TaskResult:
    """T-90m: are your XI -- above all your captain -- actually starting?

    The feed is the Pulselive confirmed-lineup ingest (fpl_edge/ingest/
    lineups.py): official teamsheets, published ~T-60m before each kickoff.
    The refresh runs first; the check then reads ONLY confirmed rows from the
    warehouse. Nothing here ever falls back to predicted lineups -- an alert
    sourced from a feed that is wrong a third of the time would train the
    operator to ignore the one that is not.

    Outcomes: no plan is `no_source` (there is nothing to check a teamsheet
    against); a blank gameweek or unpublished teamsheets are `quiet`; published
    teamsheets deliver -- an ACT alert when the captain is missing from the XI,
    a report of starting/benched/absent otherwise.
    """
    step = _ingest_lineups_step(ctx)

    plan = _freshest_plan(PLAN_DIR)
    if plan is None:
        return TaskResult(outcome="no_source", steps=[step],
                          detail="no plan artefact to check lineups against")
    _, obj = plan
    block = obj.get(f"gw{ctx.gw}") or {}
    captain = block.get("captain")
    if captain is None:
        return TaskResult(outcome="quiet", steps=[step],
                          detail="plan has no captain for this gameweek")
    xi = [int(c) for c in (block.get("starting_xi") or block.get("squad") or [])]
    if int(captain) not in xi:
        xi.append(int(captain))

    with ctx.read() as wh:
        fx = wh.sql(
            "SELECT fixture_id, home_team_code, away_team_code FROM fact_fixture "
            "WHERE season = ? AND gw = ? QUALIFY row_number() OVER "
            "(PARTITION BY fixture_id ORDER BY as_of DESC) = 1",
            [ctx.season, ctx.gw],
        )
        confirmed = wh.sql(
            "SELECT l.fixture_id, l.code, l.started FROM fact_confirmed_lineup l "
            "JOIN (SELECT DISTINCT fixture_id FROM fact_fixture WHERE season = ? "
            "      AND gw = ?) f USING (fixture_id) "
            "WHERE l.season = ? QUALIFY row_number() OVER "
            "(PARTITION BY l.source, l.fixture_id, l.code ORDER BY l.as_of DESC) = 1",
            [ctx.season, ctx.gw, ctx.season],
        )
        players = wh.sql(
            "SELECT code, web_name, team_code FROM dim_player WHERE season = ? "
            "QUALIFY row_number() OVER (PARTITION BY code ORDER BY as_of DESC) = 1",
            [ctx.season],
        )

    if fx.empty:
        return TaskResult(outcome="quiet", steps=[step],
                          detail=f"no fixtures for GW{ctx.gw} (blank); nothing to check")

    ingest_note = f"ingest {'ok' if step.ok else 'FAILED'}"
    if confirmed.empty:
        return TaskResult(
            outcome="quiet", steps=[step],
            detail=f"no teamsheet published yet for GW{ctx.gw} ({ingest_note}); "
                   "the feed polls fixtures kicking off within 2.5h",
        )

    by_code = {int(r.code): r for r in players.itertuples(index=False)}
    fixture_by_team: dict[int, list[int]] = {}
    for r in fx.itertuples(index=False):
        for tc in (int(r.home_team_code), int(r.away_team_code)):
            fixture_by_team.setdefault(tc, []).append(int(r.fixture_id))
    published = set(int(f) for f in confirmed["fixture_id"])
    started_by = {
        (int(r.fixture_id), int(r.code)): bool(r.started)
        for r in confirmed.itertuples(index=False)
    }

    def classify(code: int) -> str:
        """starting | benched | absent | awaiting -- per published teamsheets."""
        row = by_code.get(code)
        fids = fixture_by_team.get(int(row.team_code), []) if row is not None else []
        out = "awaiting"
        for fid in fids:
            if fid not in published:
                continue
            got = started_by.get((fid, code))
            if got is True:
                return "starting"
            out = "benched" if got is False else "absent"
        return out

    def nm(code: int) -> str:
        row = by_code.get(int(code))
        return str(row.web_name) if row is not None else f"code {code}"

    groups: dict[str, list[str]] = {"starting": [], "benched": [], "absent": [],
                                    "awaiting": []}
    for code in xi:
        groups[classify(int(code))].append(nm(code))

    cap_status = classify(int(captain))
    who = nm(int(captain))
    lines = [
        f"Deadline in {_fmt_delta(ctx.deadline_utc, ctx.now)}. Confirmed teamsheets "
        f"published for {len(published)}/{len(fx)} GW{ctx.gw} fixture(s).",
        "",
    ]
    for label, names in (("Confirmed starting", groups["starting"]),
                         ("On the BENCH", groups["benched"]),
                         ("ABSENT from the squad", groups["absent"]),
                         ("Teamsheet not out yet", groups["awaiting"])):
        if names:
            lines.append(f"{label}: " + ", ".join(names))
    detail = (
        f"captain {who} {cap_status}; XI: {len(groups['starting'])} starting, "
        f"{len(groups['benched'])} benched, {len(groups['absent'])} absent, "
        f"{len(groups['awaiting'])} awaiting ({ingest_note})"
    )
    if cap_status in ("benched", "absent"):
        return TaskResult(
            outcome="delivered", kind="alert", steps=[step], detail=detail,
            title=f"ACT: captain {who} is not starting",
            body=(
                f"Confirmed lineups are out and {who}, your captain, is not in "
                f"the XI ({cap_status}).\n\n" + "\n".join(lines)
            ),
        )
    return TaskResult(
        outcome="delivered", kind="report", steps=[step], detail=detail,
        title=f"GW{ctx.gw} teamsheets, captain {who} {cap_status}",
        body="\n".join(lines),
    )


def odds_refresh(ctx: TaskContext) -> TaskResult:
    """One rung of the odds ladder: refetch the market, then say how old it is.

    Two properties this task exists to hold, both of them learned the hard way.

    **A refresh that fetched nothing is an error outcome, not a quiet one.**
    ``quiet`` means "the task ran and the deterministic trigger did not fire",
    which is what the price radar does on a calm night. It is emphatically NOT
    what an odds refresh does when the vendor refuses it: that is a fetch that
    did not happen, and it must go in ``dag_firing`` as ``error`` so the row is
    evidence rather than reassurance. The nine-day outage this ladder was built
    after consisted entirely of runs that fetched nothing and said ok.

    **The freshness is reported whether or not the fetch worked.** The digest
    line is built from :func:`~fpl_edge.ingest.odds.odds_freshness` reading the
    warehouse *after* the step, so a failed refresh produces a message that
    names the real age of the data rather than the failure alone. "odds-api
    refused AND anytime_scorer is 206h old" is a different sentence from
    "odds-api refused", and only the first one gets acted on.

    The refresh runs as a subprocess for the same reason every other step does
    -- the CLI opens the warehouse for writing and a hung lock must not take
    the scheduler with it -- and the CLI itself fetches before it takes the
    lock (:func:`~fpl_edge.ingest.odds.refresh_odds_api`), so twelve HTTP round
    trips at T-5h do not sit on the lock the solver is waiting for.
    """
    # A metered fetch must never happen by accident. This repo has already had
    # a unit test refresh the developer's real FPL tokens over the network as a
    # side effect of rendering a fixture (see tests/conftest.py), and an odds
    # rung is worse: it spends credits from a 500/month allowance, and the
    # firing key means the spend is not even repeatable. So the fetch is gated
    # on an explicit off-switch that the unit suite sets, and a gated run is
    # reported as `no_source` -- an honest gap -- rather than as success.
    if os.environ.get("FPL_EDGE_DISABLE_NETWORK_INGEST", "") not in ("", "0"):
        return TaskResult(
            outcome="no_source", kind="digest",
            detail="skipped: FPL_EDGE_DISABLE_NETWORK_INGEST is set; "
                   "no credits were spent and nothing was fetched",
        )

    py = ctx.python
    argv = [py, "scripts/ingest_odds.py", "--odds-api", "--season", ctx.season,
            "--db", str(ctx.db_path)]
    steps = [run_step("ingest_odds_props", argv)]

    # The earliest rung also refreshes correct score / BTTS / team totals,
    # which have their own monthly ledger and are the derivation layer's input.
    # Once per gameweek: they are 42 credits a run and they move far less than
    # the featured markets do.
    at_extras_rung = (
        ctx.deadline_utc is not None
        and abs((ctx.deadline_utc - ctx.due_utc) - ODDS_EXTRAS_AT)
        < dt.timedelta(minutes=30)
    )
    if at_extras_rung:
        steps.append(run_step(
            "ingest_odds_extras",
            [py, "scripts/ingest_odds_extras.py", "--season", ctx.season],
        ))
    else:
        steps.append(Step(
            name="ingest_odds_extras", ok=True, seconds=0.0,
            detail=f"skipped: extras run once a gameweek, at T-{ODDS_EXTRAS_AT}",
        ))

    # Freshness AFTER the steps, from a read copy: no lock contention with the
    # subprocess that just wrote.
    from fpl_edge.ingest.odds import freshness_summary, odds_freshness

    try:
        with ctx.read() as wh:
            fresh = freshness_summary(odds_freshness(wh, season=ctx.season,
                                                     now=ctx.now))
    except Exception as exc:  # noqa: BLE001 - a failed read is itself reportable
        fresh = {"ok": False, "stale_markets": ["<unreadable>"],
                 "error": f"{type(exc).__name__}: {exc}", "markets": []}

    failed = [s.name for s in steps if not s.ok]
    stale = list(fresh.get("stale_markets") or [])

    lines = [
        (f"GW{ctx.gw} deadline in {_fmt_delta(ctx.deadline_utc, ctx.now)} "
         f"(odds rung T-{_fmt_delta(ctx.deadline_utc, ctx.due_utc)})."),
        "",
    ]
    for s in steps:
        mark = "ok" if s.ok else "FAILED"
        if s.ok and s.detail.startswith("skipped:"):
            lines.append(f"  {s.name}: {s.detail}")
        else:
            lines.append(f"  {s.name}: {mark} ({s.seconds}s)"
                         + (f" -- {s.detail}" if s.detail and not s.ok else ""))
    lines.append("")
    lines.append("Market freshness:")
    for m in fresh.get("markets") or []:
        age = m.get("age_hours")
        age_s = "never fetched" if age is None else f"{age:.1f}h"
        flag = "  STALE" if m.get("stale") else ""
        lines.append(f"  {m['market']}: {age_s} "
                     f"(budget {m.get('max_age_hours')}h){flag}")

    detail = (f"{len(steps) - len(failed)}/{len(steps)} steps ok; "
              f"stale markets: {','.join(stale) if stale else 'none'}")
    if failed:
        detail = "failed: " + ",".join(failed) + "; " + detail

    observations = [
        (0, f"odds_age_h.{m['market']}", float(m["age_hours"]))
        for m in (fresh.get("markets") or [])
        if m.get("age_hours") is not None
    ]

    # An outcome of "error" whenever a step failed. The scheduled job and the
    # dag_firing row must both go red for a refresh that did not refresh --
    # that is the entire lesson of the 2026-08-19..28 outage.
    if failed:
        return TaskResult(
            outcome="error", kind="alert", steps=steps, detail=detail,
            observations=observations,
            title=f"ODDS REFRESH FAILED, GW{ctx.gw}, "
                  f"{_fmt_delta(ctx.deadline_utc, ctx.now)} to deadline",
            body=("The odds refresh did not complete. The prices the solver "
                  "will read at this deadline are the ones listed below, at "
                  "the ages listed below.\n\n" + "\n".join(lines)),
        )
    if stale:
        return TaskResult(
            outcome="delivered", kind="alert", steps=steps, detail=detail,
            observations=observations,
            title=f"Odds refreshed, {len(stale)} market(s) still stale, GW{ctx.gw}",
            body=("Every step succeeded and these markets are still outside "
                  "their freshness budget:\n\n" + "\n".join(lines)),
        )
    # Everything fetched and everything is inside budget. No message: an alert
    # that arrives three times a gameweek saying "fine" is an alert nobody
    # reads, and the observations above are still written.
    return TaskResult(outcome="quiet", detail=detail, steps=steps,
                      observations=observations)


#: The dispatch table the tick and the registry both read.
TASKS: dict[str, object] = {
    "presser_projection_refresh": presser_projection_refresh,
    "price_radar": price_radar,
    "final_solve_delivery": final_solve_delivery,
    "lineup_captain_check": lineup_captain_check,
    ODDS_TASK: odds_refresh,
}
