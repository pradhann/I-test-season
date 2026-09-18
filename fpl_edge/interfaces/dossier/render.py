"""Assembling the dossier, and the four surfaces that serve it.

``build`` runs the loaders, then every builder in ``EXPECTED`` order, catching
per-section failures so one bad builder costs one section. ``build_text``,
``register_cli``, ``telegram_addendum`` and ``mcp_payload`` are the CLI, the
Telegram and the MCP views of the same object.

Split out of ``fpl_edge/interfaces/dossier.py`` (ARCHITECTURE_REVIEW.md
Section 3 and Section 4 row 18).
"""

from __future__ import annotations
import datetime as dt
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from fpl_edge.interfaces.ideas import CandidateMatch, Clarification
from fpl_edge.store import DEFAULT_DB, Warehouse
from fpl_edge.types import Position

from fpl_edge.interfaces.dossier.load import DEFAULT_HISTORY, DEFAULT_SEASON, PROJECTION_PATH, UTC, _Ctx, _load_fixtures, _load_intel, _load_ownership, _load_projection, _load_rates, resolve
from fpl_edge.interfaces.dossier.sections import EXPECTED, Section, _availability, _creators, _defensive, _disagreement, _elite, _fixtures, _gap, _identity, _minutes, _odds, _ownership, _press, _price, _projection, _rates, _set_pieces, _tactical


@dataclass(frozen=True, slots=True)
class Dossier:
    """Everything we know about one player at one instant."""

    query: str
    code: int
    name: str
    full_name: str
    season: str
    gw: int
    as_of: dt.datetime
    sections: tuple[Section, ...]
    build_ms: float = 0.0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def section(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)

    @property
    def gaps(self) -> tuple[Section, ...]:
        return tuple(s for s in self.sections if not s.present)

    def render(self) -> str:
        """Full plain-text dossier. No markup: it carries player news verbatim."""
        head = [
            f"# {self.full_name} — {self.season} GW{self.gw}",
            "",
            f"Read from a warehouse snapshot at {self.as_of:%Y-%m-%d %H:%M}Z. "
            f"Matched from your text {self.query!r}. Built in {self.build_ms:.0f} ms.",
            "",
        ]
        for s in self.sections:
            if not s.present:
                continue
            head.append(f"## {s.title.capitalize()}")
            head.append("")
            head.append(s.body or "")
            head.append("")
        gaps = self.gaps
        if gaps:
            head += [
                "## Not in this dossier",
                "",
                "These sections have no data or no provider at this instant. They are "
                "listed rather than dropped: a dossier that looks complete while "
                "quietly missing the odds is worse than one that admits the hole.",
                "",
            ]
            for s in gaps:
                head.append(f"- **{s.key}** — {s.title}. {s.gap}")
            head.append("")
        if self.warnings:
            head += ["## Read this before acting on the above", ""]
            head += [f"- {w}" for w in self.warnings]
        return "\n".join(head).rstrip() + "\n"

    def telegram(self, *, keys: tuple[str, ...] = ()) -> str:
        """Compact reply for a chat. Same data, fewer words.

        Telegram messages are chunked at 3,900 characters by the bot, so a full
        dossier would arrive as four separate messages on a phone. This picks the
        sections that change a decision and names the ones it left out, so the
        user knows there is more rather than assuming that was everything.
        """
        wanted = keys or (
            "identity", "availability", "projection", "set_pieces", "fixtures",
            "ownership", "tactical", "disagreement",
        )
        lines = [f"{self.full_name} — GW{self.gw} ({self.as_of:%d %b %H:%M}Z)"]
        shown = 0
        for key in wanted:
            s = self.section(key)
            if s is None or not s.present:
                continue
            shown += 1
            lines.append("")
            lines.append(f"— {s.title.upper()}")
            lines.append(_squash(s.body or ""))
        omitted = [s.key for s in self.sections if s.present and s.key not in wanted]
        if omitted:
            lines.append("")
            lines.append(f"Also known: {', '.join(omitted)}. Full view: fpl dossier {self.name}")
        gaps = self.gaps
        if gaps:
            lines.append("")
            lines.append(f"No data for: {', '.join(s.key for s in gaps)}.")
        if shown == 0:  # pragma: no cover - defensive
            lines.append("")
            lines.append("Nothing renderable at this instant.")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form, for the MCP tool.

        Gaps are first-class here too: a consumer reading the JSON gets
        ``{"body": null, "gap": "..."}`` rather than a missing key, so a model
        summarising this cannot mistake absence for a negative finding.
        """
        return {
            "query": self.query,
            "code": self.code,
            "web_name": self.name,
            "name": self.full_name,
            "season": self.season,
            "gw": self.gw,
            "as_of": self.as_of.isoformat(),
            "build_ms": round(self.build_ms, 1),
            "sections": [
                {"key": s.key, "title": s.title, "body": s.body, "gap": s.gap}
                for s in self.sections
            ],
            "gaps": [s.key for s in self.gaps],
            "warnings": list(self.warnings),
        }


def _squash(text: str, *, limit: int = 320) -> str:
    """Trim a section body for the chat surface without cutting mid-word."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0]
    return f"{cut} …"


def build(
    wh: Warehouse,
    query: str,
    *,
    season: str = DEFAULT_SEASON,
    as_of: dt.datetime | None = None,
    gw: int | None = None,
    horizon_gws: int = 5,
    history: tuple[str, ...] = DEFAULT_HISTORY,
    simulate: bool = False,
    n_sims: int = 2000,
    projection_path: Path = PROJECTION_PATH,
) -> tuple[Dossier | None, Clarification | None]:
    """Build a dossier, or return the parser's refusal to guess which player.

    Returns a pair so the caller does not have to catch an exception to handle
    the ordinary case of an ambiguous name. Every surface renders the
    clarification the same way, because it is the same object the idea inbox
    returns for the same reason.
    """
    import time

    started = time.perf_counter()
    when = (as_of or dt.datetime.now(UTC)).astimezone(UTC)
    snap = wh.snapshot_at(when)

    code, clarification, players = resolve(snap, query, season=season)
    if code is None:
        return None, clarification

    row_match = players[players["code"].astype(int) == int(code)]
    if row_match.empty:  # pragma: no cover - resolve guarantees membership
        return None, Clarification(
            raw_text=query, question=f"Resolved to code {code} but it is not in the "
            f"{season} player list.", candidates=(), pending_id="", kind="not_found",
        )
    row = row_match.iloc[0]

    if gw is None:
        try:
            gw = int(snap.next_gw(season))
        except KeyError:
            gw = 1

    ctx = _Ctx(
        wh=wh, snap=snap, season=season, gw=int(gw), as_of=when, code=int(code),
        row=row, players=players,
        teams=snap.table("dim_team", where="season = ?", params=[season]),
        history=history,
    )
    _load_rates(ctx)
    _load_fixtures(ctx, horizon_gws)
    _load_ownership(ctx)
    _load_projection(ctx, simulate=simulate, n_sims=n_sims, path=projection_path)
    _load_intel(ctx)

    builders = {
        "identity": _identity,
        "price": _price,
        "ownership": _ownership,
        "projection": _projection,
        "minutes": _minutes,
        "fixtures": lambda c: _fixtures(c, horizon_gws),
        "rates": _rates,
        "set_pieces": _set_pieces,
        "defensive": _defensive,
        "odds": _odds,
        "availability": _availability,
        "tactical": _tactical,
        "press": _press,
        "creators": _creators,
        "elite": _elite,
        "disagreement": _disagreement,
    }
    sections: list[Section] = []
    for key in EXPECTED:
        try:
            sections.append(builders[key](ctx))
        except Exception as exc:  # noqa: BLE001 - one bad section must not lose the rest
            sections.append(
                _gap(key, f"section raised {type(exc).__name__}: {exc}")
            )

    first = str(row.get("first_name") or "").strip()
    second = str(row.get("second_name") or "").strip()
    full = f"{first} {second}".strip() or str(row["web_name"])
    if full.lower() != str(row["web_name"]).lower():
        full = f"{row['web_name']} ({full})"

    return Dossier(
        query=query, code=int(code), name=str(row["web_name"]), full_name=full,
        season=season, gw=int(gw), as_of=when, sections=tuple(sections),
        build_ms=(time.perf_counter() - started) * 1000.0,
        warnings=tuple(ctx.warnings),
    ), None


def build_text(
    query: str,
    *,
    db: Path = DEFAULT_DB,
    season: str = DEFAULT_SEASON,
    as_of: dt.datetime | None = None,
    **kwargs: Any,
) -> str:
    """Open the warehouse read-only, build, render. The one-call convenience.

    Read-only on purpose: this project runs long simulations and ingests against
    the same DuckDB file, which permits a single writer, and a dossier that
    cannot be produced because a backtest holds the lock is a dossier that fails
    at exactly the moment it is wanted.
    """
    if not Path(db).exists():
        return f"No warehouse at {db}. Run `make ingest` first."
    with Warehouse(db, read_only=True) as wh:
        dossier, clarification = build(wh, query, season=season, as_of=as_of, **kwargs)
        if dossier is None:
            return clarification.render() if clarification else "Could not resolve that name."
        return dossier.render()


def register_cli(app: Any) -> None:
    """Attach ``fpl dossier`` to an existing Typer app.

    Registration rather than definition-in-place: the CLI module belongs to
    another team, and a function they call is a smaller thing to merge than a
    command body they have to maintain.
    """
    import typer

    @app.command("dossier")
    def dossier_command(  # noqa: D401 - Typer reads the docstring as help text
        name: str = typer.Argument(..., help="Player name, fuzzy. 'semenyo', 'rashfrod'."),
        db: Path = typer.Option(DEFAULT_DB, "--db", help="Path to the DuckDB warehouse."),
        season: str = typer.Option(DEFAULT_SEASON, "--season"),
        as_of: str = typer.Option(None, "--as-of", help="Read the warehouse as of this UTC instant."),
        gw: int = typer.Option(None, "--gw", help="Defaults to the next open gameweek."),
        horizon: int = typer.Option(5, "--horizon", help="Fixtures to rate ahead."),
        simulate: bool = typer.Option(
            False, "--simulate",
            help="Run the points model live (~95 s) instead of reading the cached projection.",
        ),
        as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    ) -> None:
        """Everything the engine knows about one player, in one view."""
        import json

        when = _parse_as_of(as_of)
        if not Path(db).exists():
            typer.echo(f"No warehouse at {db}. Run `make ingest` first.")
            raise typer.Exit(code=2)
        with Warehouse(db, read_only=True) as wh:
            dossier, clarification = build(
                wh, name, season=season, as_of=when, gw=gw,
                horizon_gws=horizon, simulate=simulate,
            )
        if dossier is None:
            # markup=False equivalent: typer.echo never interprets Rich markup,
            # and this string contains player names the user typed.
            typer.echo(clarification.render() if clarification else "Could not resolve that name.")
            raise typer.Exit(code=1)
        typer.echo(json.dumps(dossier.to_dict(), indent=2) if as_json else dossier.render())


def _parse_as_of(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--as-of must carry a timezone, e.g. 2026-08-21T17:30:00Z")
    return parsed.astimezone(UTC)


def telegram_addendum(
    wh: Warehouse,
    submission: Any,
    *,
    season: str = DEFAULT_SEASON,
    now: dt.datetime | None = None,
    max_chars: int = 2600,
) -> str:
    """Dossier text to append to a bot reply, or "" when there is nothing to add.

    Called by the Telegram bot after an idea is logged. Returns a string rather
    than sending anything itself, so the bot keeps sole ownership of who it is
    allowed to talk to -- the allowlist check stays in one place.

    Never raises. A failure here must degrade to "the idea was still logged",
    because the thesis and its timestamp are the durable asset and an extra
    paragraph is not.
    """
    idea = getattr(submission, "idea", None)
    code = getattr(idea, "subject_code", None) if idea is not None else None
    if code is None:
        return ""
    try:
        dossier, _ = build(
            wh, str(getattr(idea, "subject_name", None) or code),
            season=season, as_of=now,
        )
        if dossier is None:
            return ""
        text = dossier.telegram()
    except Exception:  # noqa: BLE001 - see docstring
        return ""
    return "\n\n" + (text if len(text) <= max_chars else text[:max_chars] + " …")


def mcp_payload(
    query: str,
    *,
    db: Path = DEFAULT_DB,
    season: str = DEFAULT_SEASON,
    as_of: dt.datetime | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """The MCP tool's return value: JSON-safe, with gaps preserved."""
    if not Path(db).exists():
        return {"error": f"No warehouse at {db}. Run `make ingest` in the engine repo."}
    with Warehouse(db, read_only=True) as wh:
        dossier, clarification = build(wh, query, season=season, as_of=as_of, **kwargs)
    if dossier is None and clarification is not None:
        return {
            "ambiguous": clarification.kind == "ambiguous",
            "question": clarification.question,
            "candidates": [
                {"code": int(c.code), "label": c.label, "hint": c.hint, "score": c.score}
                for c in clarification.candidates
            ],
        }
    if dossier is None:  # pragma: no cover
        return {"error": "could not resolve that name"}
    return dossier.to_dict()


def _unused_guard() -> None:  # pragma: no cover
    """Keep imports that only the type checker and future sections need."""
    _ = (CandidateMatch, math, Position)
