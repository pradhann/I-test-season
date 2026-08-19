"""`fpl thesis add` and `fpl theses ...` — the terminal surface of the registry.

Kept separate from the engine so the resolve/create logic never imports typer.
Importing this module also registers the weekly-report section, so wiring the
CLI into ``fpl_edge.cli.main`` is the single hook that makes theses appear in
``fpl weekly``.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import typer
from rich.console import Console

from fpl_edge.store import DEFAULT_DB, Warehouse
from fpl_edge.theses import report as theses_report
from fpl_edge.theses.create import PlayerResolutionError, create_thesis, sync_from_registry
from fpl_edge.theses.model import ClaimType, ThesisSource
from fpl_edge.theses.resolve import resolve_theses
from fpl_edge.theses.scoreboard import compute
from fpl_edge.theses.store import DEFAULT_THESES_DIR, ThesesStore

UTC = dt.timezone.utc
DEFAULT_SEASON = "2026-27"

theses_report.register()

theses_app = typer.Typer(
    no_args_is_help=True,
    help="The hypothesis registry: versioned, machine-graded beliefs.",
)
thesis_app = typer.Typer(no_args_is_help=True, help="File one thesis.")

console = Console()


def echo(text: str) -> None:
    console.print(text, markup=False, highlight=False)


def _parse_as_of(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(UTC)
    parsed = dt.datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        raise typer.BadParameter("--as-of must carry a timezone, e.g. 2026-08-18T22:50:00Z")
    return parsed.astimezone(UTC)


def _warehouse(db: Path, *, read_only: bool = True) -> Warehouse:
    """Open the warehouse, read-only by default.

    Theses live in files; the warehouse is only ever *read* here (creation
    verdicts, realised results). Read-only means `fpl thesis add` still works
    while an ingest or the bot holds DuckDB's single writer lock -- which is
    exactly when ideas tend to arrive. Only `theses sync` needs the writer
    (the ideas registry applies its migrations at open).
    """
    if not db.exists():
        console.print(f"[red]No warehouse at {db}.[/] Run `make ingest` first.")
        raise typer.Exit(code=2)
    return Warehouse(db, read_only=read_only)


DbOpt = typer.Option(DEFAULT_DB, "--db", help="Path to the DuckDB warehouse.")
SeasonOpt = typer.Option(DEFAULT_SEASON, "--season", help="Season in FPL's 2026-27 form.")
AsOfOpt = typer.Option(None, "--as-of", help="Act as of this UTC instant (reproducible).")
DirOpt = typer.Option(
    DEFAULT_THESES_DIR, "--dir", help="Theses directory (open/, resolved/, scoreboard/)."
)


@thesis_app.command("add")
def thesis_add(
    raw_input: str = typer.Argument(..., help="The belief, in plain English."),
    db: Path = DbOpt,
    season: str = SeasonOpt,
    as_of: str = AsOfOpt,
    base_dir: Path = DirOpt,
    source: str = typer.Option(
        "user_chat", "--source",
        help="user_chat | creator | elite_manager | model | llm_scout",
    ),
    creator: str = typer.Option(
        None, "--creator", help="Named creator/model this belief belongs to."
    ),
    player: str = typer.Option(
        None, "--player", help="Player name query; defaults to parsing the text."
    ),
    claim_type: str = typer.Option(
        None, "--claim-type",
        help="buy | avoid | watch | out_of_position | minutes | captain "
             "(defaults to parsing the text)",
    ),
    gw: int = typer.Option(None, "--gw", help="First gameweek of the window."),
    horizon: int = typer.Option(None, "--horizon", help="Window length in gameweeks."),
    prediction: str = typer.Option(
        None, "--prediction",
        help="Exact claim sentence from the grammar; overrides the default. "
             "If it matches no template the thesis is demoted to watch, with a note.",
    ),
    acted: bool = typer.Option(False, "--acted", help="You actually did this."),
    prose: str = typer.Option("", "--prose", help="Free-text reasoning for the file body."),
) -> None:
    """File a thesis: resolve the player, freeze the model's verdict, write the file."""
    when = _parse_as_of(as_of)
    with _warehouse(db) as wh:
        try:
            thesis, path = create_thesis(
                wh,
                raw_input=raw_input,
                source=ThesisSource(source),
                season=season,
                player=player,
                claim_type=ClaimType(claim_type) if claim_type else None,
                creator=creator,
                gw_start=gw,
                horizon_gws=horizon,
                prediction=prediction,
                prose=prose,
                acted=acted,
                as_of=when,
                store=ThesesStore(base_dir),
                demote_unfalsifiable=True,
            )
        except PlayerResolutionError as exc:
            echo(str(exc))
            raise typer.Exit(code=1) from exc
    echo(f"Filed {path}")
    echo(f"  player: {thesis.player} (code {thesis.player_code})")
    echo(f"  claim:  {thesis.falsifiable_prediction or '(watch — no falsifiable claim)'}")
    if thesis.comparator_label:
        echo(f"  vs:     {thesis.comparator_label}")
    mv = thesis.model_verdict_at_creation
    echo(
        "  model at creation: "
        + ", ".join(
            f"{k}={mv[k]}" for k in ("xpts", "price", "ownership_pct", "status")
            if mv.get(k) is not None
        )
    )
    echo(f"  settles: {thesis.window_label}")


@theses_app.command("resolve")
def theses_resolve(
    db: Path = DbOpt,
    season: str = SeasonOpt,
    as_of: str = AsOfOpt,
    base_dir: Path = DirOpt,
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show grading, moves, scoreboard and the commit message; write nothing.",
    ),
    no_commit: bool = typer.Option(False, "--no-commit", help="Write files but skip the commit."),
    no_sync: bool = typer.Option(
        False, "--no-sync", help="Skip mirroring new registry ideas into files first."
    ),
) -> None:
    """Grade every open thesis whose window has finalised; move, score, commit."""
    from fpl_edge.store import WarehouseLockedError

    when = _parse_as_of(as_of)
    # The registry sync needs the writer (its migrations run at open); grading
    # itself only reads. If another process holds the single writer lock,
    # settle anyway rather than skipping the week.
    sync = not no_sync
    try:
        wh = _warehouse(db, read_only=no_sync or dry_run)
    except WarehouseLockedError:
        console.print("[yellow]Warehouse writer is busy; resolving read-only "
                      "without the registry sync. Run `fpl theses sync` later.[/]")
        sync = False
        wh = _warehouse(db, read_only=True)
    with wh:
        report = resolve_theses(
            wh,
            season=season,
            as_of=when,
            store=ThesesStore(base_dir),
            dry_run=dry_run,
            commit=not no_commit,
            sync_registry=sync and not dry_run,
        )
    echo(report.render())


@theses_app.command("sync")
def theses_sync(
    db: Path = DbOpt, season: str = SeasonOpt, base_dir: Path = DirOpt
) -> None:
    """Mirror registry ideas (Telegram/CLI inbox) into thesis files. Idempotent."""
    with _warehouse(db, read_only=False) as wh:
        created = sync_from_registry(wh, season=season, store=ThesesStore(base_dir))
    if not created:
        echo("Nothing to sync: every registry idea with a subject is already filed.")
    for thesis, path in created:
        echo(f"Filed {path} (from {thesis.idea_id})")


@theses_app.command("list")
def theses_list(
    base_dir: Path = DirOpt,
    status: str = typer.Option("open", "--status", help="open | resolved"),
) -> None:
    """List theses on disk."""
    store = ThesesStore(base_dir)
    rows = store.load_open() if status == "open" else store.load_resolved()
    if not rows:
        echo(f"No {status} theses under {store.base}.")
        return
    for thesis, path in rows:
        outcome = f" -> {thesis.resolution['outcome']}" if thesis.resolution else ""
        echo(f"{thesis.id} [{thesis.scoreboard_key}]{outcome}")
        echo(f"    {thesis.falsifiable_prediction or '(watch)'}  ({thesis.window_label})")


@theses_app.command("review")
def theses_review(base_dir: Path = DirOpt) -> None:
    """The scoreboard, and what hesitancy has cost so far."""
    store = ThesesStore(base_dir)
    resolved = [t for t, _ in store.load_resolved()]
    if not resolved:
        echo("Nothing resolved yet. Run `make resolve-gw` after a gameweek finalises.")
        return
    records = compute(resolved)
    echo(f"{len(resolved)} resolved theses.\n")
    for r in records:
        hit = "no scored claims" if r.hit_rate is None else \
            f"{r.correct}/{r.sample} correct ({r.hit_rate:.0%})"
        mm = "" if r.mean_margin is None else f", mean margin {r.mean_margin:+.1f} pts"
        echo(f"  {r.entity_type:7s} {r.entity}: {hit}{mm}")
    hesitancy = sum(r.hesitancy_cost_pts for r in records if r.entity_type == "source")
    if hesitancy:
        echo(
            f"\nCost of hesitancy: {hesitancy:+.1f} pts — the summed margins of "
            "correct calls that were never acted on."
        )
    from fpl_edge.theses.model import ThesisOutcome

    scored_club = [
        t for t in resolved
        if t.model_verdict_at_creation.get("is_supported_club")
        and t.outcome in (ThesisOutcome.CORRECT, ThesisOutcome.INCORRECT)
    ]
    if scored_club:
        right = sum(1 for t in scored_club if t.outcome is ThesisOutcome.CORRECT)
        echo(
            f"\nClub affinity check: {right}/{len(scored_club)} correct on "
            "supported-club players. Compare with the overall rate above "
            "before trusting your heart."
        )
