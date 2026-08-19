"""``fpl intel ...`` -- collect, inspect and alert on news and tactical signals.

Registered onto the main Typer app by :func:`register_cli` rather than defined
inside :mod:`fpl_edge.cli.main`, which belongs to another team. A single call is
a smaller thing for them to carry than four command bodies.

Also runnable standalone as ``python -m fpl_edge.intel.cli`` so the collector can
sit on a timer without depending on the top-level CLI's import graph.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import typer

from fpl_edge.intel.bootstrap import ARCHIVE_DIR
from fpl_edge.intel.collect import collect as run_collect
from fpl_edge.intel.items import IntelKind
from fpl_edge.intel.store import IntelStore
from fpl_edge.store import DEFAULT_DB, Warehouse

UTC = dt.timezone.utc
DEFAULT_SEASON = "2026-27"

app = typer.Typer(no_args_is_help=True, help="News, press conferences and tactical signals.")


def _as_of(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(UTC)
    parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise typer.BadParameter("--as-of must carry a timezone, e.g. 2026-08-21T17:30:00Z")
    return parsed.astimezone(UTC)


@app.command("collect")
def collect_cmd(
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    season: str = typer.Option(DEFAULT_SEASON, "--season"),
    archive: Path = typer.Option(ARCHIVE_DIR, "--archive", help="Raw FPL body archive."),
    probe: bool = typer.Option(
        False, "--probe",
        help="Also reach the external press-conference sources and record their HTTP status.",
    ),
) -> None:
    """Replay the raw archive into dated intel rows. Idempotent.

    Needs the warehouse write lock. DuckDB permits one writer, and this project
    runs long simulations against the same file, so a collision here is normal
    rather than exceptional -- the error names the process holding it.
    """
    with Warehouse(db) as wh:
        typer.echo(run_collect(wh, season=season, archive_dir=archive, probe_sources=probe).render())


@app.command("news")
def news_cmd(
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    season: str = typer.Option(DEFAULT_SEASON, "--season"),
    as_of: str = typer.Option(None, "--as-of"),
    kind: str = typer.Option(None, "--kind", help="availability|press_conference|set_piece|out_of_position|formation|source_probe"),
    hours: float = typer.Option(72.0, "--hours", help="Only items published this recently."),
    limit: int = typer.Option(30, "--limit"),
) -> None:
    """Everything published at or before --as-of, newest first."""
    when = _as_of(as_of)
    with Warehouse(db, read_only=True) as wh:
        store, exists = IntelStore.open_reader(wh)
        if not exists:
            typer.echo("No intel tables in this warehouse. Run `fpl intel collect` first.")
            raise typer.Exit(code=1)
        items = store.items(
            when, season=season, kind=IntelKind(kind) if kind else None, limit=limit * 4
        )
    cutoff = when - dt.timedelta(hours=hours)
    items = [i for i in items if i.published_at >= cutoff][:limit]
    if not items:
        typer.echo(
            f"No intel published in the {hours:.0f}h before {when:%Y-%m-%d %H:%M}Z. "
            "That is a real answer, not an empty result: the store was queried and "
            "the point-in-time filter matched nothing."
        )
        return
    for i in items:
        typer.echo(f"{i.published_at:%Y-%m-%d %H:%M}Z [{i.kind}] {i.headline}")
        if i.body:
            typer.echo(f"    {i.body}")
        if i.source_url:
            typer.echo(f"    {i.source_url}")


@app.command("setpieces")
def setpieces_cmd(
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    season: str = typer.Option(DEFAULT_SEASON, "--season"),
    as_of: str = typer.Option(None, "--as-of"),
    changes: bool = typer.Option(False, "--changes", help="Show detected changes instead of state."),
    limit: int = typer.Option(40, "--limit"),
) -> None:
    """First-choice takers, or every change on record."""
    when = _as_of(as_of)
    with Warehouse(db, read_only=True) as wh:
        store, exists = IntelStore.open_reader(wh)
        if not exists:
            typer.echo("No intel tables. Run `fpl intel collect` first.")
            raise typer.Exit(code=1)
        if changes:
            found = store.changes(when, limit=limit)
            if not found:
                typer.echo(f"No set-piece change visible at {when:%Y-%m-%d %H:%M}Z.")
                return
            for c in sorted(found, key=lambda x: -abs(x.delta_goals_per_game)):
                typer.echo(
                    f"{c.detected_at:%Y-%m-%d} {c.headline} "
                    f"[{c.delta_goals_per_game:+.3f} goals/game]"
                )
            return
        duties = store.duties(when, season=season)
        firsts = [d for d in duties if d.ord == 1]
        if not firsts:
            typer.echo(f"No set-piece duty on record for {season} at {when:%Y-%m-%d %H:%M}Z.")
            return
        for d in sorted(firsts, key=lambda x: (str(x.duty), x.team_code or 0)):
            typer.echo(f"{d.duty.label:<32} team {d.team_code}  code {d.code}")


@app.command("oop")
def oop_cmd(
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    season: str = typer.Option(DEFAULT_SEASON, "--season"),
    as_of: str = typer.Option(None, "--as-of"),
    min_score: float = typer.Option(0.15, "--min-score"),
) -> None:
    """Players FPL classifies in one position who perform like another."""
    from fpl_edge.intel.oop import POS_NAME

    when = _as_of(as_of)
    with Warehouse(db, read_only=True) as wh:
        store, exists = IntelStore.open_reader(wh)
        if not exists:
            typer.echo("No intel tables. Run `fpl intel collect` first.")
            raise typer.Exit(code=1)
        signals = store.oop(when, season=season, min_score=min_score)
        names = dict(
            zip(
                wh.snapshot_at(when).players(season)["code"].astype(int),
                wh.snapshot_at(when).players(season)["web_name"].astype(str),
            )
        )
    if not signals:
        typer.echo(f"No out-of-position signal at or above {min_score:.2f}.")
        return
    for s in signals:
        typer.echo(
            f"{s.score:5.2f}  {names.get(s.code, s.code):<16} "
            f"{POS_NAME[s.fpl_position]} -> {POS_NAME[s.plays_like]}"
        )
        typer.echo(f"        {s.evidence}")


@app.command("status")
def status_cmd(db: Path = typer.Option(DEFAULT_DB, "--db")) -> None:
    """Row counts per intel table."""
    with Warehouse(db, read_only=True) as wh:
        store, exists = IntelStore.open_reader(wh)
        if not exists:
            typer.echo("No intel tables in this warehouse. Run `fpl intel collect` first.")
            raise typer.Exit(code=1)
        for table, n in sorted(store.counts().items()):
            typer.echo(f"{table:<24} {n:>8,}")


def register_cli(parent: Any) -> None:
    """Attach ``fpl intel ...`` to an existing Typer app."""
    parent.add_typer(app, name="intel")


if __name__ == "__main__":
    app()
