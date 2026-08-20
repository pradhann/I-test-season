"""The `fpl` command line.

Subcommands are grouped by the object they act on rather than by the team that
built them, so `fpl idea ...` is the whole idea lifecycle: submit, review, track,
and the bot that feeds it. Other teams add their own sub-apps here as they land;
`weekly` already delegates to the section registry in
:mod:`fpl_edge.interfaces.report`, so a new report section needs no edit to this
file.

Every command takes ``--as-of``. Defaulting to "now" and offering no way to
override it is how a decision tool quietly becomes impossible to reproduce: the
same command run on Tuesday and Thursday gives different answers with no record
of why. With ``--as-of`` the warehouse is read at a fixed instant and the output
is reproducible.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from fpl_edge.interfaces.bias import MIN_OBSERVATIONS, review as run_review
from fpl_edge.interfaces.ideas import IdeaStatus
from fpl_edge.interfaces.inbox import IdeaInbox
from fpl_edge.interfaces.registry import IdeaRegistry
from fpl_edge.interfaces.report import weekly_report
from fpl_edge.interfaces.tracking import track as run_track
# Importing the package also registers the weekly report's `transfers` section.
from fpl_edge.myteam.cli import app as myteam_app
from fpl_edge.store import DEFAULT_DB, Warehouse

UTC = dt.timezone.utc
DEFAULT_SEASON = "2026-27"

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Rank-utility optimising FPL decision engine.",
)
idea_app = typer.Typer(no_args_is_help=True, help="The idea inbox: your hypotheses, tracked.")
app.add_typer(idea_app, name="idea")
app.add_typer(myteam_app, name="myteam")

# The hypothesis registry team's surface. Importing it also registers the
# theses section of the weekly report.
from fpl_edge.theses.cli import theses_app, thesis_app

app.add_typer(theses_app, name="theses")
app.add_typer(thesis_app, name="thesis")

console = Console()


def echo(text: str) -> None:
    """Print text that contains user input, with Rich markup disabled.

    Rich reads ``[...]`` as markup, which silently ate "[low confidence]" from
    every verdict line during development. The same mechanism is an echo surface:
    the raw text of an idea is the user's own words, and a message containing
    "[red]" or a link tag would render as formatting in the engine's output. Any
    string built from `raw_text`, a thesis or a rationale goes through here.
    """
    console.print(text, markup=False, highlight=False)


def _parse_as_of(value: str | None) -> dt.datetime:
    """Accept an ISO instant, or default to now. Always returns tz-aware UTC."""
    if not value:
        return dt.datetime.now(UTC)
    text = value.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        # Naive input would be interpreted in the host's zone, and the rule
        # registry is explicit that only UTC is authoritative here.
        raise typer.BadParameter("--as-of must carry a timezone, e.g. 2026-08-18T22:50:00Z")
    return parsed.astimezone(UTC)


def _warehouse(db: Path) -> Warehouse:
    if not db.exists():
        console.print(f"[red]No warehouse at {db}.[/] Run `make ingest` first.")
        raise typer.Exit(code=2)
    return Warehouse(db)


DbOpt = typer.Option(DEFAULT_DB, "--db", help="Path to the DuckDB warehouse.")
SeasonOpt = typer.Option(DEFAULT_SEASON, "--season", help="Season in FPL's 2026-27 form.")
AsOfOpt = typer.Option(None, "--as-of", help="Read the warehouse as of this UTC instant.")


# -- fpl idea submit ---------------------------------------------------------


@idea_app.command("submit")
def idea_submit(
    text: str = typer.Argument(..., help="The idea, in plain English."),
    db: Path = DbOpt,
    season: str = SeasonOpt,
    as_of: str = AsOfOpt,
    acted: bool = typer.Option(False, "--acted", help="You actually did this."),
    reply_to: str = typer.Option(
        None, "--reply-to",
        help="Conversation key, so a follow-up can answer a clarification.",
    ),
) -> None:
    """Log an idea from the terminal. Same path the bot uses."""
    when = _parse_as_of(as_of)
    with _warehouse(db) as wh:
        inbox = IdeaInbox(wh, season=season)
        sub = inbox.submit(
            text, source="cli", source_ref=reply_to, now=when, acted=acted
        )
        echo(sub.render())
        if sub.clarification is not None and not reply_to:
            console.print(
                "\n[dim]Pass --reply-to <any key> and rerun with your answer to "
                "resolve this.[/]"
            )
            raise typer.Exit(code=1)


# -- fpl idea review ---------------------------------------------------------


@idea_app.command("review")
def idea_review(
    db: Path = DbOpt,
    season: str = SeasonOpt,
    as_of: str = AsOfOpt,
    show_all: bool = typer.Option(False, "--all", help="List every idea, not just recent."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """How every idea you ever had actually performed, and what it says about you."""
    when = _parse_as_of(as_of)
    with _warehouse(db) as wh:
        rev = run_review(wh, season=season)

        if as_json:
            typer.echo(json.dumps(_review_json(rev, when), indent=2, default=str))
            return

        b = rev.scoreboard
        console.rule(f"[bold]Idea review — {season}")
        if b.n_total == 0:
            console.print(
                "No ideas recorded. Text the bot, or run "
                "[bold]fpl idea submit \"I like Rashford\"[/]."
            )
            return

        _print_scoreboard(b)
        _print_biases(rev)
        _print_by_kind(rev)
        _print_ideas(rev, show_all=show_all)

        if rev.caveats:
            console.print()
            console.rule("[bold]Read this before believing any of the above")
            for c in rev.caveats:
                console.print(f"  • {c}")


def _print_scoreboard(b) -> None:
    console.print()
    console.print(
        f"[bold]{b.n_total}[/] ideas — {b.n_resolved} resolved, "
        f"{b.n_open} open, {b.n_void} void."
    )
    if b.hit_rate is not None:
        console.print(
            f"Hit rate [bold]{b.hit_rate:.0%}[/] against the comparator each thesis "
            f"named; mean margin {b.mean_margin:+.2f} points."
        )
    if b.acted_hit_rate is not None or b.unacted_hit_rate is not None:
        table = Table(title="Acted on vs skipped", title_justify="left", show_edge=False)
        table.add_column("")
        table.add_column("n", justify="right")
        table.add_column("hit rate", justify="right")
        for label, n, rate in (
            ("You did it", b.acted_n, b.acted_hit_rate),
            ("You skipped it", b.unacted_n, b.unacted_hit_rate),
        ):
            table.add_row(label, str(n), "—" if rate is None else f"{rate:.0%}")
        console.print()
        console.print(table)
        if (
            b.acted_hit_rate is not None and b.unacted_hit_rate is not None
            and min(b.acted_n, b.unacted_n) >= MIN_OBSERVATIONS
        ):
            gap = b.unacted_hit_rate - b.acted_hit_rate
            if gap > 0.1:
                console.print(
                    "[yellow]The ideas you talked yourself out of did better than the "
                    "ones you executed.[/]"
                )
    if b.brier is not None:
        better = "beats" if b.brier < (b.baseline_brier or 1) else "loses to"
        console.print()
        console.print(
            f"Engine calibration: Brier [bold]{b.brier:.3f}[/] vs {b.baseline_brier:.3f} "
            f"for always saying 50% — {better} the coin flip."
        )
    if b.engine_agreed_hit_rate is not None and b.engine_disagreed_hit_rate is not None:
        console.print(
            f"When the engine agreed with you: {b.engine_agreed_hit_rate:.0%} right. "
            f"When it disagreed: {b.engine_disagreed_hit_rate:.0%}."
        )


def _print_biases(rev) -> None:
    console.print()
    console.rule("[bold]Biases, measured from your own history")
    console.print(
        "[dim]Each is a hypothesis test against the population base rate captured "
        "at the moment you had the idea. p-values are Holm-corrected across the "
        f"probes that ran. Nothing is called a bias below n={MIN_OBSERVATIONS}.[/]"
    )
    console.print()
    for f in rev.findings:
        marker = "!" if f.significant else " "
        console.print(f"[bold]{f.name}[/] — {f.question}")
        echo(f"  {marker} {f.verdict()}")
        echo(f"    {f.detail}")
        console.print()


def _print_by_kind(rev) -> None:
    if rev.by_kind.empty:
        return
    table = Table(title="By kind of idea", title_justify="left", show_edge=False)
    table.add_column("kind")
    table.add_column("ideas", justify="right")
    table.add_column("resolved", justify="right")
    table.add_column("hit rate", justify="right")
    for _, row in rev.by_kind.iterrows():
        table.add_row(
            str(row["kind"]), str(int(row["n"])), str(int(row["resolved"])),
            "—" if row["hit_rate"] is None else f"{float(row['hit_rate']):.0%}",
        )
    console.print(table)


def _print_ideas(rev, *, show_all: bool) -> None:
    df = rev.ideas
    if df.empty:
        return
    rows = df if show_all else df.tail(15)
    table = Table(
        title=f"{'All' if show_all else 'Most recent'} ideas", title_justify="left",
        show_edge=False,
    )
    for col, justify in (
        ("when", "left"), ("said", "left"), ("thesis", "left"),
        ("engine", "right"), ("acted", "center"), ("outcome", "right"),
    ):
        table.add_column(col, justify=justify, overflow="fold")
    for _, r in rows.iterrows():
        outcome = r["outcome"] if r["status"] == "resolved" else r["status"]
        colour = {"correct": "green", "incorrect": "red"}.get(str(outcome), "dim")
        p = r["p_thesis_true"]
        table.add_row(
            f"{r['created_utc']:%d %b %H:%M}",
            str(r["raw_text"])[:40],
            str(r["thesis"])[:70],
            "—" if p is None or p != p else f"{float(p):.0%}",
            "yes" if r["acted"] else "no",
            f"[{colour}]{outcome}[/]",
        )
    console.print()
    console.print(table)


def _review_json(rev, when: dt.datetime) -> dict:
    b = rev.scoreboard
    return {
        "season": rev.season,
        "generated_utc": when.isoformat(),
        "scoreboard": {
            "n_total": b.n_total, "n_resolved": b.n_resolved, "n_open": b.n_open,
            "n_void": b.n_void, "hit_rate": b.hit_rate, "mean_margin": b.mean_margin,
            "acted_n": b.acted_n, "acted_hit_rate": b.acted_hit_rate,
            "unacted_n": b.unacted_n, "unacted_hit_rate": b.unacted_hit_rate,
            "brier": b.brier, "baseline_brier": b.baseline_brier,
        },
        "biases": [
            {
                "name": f.name, "question": f.question, "n": f.n,
                "observed": f.observed, "expected": f.expected, "effect": f.effect,
                "p_value": f.p_value, "p_adjusted": f.p_adjusted,
                "has_evidence": f.has_evidence, "significant": f.significant,
                "verdict": f.verdict(), "detail": f.detail,
            }
            for f in rev.findings
        ],
        "caveats": list(rev.caveats),
    }


# -- fpl idea track / list / acted -------------------------------------------


@idea_app.command("track")
def idea_track(
    db: Path = DbOpt, season: str = SeasonOpt, as_of: str = AsOfOpt,
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Settle every idea whose gameweeks have finalised. Safe to run on a timer."""
    when = _parse_as_of(as_of)
    with _warehouse(db) as wh:
        result = run_track(wh, season=season, now=when)
        console.print(result.render())
        if verbose:
            for note in result.notes:
                console.print(f"  {note}")


@idea_app.command("list")
def idea_list(
    db: Path = DbOpt, season: str = SeasonOpt,
    status: str = typer.Option(None, "--status", help="open | resolved | void"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """List ideas, newest first."""
    with _warehouse(db) as wh:
        reg = IdeaRegistry(wh)
        st = IdeaStatus(status) if status else None
        ideas = reg.ideas(season=season, status=st, limit=limit)
        if not ideas:
            console.print("No ideas.")
            return
        for i in ideas:
            v = reg.latest_verdict(i.idea_id)
            mark = "*" if i.acted else " "
            echo(f"{i.idea_id} {mark} {i.created_utc:%d %b %H:%M}Z  [{i.status}]")
            echo(f"    said: {i.raw_text}")
            echo(f"    thesis: {i.thesis}")
            if v:
                echo(
                    f"    engine: {v.stance} P={v.p_thesis_true:.0%} "
                    f"({v.provider} {v.provider_version}, {v.confidence})"
                )
            if i.outcome:
                echo(
                    f"    outcome: {i.outcome} "
                    f"({i.subject_points:.0f} vs {i.comparator_points:.0f})"
                )
            console.print()


@idea_app.command("acted")
def idea_acted(
    idea_id: str = typer.Argument(..., help="The idea id, from `fpl idea list`."),
    db: Path = DbOpt,
    undo: bool = typer.Option(False, "--undo"),
) -> None:
    """Record that you actually did it. Tracking happens either way."""
    with _warehouse(db) as wh:
        ok = IdeaRegistry(wh).mark_acted(
            idea_id, acted=not undo, when=dt.datetime.now(UTC)
        )
        console.print("Updated." if ok else f"No idea with id {idea_id}.")
        if not ok:
            raise typer.Exit(code=1)


@app.command("elite")
def elite(
    top: int = typer.Option(20, "--top", help="How many managers to show."),
    refresh: bool = typer.Option(False, "--refresh", help="Recompute from the warehouse."),
) -> None:
    """The maintained list of TOP FPL managers, skill-scored across seasons.

    Skill is separated from luck with a hierarchical model over normalised
    season ranks (ICC ~0.53: about half of season-to-season variance is
    persistent skill). The list refreshes nightly with the settlement job;
    --refresh recomputes now. Their squads become copyable the moment each
    gameweek's deadline locks them.
    """
    import json as _json
    from pathlib import Path as _P

    cache = _P("data/warehouse/elite_list.json")
    if refresh or not cache.exists():
        from fpl_edge.models.copying.report import build

        data = build()
        cache.write_text(_json.dumps(data, default=str))
    else:
        data = _json.loads(cache.read_text())

    rows = data.get("shortlist", [])[:top]
    if not rows:
        echo("No elite list yet. Run `make deploy` (nightly) or `fpl elite --refresh`.")
        raise typer.Exit(1)
    from rich.table import Table

    t = Table(title=f"Top {len(rows)} managers by skill score (theta)")
    for col in ("#", "manager", "entry", "seasons", "theta", "top-10k seasons", "best rank"):
        t.add_column(col)
    for i, r in enumerate(rows, 1):
        t.add_row(str(i), str(r.get("name", "?")), str(r.get("entry_id")),
                  str(r.get("n_seasons")), f"{r.get('theta_hat', 0):.2f}",
                  str(r.get("top10k_seasons")), f"{r.get('best_rank'):,}")
    console.print(t)
    you = data.get("user", {})
    if you:
        console.print(
            f"You: theta {you.get('theta_hat', 0):.2f}, pool rank "
            f"{int(you.get('pool_rank', 0))}/{int(you.get('pool_size', 0))} "
            f"(expected percentile {you.get('expected_percentile', 0):.1%})"
        )


# -- fpl idea telegram -------------------------------------------------------


@idea_app.command("telegram")
def idea_telegram(
    db: Path = DbOpt,
    season: str = SeasonOpt,
    env_file: Path = typer.Option(Path(".env"), "--env-file"),
    discover: bool = typer.Option(
        False, "--discover",
        help="Log the chat id of anyone who messages, so you can find yours.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Run the whole bot against a fake transport. No token needed.",
    ),
    cycles: int = typer.Option(None, "--cycles", help="Stop after N poll cycles."),
) -> None:
    """Run the long-polling bot. Reads TELEGRAM_BOT_TOKEN from env or .env.

    The squad commands (/setsquad, /confirm, /myteam, /sync) are installed onto
    this same bot rather than run as a second one: two pollers on one token fight
    over getUpdates, and the idea inbox must keep working unchanged.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs full request URLs at INFO, and Telegram Bot API URLs CONTAIN
    # THE TOKEN. A credential in a log file defeats everything .env protects.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    from fpl_edge.interfaces.telegram import (
        FakeTransport,
        TelegramConfig,
        build_bot,
    )

    cfg = TelegramConfig.from_env(env_path=env_file)

    if dry_run:
        with _warehouse(db) as wh:
            transport = FakeTransport()
            bot = build_bot(
                IdeaInbox(wh, season=season), config=cfg, transport=transport,
                season=season, discover=True,
            )
            _install_myteam(bot, wh, season)
            chat = next(iter(cfg.allowed_chat_ids), 1)
            bot.allowed = frozenset({chat})
            console.print(f"[dim]Dry run against a fake transport, chat id {chat}.[/]")
            for line in sys.stdin:
                line = line.rstrip("\n")
                if not line:
                    continue
                transport.push_message(line, chat_id=chat)
                bot.poll_once(timeout=0)
                for reply in transport.replies_to(chat)[-1:]:
                    console.print(reply)
            console.print(f"[dim]{bot.stats}[/]")
        return

    problems = cfg.problems()
    if problems and not discover:
        for p in problems:
            console.print(f"[red]{p}[/]")
        raise typer.Exit(code=2)
    if not cfg.token:
        console.print("[red]--discover still needs TELEGRAM_BOT_TOKEN.[/]")
        raise typer.Exit(code=2)

    from fpl_edge.store import LeasedWarehouse

    with LeasedWarehouse(db) as wh:
        bot = build_bot(IdeaInbox(wh, season=season), config=cfg, season=season, discover=discover)
        _install_myteam(bot, wh, season)
        bot.drop_webhook()
        console.print(f"Connected as @{bot.check_connection()}.")
        console.print(
            f"Accepting messages from {sorted(bot.allowed) or 'NOBODY (allowlist empty)'}."
        )
        bot.run_forever(max_cycles=cycles)
        console.print(f"{bot.stats}")


def _install_myteam(bot, wh: Warehouse, season: str) -> None:
    """Attach the squad commands to the idea bot. Owned by fpl_edge.myteam."""
    from fpl_edge.myteam.bot import MyTeamCommands, install

    install(bot, MyTeamCommands(warehouse=wh, season=season))


# -- fpl weekly --------------------------------------------------------------


@app.command("weekly")
def weekly(
    db: Path = DbOpt,
    season: str = SeasonOpt,
    gw: int = typer.Option(None, "--gw", help="Defaults to the next open gameweek."),
    as_of: str = AsOfOpt,
) -> None:
    """The decision report for the upcoming deadline."""
    when = _parse_as_of(as_of)
    with _warehouse(db) as wh:
        echo(weekly_report(wh, season=season, gw=gw, as_of=when).render())


# -- fpl platform ------------------------------------------------------------
#
# The web platform (fpl_edge/platform/). Imported inside the command rather than
# at module scope so that `fpl` stays fast and keeps working on a checkout where
# FastAPI is not installed: the CLI, the bot and the MCP server predate the
# platform and must not acquire a hard dependency on it.

platform_app = typer.Typer(no_args_is_help=True, help="The decision platform web server.")
app.add_typer(platform_app, name="platform")


@platform_app.command("serve")
def platform_serve(
    db: Path = DbOpt,
    host: str = typer.Option(
        "127.0.0.1", "--host",
        help="Loopback by default: there is no auth layer, and /api/query is a "
             "SQL endpoint that should not answer on the local network.",
    ),
    port: int = typer.Option(8321, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change."),
) -> None:
    """Serve the panels, the guarded query path and the chat route."""
    try:
        from fpl_edge.platform.app import serve
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise typer.BadParameter(
            f"the platform needs FastAPI and uvicorn: {exc}. "
            f"Run `uv pip install -e '.[dev]'` to pick them up."
        ) from exc
    echo(f"platform on http://{host}:{port}  (warehouse {db}, docs at /docs)")
    serve(host=host, port=port, db=db, reload=reload)


# -- fpl dossier / fpl intel -------------------------------------------------
#
# Owned by the intel team (fpl_edge/intel/, fpl_edge/interfaces/dossier.py). They
# register their own commands so that adding a section, a flag or a subcommand
# never needs an edit to this file, and so the CLI, the MCP tool and the Telegram
# reply stay three renderers over one implementation.
from fpl_edge.intel.cli import register_cli as _register_intel_cli  # noqa: E402
from fpl_edge.interfaces.dossier import register_cli as _register_dossier_cli  # noqa: E402

_register_dossier_cli(app)
_register_intel_cli(app)


if __name__ == "__main__":
    app()
