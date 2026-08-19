"""`fpl myteam ...` -- see the reconstructed team, enter it once, act on it.

Mounted onto the main app as a Typer sub-app. Also runnable on its own with
``python -m fpl_edge.myteam``, which is how it can be exercised without the
interfaces team having to edit their CLI.

Every command takes ``--as-of`` for the same reason the rest of the CLI does:
"now" is not reproducible, and a transfer recommendation you cannot reproduce is
not a recommendation, it is a hunch with a timestamp.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import typer
from rich.console import Console

from fpl_edge.config import USER
from fpl_edge.myteam.forecast import PointsForecastUnavailableError, TablePointsForecast
from fpl_edge.myteam.manual import build_draft, reconcile
from fpl_edge.myteam.recommend import NoSquadError, recommend
from fpl_edge.myteam.sources import AuthenticatedEndpointError, PublicEntryClient
from fpl_edge.myteam.state import PlayerIndex, Provenance, reconstruct
from fpl_edge.myteam.store import DEFAULT_ROOT, MyTeamStore, NoSuchDraftError
from fpl_edge.opt import ObjectiveMode
from fpl_edge.opt.interfaces import RankUtilityUnavailableError
from fpl_edge.store import DEFAULT_DB, Warehouse

UTC = dt.timezone.utc
DEFAULT_SEASON = "2026-27"

app = typer.Typer(
    no_args_is_help=True,
    help="Your actual team: reconstruct it, enter it once, and act on it.",
)
console = Console()


def echo(text: str) -> None:
    """Print text built from user input with Rich markup off.

    A squad pasted by a human can contain brackets, and Rich would read
    ``[b]Fernandes`` as a bold tag and eat it.
    """
    console.print(text, markup=False, highlight=False)


def _as_of(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(UTC)
    parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise typer.BadParameter("--as-of must carry a timezone, e.g. 2026-08-18T22:50:00Z")
    return parsed.astimezone(UTC)


def _warehouse(db: Path) -> Warehouse:
    if not db.exists():
        console.print(f"[red]No warehouse at {db}.[/] Run `make ingest` first.")
        raise typer.Exit(code=2)
    return Warehouse(db, read_only=True)


DbOpt = typer.Option(DEFAULT_DB, "--db", help="Path to the DuckDB warehouse.")
SeasonOpt = typer.Option(DEFAULT_SEASON, "--season", help="Season in FPL's 2026-27 form.")
AsOfOpt = typer.Option(None, "--as-of", help="Read the warehouse as of this UTC instant.")
EntryOpt = typer.Option(None, "--entry", help=f"FPL entry id. Defaults to {USER.entry_id}.")
StoreOpt = typer.Option(DEFAULT_ROOT, "--store", help="Where the manual squad is kept.")


def _entry_id(value: int | None) -> int:
    return int(value if value is not None else USER.entry_id)


# -- fpl myteam show ---------------------------------------------------------


def _try_private(entry_id: int):
    """The live private squad when a session cookie is configured, else None.

    Degrades silently to the public/manual path on any failure EXCEPT a stale
    cookie, which is worth a visible warning: the manager set it up and would
    otherwise not learn it stopped working until a wrong recommendation.
    """
    from fpl_edge.myteam.private import (
        NoSessionError,
        PrivateTeamClient,
        StaleSessionError,
    )

    if PrivateTeamClient.disabled_by_env():
        return None
    client = PrivateTeamClient()
    from fpl_edge.myteam.tokens import TokenManager

    if not client.configured and not TokenManager().configured:
        return None
    try:
        return client.fetch(entry_id)
    except StaleSessionError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        return None
    except NoSessionError:
        return None


@app.command("show")
def show(
    db: Path = DbOpt,
    season: str = SeasonOpt,
    as_of: str = AsOfOpt,
    entry: int = EntryOpt,
    store_root: Path = StoreOpt,
    gw: int = typer.Option(None, "--gw", help="Defaults to the next open gameweek."),
    offline: bool = typer.Option(False, "--offline", help="Do not call the FPL API."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Everything the engine knows about your team, and what it cannot know."""
    when = _as_of(as_of)
    entry_id = _entry_id(entry)
    store = MyTeamStore(entry_id, root=store_root)
    manual = store.confirmed(season=season)

    with _warehouse(db) as wh:
        snapshot = wh.snapshot_at(when)
        index = PlayerIndex.from_snapshot(snapshot, season)
        client = None if offline else PublicEntryClient()
        try:
            if offline:
                console.print("[dim]--offline: no FPL API calls; manual squad only.[/]")
                from fpl_edge.myteam.sources import EntryHistory, EntrySummary

                summary = EntrySummary(
                    entry_id=entry_id, name="", player_name="", started_event=None,
                    current_event=None, entered_events=(), last_deadline_bank=None,
                    last_deadline_value=None, last_deadline_total_transfers=0,
                    years_active=None, favourite_team=None,
                    summary_overall_points=None, summary_overall_rank=None,
                )
                state = reconstruct(
                    snapshot, entry_id=entry_id, season=season, entry=summary,
                    history=EntryHistory((), (), ()), transfers=(), manual=manual, gw=gw,
                )
            else:
                state = reconstruct(
                    snapshot, entry_id=entry_id, season=season, client=client,
                    manual=manual, private=_try_private(entry_id), gw=gw,
                )
        finally:
            if client is not None:
                client.close()

        if as_json:
            typer.echo(json.dumps(_state_json(state, index), indent=2, default=str))
            return

        echo(state.render())
        if state.picks:
            console.print()
            echo(f"Team value {state.team_value(index.price_now)} "
                 f"(squad {state.squad_value(index.price_now)} + bank {state.bank})")
            console.print()
            for pick in sorted(state.picks, key=lambda p: p.order):
                tag = "  " if pick.is_starter else "B "
                mark = " (C)" if pick.is_captain else (" (V)" if pick.is_vice else "")
                echo(
                    f"  {tag}{pick.position.name} {index.name[pick.code]:<18} "
                    f"bought {state.bought_at[pick.code] / 10:.1f} "
                    f"now {index.price_now[pick.code] / 10:.1f}{mark}"
                )
        if state.past_seasons:
            console.print()
            echo("Past seasons (points, overall rank):")
            for name, points, rank in state.past_seasons:
                echo(f"  {name}  {points:>5}  {rank:>9,}")


def _state_json(state, index) -> dict:
    return {
        "entry_id": state.entry_id,
        "season": state.season,
        "gw": int(state.gw),
        "as_of": state.as_of.isoformat(),
        "provenance": state.provenance.value,
        "known": state.known,
        "bank_tenths": state.bank_tenths,
        "free_transfers": state.free_transfers,
        "total_transfers_this_entry": state.total_transfers_this_entry,
        "team_value_tenths": (
            state.team_value(index.price_now).tenths if state.picks else None
        ),
        "chips": [
            {"chip": c.chip, "remaining": c.remaining,
             "by_half": [c.remaining_in(i) for i in range(len(c.windows))]}
            for c in state.chip_status()
        ],
        "picks": (
            [
                {
                    "code": p.code, "name": index.name[p.code], "order": p.order,
                    "position": p.position.name, "bought_at": state.bought_at[p.code],
                    "price_now": index.price_now[p.code],
                    "captain": p.is_captain, "vice": p.is_vice,
                }
                for p in sorted(state.picks, key=lambda x: x.order)
            ]
            if state.picks else None
        ),
        "derived": list(state.derived),
        "unavailable": list(state.unavailable),
        "checks": [
            {"name": c.name, "derived": c.derived, "observed": c.observed, "ok": c.ok}
            for c in state.checks
        ],
    }


# -- fpl myteam set ----------------------------------------------------------


@app.command("set")
def set_squad(
    squad: str = typer.Argument(
        None,
        help="Your 15, any order. One per line or comma separated. '-' reads stdin.",
    ),
    db: Path = DbOpt,
    season: str = SeasonOpt,
    as_of: str = AsOfOpt,
    entry: int = EntryOpt,
    store_root: Path = StoreOpt,
    gw: int = typer.Option(1, "--gw", help="The gameweek this squad is for."),
    yes: bool = typer.Option(
        False, "--yes",
        help="Confirm immediately. Only for scripts -- you lose the check.",
    ),
) -> None:
    """Tell the engine your 15, once, because FPL will not until GW1 starts.

    Parses names in any order, ignores the price and position annotations the
    app puts next to them, refuses to guess between two players who match
    equally well, validates the result against the real rules, and shows it back
    for confirmation. Nothing is saved until you confirm.
    """
    when = _as_of(as_of)
    entry_id = _entry_id(entry)
    text = sys.stdin.read() if (squad is None or squad.strip() == "-") else squad
    if not text.strip():
        console.print("[red]Nothing to parse.[/] Pass the squad, or pipe it in with '-'.")
        raise typer.Exit(code=2)

    with _warehouse(db) as wh:
        snapshot = wh.snapshot_at(when)
        index = PlayerIndex.from_snapshot(snapshot, season)
        from fpl_edge.interfaces.parsing import PlayerResolver

        draft = build_draft(
            text,
            resolver=PlayerResolver(snapshot.players(season)),
            index=index,
            entry_id=entry_id,
            season=season,
            gw=gw,
            now=when,
            source="cli",
        )
        echo(draft.render(index))
        if draft.record is None:
            raise typer.Exit(code=1)

        store = MyTeamStore(entry_id, root=store_root)
        token = store.stage(draft)
        if yes:
            store.confirm(token, now=when)
            console.print()
            console.print(f"[green]Saved.[/] {store.path}")
            return
        console.print()
        console.print(f"[dim]Staged. Run `fpl myteam confirm {token}` to save it.[/]")


@app.command("confirm")
def confirm(
    token: str = typer.Argument(..., help="The token from `fpl myteam set`."),
    entry: int = EntryOpt,
    store_root: Path = StoreOpt,
    as_of: str = AsOfOpt,
) -> None:
    """Save the staged squad. The token has to match, so a stale draft cannot slip through."""
    store = MyTeamStore(_entry_id(entry), root=store_root)
    try:
        record = store.confirm(token, now=_as_of(as_of))
    except (NoSuchDraftError, ValueError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
    console.print(
        f"[green]Saved[/] {len(record.codes)} players for {record.season} "
        f"GW{int(record.gw)} → {store.path}"
    )


@app.command("discard")
def discard(entry: int = EntryOpt, store_root: Path = StoreOpt) -> None:
    """Throw away the staged squad without saving it."""
    dropped = MyTeamStore(_entry_id(entry), root=store_root).discard()
    console.print("Discarded." if dropped else "Nothing was staged.")


# -- fpl myteam sync ---------------------------------------------------------


@app.command("sync")
def sync(
    db: Path = DbOpt,
    season: str = SeasonOpt,
    as_of: str = AsOfOpt,
    entry: int = EntryOpt,
    store_root: Path = StoreOpt,
    gw: int = typer.Option(None, "--gw", help="Gameweek to reconcile. Defaults to the last played."),
) -> None:
    """Switch to the public picks once a gameweek has started, and reconcile.

    From the moment a gameweek kicks off, FPL publishes the 15 and the manual
    record stops being the source of truth. This command reports any
    disagreement between the two rather than overwriting either: a difference
    explained by transfers made after you told us is fine, and a difference that
    is not means the manual entry was wrong and every recommendation made off it
    was made off a fiction.
    """
    when = _as_of(as_of)
    entry_id = _entry_id(entry)
    store = MyTeamStore(entry_id, root=store_root)
    record = store.confirmed(season=season)

    with _warehouse(db) as wh, PublicEntryClient() as client:
        snapshot = wh.snapshot_at(when)
        index = PlayerIndex.from_snapshot(snapshot, season)
        history = client.history(entry_id)
        target = int(gw) if gw is not None else (
            int(history.last_gw) if history.last_gw is not None else 0
        )
        if target < 1:
            console.print(
                "No gameweek has finished yet, so FPL is not publishing any picks. "
                "The manual squad remains the only source there is."
            )
            if record is not None:
                echo(f"Manual squad on file: {len(record.codes)} players for "
                     f"{record.season} GW{int(record.gw)}, confirmed "
                     f"{record.confirmed_utc:%Y-%m-%d %H:%M}Z.")
            raise typer.Exit(code=0)

        picks = client.picks(entry_id, target)
        if picks is None:
            console.print(f"GW{target} picks are not public yet (404).")
            raise typer.Exit(code=0)

        public_codes = [index.code(p.element) for p in picks.picks]
        console.print(f"[green]GW{target} picks are public[/] "
                      f"({len(public_codes)} players). Automatic from here.")
        if record is None:
            console.print("[dim]No manual squad on file to compare against.[/]")
            raise typer.Exit(code=0)

        transfers = client.transfers(entry_id)
        between = sum(1 for t in transfers if int(record.gw) <= int(t.gw) <= target)
        result = reconcile(record, public_codes, gw=target, transfers_between=between)
        console.print()
        echo(result.render(index))
        raise typer.Exit(code=0 if result.matches or result.explained_by_transfers else 1)


# -- fpl myteam transfers ----------------------------------------------------


@app.command("transfers")
def transfers(
    db: Path = DbOpt,
    season: str = SeasonOpt,
    as_of: str = AsOfOpt,
    entry: int = EntryOpt,
    store_root: Path = StoreOpt,
    gw: int = typer.Option(None, "--gw", help="Defaults to the next open gameweek."),
    horizon: int = typer.Option(3, "--horizon", help="Gameweeks to plan over."),
    mode: str = typer.Option(
        "rank-utility", "--mode",
        help="rank-utility (the real objective) or expected-points (a surrogate).",
    ),
    xpts: Path = typer.Option(
        None, "--xpts",
        help="Points forecast table with columns code, gw, xpts, p_play.",
    ),
    candidates: int = typer.Option(12, "--candidates", help="Alternatives to solve in full."),
) -> None:
    """What to do at the upcoming deadline, what it costs, and what lost."""
    when = _as_of(as_of)
    entry_id = _entry_id(entry)
    store = MyTeamStore(entry_id, root=store_root)
    chosen_mode = {
        "rank-utility": ObjectiveMode.RANK_UTILITY,
        "rank_utility": ObjectiveMode.RANK_UTILITY,
        "expected-points": ObjectiveMode.EXPECTED_POINTS,
        "expected_points": ObjectiveMode.EXPECTED_POINTS,
    }.get(mode.lower())
    if chosen_mode is None:
        raise typer.BadParameter("--mode must be rank-utility or expected-points")

    points_forecast = None
    if xpts is not None:
        import pandas as pd

        frame = pd.read_parquet(xpts) if xpts.suffix == ".parquet" else pd.read_csv(xpts)
        points_forecast = TablePointsForecast(frame=frame, name=f"table:{xpts.name}")

    with _warehouse(db) as wh, PublicEntryClient() as client:
        snapshot = wh.snapshot_at(when)
        index = PlayerIndex.from_snapshot(snapshot, season)
        state = reconstruct(
            snapshot, entry_id=entry_id, season=season, client=client,
            manual=store.confirmed(season=season),
            private=_try_private(entry_id), gw=gw,
        )
        target = int(gw) if gw is not None else int(state.gw)
        try:
            rec = recommend(
                snapshot, state, season=season,
                gws=list(range(target, target + horizon)),
                points_forecast=points_forecast, mode=chosen_mode,
                candidates=candidates,
            )
        except RankUtilityUnavailableError as exc:
            console.print("[yellow]The rank-utility objective has no provider.[/]")
            echo(str(exc))
            console.print()
            console.print(
                "[dim]An expected-points answer is a genuinely different "
                "recommendation, not an approximation of this one. Ask for it by "
                "name with --mode expected-points.[/]"
            )
            raise typer.Exit(code=3) from exc
        except PointsForecastUnavailableError as exc:
            console.print("[yellow]No points forecast.[/]")
            echo(str(exc))
            raise typer.Exit(code=3) from exc
        except NoSquadError as exc:
            console.print("[yellow]Your squad is not known.[/]")
            echo(str(exc))
            raise typer.Exit(code=3) from exc

        echo(rec.render(index))


# -- fpl myteam whynot -------------------------------------------------------


@app.command("whynot")
def whynot(entry: int = EntryOpt) -> None:
    """How the engine reads your team, and why it never takes a password."""
    console.print(
        "The engine never stores or asks for your FPL password: the login runs "
        "through the Premier League SSO behind anti-bot protection, a stored "
        "password grants full account control, and neither is needed.\n\n"
        "Instead, three sources in order of strength:\n"
        "  1. FPL_SESSION_COOKIE in .env -- your own browser session, pasted by "
        "you, revocable by logging out. Reads /api/my-team/ live, including the "
        "pre-deadline squad, exact purchase prices and bank. Check it with "
        "`fpl myteam cookie`.\n"
        "  2. Public picks -- /api/entry/{id}/event/{gw}/picks/ once a gameweek "
        "has started.\n"
        "  3. Manual entry -- `fpl myteam set`, confirmed back before saving."
    )


@app.command("cookie")
def cookie_probe(entry: int = EntryOpt) -> None:
    """Test the FPL session cookie end to end, without printing it."""
    from fpl_edge.myteam.private import (
        NoSessionError,
        PrivateTeamClient,
        StaleSessionError,
    )

    entry_id = _entry_id(entry)
    client = PrivateTeamClient()
    try:
        squad = client.fetch(entry_id)
    except (NoSessionError, StaleSessionError) as exc:
        echo(str(exc))
        raise typer.Exit(1)
    console.print(
        f"[green]Session OK.[/green] Live squad read: 15 picks, "
        f"bank {squad.bank}, value {squad.squad_value}, "
        f"free transfers {squad.free_transfers}, "
        f"chips: " + ", ".join(f"{k}={v}" for k, v in sorted(squad.chips.items()))
    )
    console.print(
        "The weekly report and `myteam show` now read this squad automatically."
    )


if __name__ == "__main__":  # pragma: no cover
    app()
