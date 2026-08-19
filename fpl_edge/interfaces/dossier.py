"""The dossier: everything the engine knows about one player, in one view.

The user's request was literal -- *"if I say I like a player, find out everything
about it"* -- and the failure mode of answering it is equally literal. A view
that silently drops the sections it has no data for reads as a complete picture
of a player, and the reader has no way to tell the difference between "the odds
say nothing unusual" and "we have no odds". So this module copies the convention
:mod:`fpl_edge.interfaces.report` established for the weekly report: the set of
sections a dossier is *supposed* to contain is declared up front in
:data:`EXPECTED`, each one either renders or records why it could not, and the
ones that could not are printed at the end with the reason.

That is why :class:`Section` has both ``body`` and ``gap`` and why exactly one of
them is set. A section is never merely absent.

Where the data comes from
-------------------------
Everything is read at one ``as_of`` instant through a single
:class:`~fpl_edge.store.Snapshot`, so a dossier is reproducible: the same
``--as-of`` gives the same answer next week. The model inputs are the engine's
own -- per-90 rates from :mod:`fpl_edge.models.points.shares`, fixture difficulty
from a live Dixon-Coles fit rather than FPL's published FDR colours, ownership
and effective ownership from :mod:`fpl_edge.models.ownership` -- and the intel
sections come from :mod:`fpl_edge.intel`, which dates every item by when the
world could have known it.

Three surfaces, one implementation
----------------------------------
:func:`build` is the whole thing. ``fpl dossier``, the MCP tool and the Telegram
reply are three renderers over it (:meth:`Dossier.render`, :meth:`Dossier.to_dict`,
:meth:`Dossier.telegram`), for the same reason the idea inbox has one
:meth:`~fpl_edge.interfaces.inbox.IdeaInbox.submit`: three surfaces that each
built their own view would drift into three different answers to the same
question, and the user would have no way to know which one to believe.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fpl_edge.interfaces.features import player_universe
from fpl_edge.interfaces.ideas import CandidateMatch, Clarification
from fpl_edge.interfaces.parsing import PlayerResolver
from fpl_edge.store import DEFAULT_DB, Snapshot, Warehouse
from fpl_edge.types import PlayerCode, Position

UTC = dt.timezone.utc

DEFAULT_SEASON = "2026-27"
DEFAULT_HISTORY = ("2022-23", "2023-24", "2024-25", "2025-26")

#: Where `scripts/gw1_projection.py` leaves the simulated point distribution.
#: Read rather than recomputed by default: the full pipeline fits a gradient-
#: boosted minutes model over four seasons and takes about 95 seconds on this
#: machine, which is fine for a scheduled run and unacceptable for a chat reply.
#: `--simulate` runs it live for anyone who wants the wait.
PROJECTION_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "warehouse" / "gw1_projection.parquet"
)

POS_NAME = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

#: What a complete dossier contains. A key here with no rendered body is
#: reported as an explicit gap, never omitted. Ordering is the render order.
EXPECTED: dict[str, str] = {
    "identity": "who the player is, and whether the game will let you pick him",
    "price": "price and price-change pressure",
    "ownership": "ownership and effective ownership",
    "projection": "our projected points distribution",
    "minutes": "minutes and rotation risk",
    "fixtures": "upcoming fixture difficulty from our own ratings",
    "rates": "underlying xG and xA per 90",
    "set_pieces": "set-piece and penalty duty, and any change to it",
    "defensive": "defensive-contribution likelihood",
    "odds": "the bookmakers' anytime-scorer price",
    "availability": "injury and availability news, with its timestamp",
    "tactical": "out-of-position and formation signals",
    "press": "press-conference and club-news coverage",
    "creators": "what content creators are saying",
    "elite": "what skilled managers own",
    "disagreement": "where our model and the market disagree",
}


@dataclass(frozen=True, slots=True)
class Section:
    """One block of the dossier. Exactly one of ``body`` and ``gap`` is set."""

    key: str
    title: str
    body: str | None = None
    gap: str | None = None

    def __post_init__(self) -> None:
        if (self.body is None) == (self.gap is None):
            raise ValueError(
                f"section {self.key!r} must have exactly one of body and gap; "
                "a section with neither is the silent omission this class exists "
                "to make impossible, and one with both is ambiguous."
            )

    @property
    def present(self) -> bool:
        return self.body is not None


def _ok(key: str, body: str) -> Section:
    return Section(key=key, title=EXPECTED[key], body=body)


def _gap(key: str, why: str) -> Section:
    return Section(key=key, title=EXPECTED[key], gap=why)


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


# -- resolution ---------------------------------------------------------------


def resolve(
    snapshot: Snapshot, query: str, *, season: str
) -> tuple[PlayerCode | None, Clarification | None, pd.DataFrame]:
    """Fuzzy name -> one player, or a refusal to guess.

    Delegates to :class:`~fpl_edge.interfaces.parsing.PlayerResolver` rather than
    matching names here. That resolver already knows that "salah" and "saliba"
    are two edits apart and must not be confused, that "kdb" is a nickname no
    string distance recovers, and that a tie is answered by asking rather than by
    breaking it on ownership. Reimplementing any of that would mean a name
    resolves one way through the idea inbox and another through the dossier.
    """
    players = player_universe(snapshot, season)
    if players.empty:
        return None, Clarification(
            raw_text=query,
            question=f"No player list in the warehouse for {season} at {snapshot.as_of:%Y-%m-%d %H:%M}Z.",
            candidates=(), pending_id="", kind="no_universe",
        ), players

    res = PlayerResolver(players).resolve(query)
    if res.best is not None:
        return res.best.code, None, players

    if res.ambiguous:
        question = f"Which {query.strip()} did you mean?"
        kind = "ambiguous"
    else:
        question = f"I cannot find a player matching {query.strip()!r}."
        kind = "not_found"
    return None, Clarification(
        raw_text=query, question=question, candidates=res.candidates,
        pending_id="", kind=kind,
    ), players


# -- the loaded context every section reads from ------------------------------


@dataclass
class _Ctx:
    """Everything loaded once, so sixteen sections do not each hit the warehouse."""

    wh: Warehouse
    snap: Snapshot
    season: str
    gw: int
    as_of: dt.datetime
    code: int
    row: pd.Series
    players: pd.DataFrame
    teams: pd.DataFrame
    history: tuple[str, ...]
    warnings: list[str] = field(default_factory=list)

    # Lazily filled by the loaders below; None means "tried and could not".
    rates: Any = None
    rates_error: str | None = None
    fixtures: pd.DataFrame | None = None
    goal_model: Any = None
    goals_error: str | None = None
    ownership: pd.DataFrame | None = None
    ownership_error: str | None = None
    projection: pd.DataFrame | None = None
    projection_error: str | None = None
    projection_stamp: dt.datetime | None = None
    intel: Any = None
    intel_error: str | None = None

    @property
    def position(self) -> int:
        return int(self.row["position"])

    @property
    def team_code(self) -> int:
        return int(self.row["team_code"])

    @property
    def web_name(self) -> str:
        return str(self.row["web_name"])

    def team_name(self, code: int | None = None) -> str:
        code = self.team_code if code is None else int(code)
        if self.teams.empty:
            return f"team {code}"
        hit = self.teams[self.teams["team_code"].astype(int) == int(code)]
        return str(hit.iloc[0]["name"]) if not hit.empty else f"team {code}"


def _load_rates(ctx: _Ctx) -> None:
    from fpl_edge.models.points.shares import estimate_rates

    try:
        ctx.rates = estimate_rates(ctx.snap, list(ctx.history))
    except (ValueError, KeyError) as exc:
        ctx.rates_error = f"{type(exc).__name__}: {exc}"


def _load_fixtures(ctx: _Ctx, horizon: int) -> None:
    from fpl_edge.models.team_goals import DixonColesModel

    ctx.fixtures = ctx.snap.upcoming_fixtures(ctx.season, horizon_gws=horizon)
    try:
        model = DixonColesModel()
        model.fit(ctx.snap, ctx.season)
        ctx.goal_model = model
    except (ValueError, KeyError) as exc:
        ctx.goals_error = f"{type(exc).__name__}: {exc}"


def _load_ownership(ctx: _Ctx) -> None:
    from fpl_edge.models.ownership.model import OwnershipForecaster

    try:
        ctx.ownership = OwnershipForecaster().forecast(ctx.snap, ctx.season, ctx.gw)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        ctx.ownership_error = f"{type(exc).__name__}: {exc}"


def _load_projection(ctx: _Ctx, *, simulate: bool, n_sims: int, path: Path) -> None:
    if simulate:
        try:
            ctx.projection = _simulate_projection(ctx, n_sims=n_sims)
            ctx.projection_stamp = ctx.as_of
        except (ValueError, KeyError, ImportError) as exc:
            ctx.projection_error = f"live simulation failed — {type(exc).__name__}: {exc}"
        return
    if not path.exists():
        ctx.projection_error = (
            f"no cached projection at {path}. Run `uv run python scripts/gw1_projection.py` "
            "to produce one, or pass --simulate to run the points model inline "
            "(about 95 s: it fits the minutes model over four seasons first)."
        )
        return
    try:
        ctx.projection = pd.read_parquet(path)
        ctx.projection_stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except (OSError, ValueError) as exc:
        ctx.projection_error = f"{type(exc).__name__} reading {path}: {exc}"


def _simulate_projection(ctx: _Ctx, *, n_sims: int) -> pd.DataFrame:
    """Run the real decomposed points model for this gameweek.

    Slow by construction, not by accident: the minutes model is a gradient
    boosting classifier trained on every player-fixture in the visible history,
    and the point of the decomposed model is that scorelines, minutes and shares
    are sampled *jointly* so two defenders in the same team keep a clean sheet in
    the same simulation. Neither part can be cached across as-of instants without
    reintroducing exactly the leakage the snapshot prevents.
    """
    from fpl_edge.models.minutes import GBMMinutesModel, TrainingSetBuilder
    from fpl_edge.models.points.model import DecomposedPointsModel
    from fpl_edge.models.team_goals import DixonColesModel

    goals = ctx.goal_model
    if goals is None:
        goals = DixonColesModel()
        goals.fit(ctx.snap, ctx.season)
    builder = TrainingSetBuilder(snapshot_at=ctx.wh.snapshot_at, catalog=ctx.snap)
    minutes = GBMMinutesModel().fit(builder.build(list(ctx.history)))
    rates = ctx.rates
    if rates is None:
        from fpl_edge.models.points.shares import estimate_rates

        rates = estimate_rates(ctx.snap, list(ctx.history))
    model = DecomposedPointsModel(goal_model=goals, minutes_model=minutes, rates=rates)
    sample = model.simulate(ctx.snap, ctx.season, ctx.gw, n_sims=n_sims, seed=20260821)
    return pd.DataFrame(
        {
            "code": sample.codes,
            "xpts": sample.mean(),
            "p10": sample.quantile(0.10),
            "p90": sample.quantile(0.90),
            "p_haul": sample.p_at_least(10),
            "p_blank": (sample.points <= 2).mean(axis=1),
        }
    ).set_index("code")


def _load_intel(ctx: _Ctx) -> None:
    from fpl_edge.intel.store import IntelStore

    try:
        store, exists = IntelStore.open_reader(ctx.wh)
    except Exception as exc:  # noqa: BLE001 - a broken intel table must not kill the dossier
        ctx.intel_error = f"{type(exc).__name__}: {exc}"
        return
    if not exists:
        ctx.intel_error = (
            "the intel tables do not exist in this warehouse. Run "
            "`uv run python -m fpl_edge.intel.cli collect` once to create and fill them."
        )
        return
    ctx.intel = store


# -- sections -----------------------------------------------------------------


def _identity(ctx: _Ctx) -> Section:
    row = ctx.row
    pos = POS_NAME.get(ctx.position, "?")
    status = str(row.get("status") or "?")
    chance = row.get("chance_of_playing_next_round")
    selectable = row.get("can_select")
    lines = [
        f"{ctx.web_name} — {pos}, {ctx.team_name()}, "
        f"£{float(row['price_tenths']) / 10:.1f}m, code {ctx.code}.",
        f"FPL status {status!r}"
        + (f", stated {int(chance)}% chance of playing next round" if pd.notna(chance) else "")
        + ".",
    ]
    if pd.notna(selectable):
        lines.append(
            "The game "
            + ("WILL" if bool(selectable) else "will NOT")
            + " currently let you select him (FPL's own can_select flag, which is what "
            "the game enforces and can differ from status)."
        )
    else:
        lines.append(
            "FPL's can_select flag is absent from this warehouse row, so selectability "
            "is inferred from status alone."
        )
    ident = pd.Timestamp(row["identity_as_of"])
    state = pd.Timestamp(row["state_as_of"])
    lines.append(
        f"Identity row as of {ident:%d %b %H:%M}Z; price/ownership row as of "
        f"{state:%d %b %H:%M}Z."
    )
    return _ok("identity", "\n".join(lines))


def _price(ctx: _Ctx) -> Section:
    from fpl_edge.intel.bootstrap import latest_element, price_pressure

    price = float(ctx.row["price_tenths"]) / 10.0
    lines = [f"Current price £{price:.1f}m."]
    hit = latest_element(ctx.code, until=ctx.as_of)
    if hit is None:
        lines.append(
            "No archived bootstrap body carries this player at or before this instant, "
            "so FPL's own price-change projection is unavailable. Net transfers this "
            f"gameweek from the warehouse: "
            f"{int(ctx.row.get('transfers_in_event') or 0) - int(ctx.row.get('transfers_out_event') or 0):+,}."
        )
        return _ok("price", "\n".join(lines))
    element, when = hit
    pressure = price_pressure(element)
    lines.append(pressure.summary())
    if pressure.locked_until:
        lines.append(f"FPL reports the price locked until {pressure.locked_until}.")
    if pressure.calibrating:
        lines.append(
            "FPL flags this player's price model as still CALIBRATING, so the projection "
            "above is its own low-confidence estimate."
        )
    lines.append(f"Read from the bootstrap poll archived at {when:%d %b %H:%M}Z.")
    lines.append(
        "Note: the rule registry records `prices.in_season_change_time_utc` as "
        "UNVERIFIED, so nothing here assumes a nightly change time — these are FPL's "
        "stated numbers, not our forecast of when the change lands."
    )
    return _ok("price", "\n".join(lines))


def _ownership(ctx: _Ctx) -> Section:
    own = ctx.row.get("selected_by_pct")
    lines = []
    if pd.notna(own):
        lines.append(f"Selected by {float(own):.1f}% of the field (FPL's own figure).")
    if ctx.ownership is None:
        lines.append(
            "Effective ownership unavailable — " + (ctx.ownership_error or "no forecast.")
        )
        return _ok("ownership", "\n".join(lines)) if lines else _gap(
            "ownership", ctx.ownership_error or "no ownership data."
        )
    hit = ctx.ownership[ctx.ownership["code"].astype(int) == ctx.code]
    if hit.empty:
        lines.append("The ownership forecast has no row for this player.")
        return _ok("ownership", "\n".join(lines))
    r = hit.iloc[0]
    lines.append(
        f"Forecast for GW{ctx.gw}: ownership {float(r['own_mean']):.1%} "
        f"(80% interval {float(r['own_lo']):.1%}–{float(r['own_hi']):.1%}), "
        f"start share {float(r['start_share']):.1%}, "
        f"captaincy share {float(r['captaincy_share']):.2%}."
    )
    lines.append(
        f"Effective ownership {float(r['eo_overall']):.1%} overall, "
        f"{float(r['eo_top10k']):.1%} in the top 10k."
    )
    if bool(r.get("eo_top10k_is_prior", False)):
        lines.append(
            "The top-10k figure is a PRIOR, not a measurement: no elite squad sample has "
            "been taken for this gameweek, so it is the overall forecast tilted by the "
            "model's elite parameters rather than observed picks."
        )
    lines.append(
        f"EO is what decides rank. At {float(r['eo_overall']):.1%} EO, every point he "
        f"scores moves you {1 - float(r['eo_overall']):.2f} points against the field if "
        f"you own him, and {float(r['eo_overall']):.2f} against you if you do not."
    )
    lines.append(f"Forecast path: {r.get('path', 'unknown')}.")
    return _ok("ownership", "\n".join(lines))


def _projection(ctx: _Ctx) -> Section:
    if ctx.projection is None:
        return _gap("projection", ctx.projection_error or "no projection available.")
    frame = ctx.projection
    if ctx.code not in frame.index:
        return _gap(
            "projection",
            f"the projection covers {len(frame)} players but not code {ctx.code}. He is "
            "most likely not selectable at this instant, so the points model excluded him.",
        )
    r = frame.loc[ctx.code]
    lines = [
        f"Expected points GW{ctx.gw}: {float(r['xpts']):.2f}.",
        f"Distribution: p10 {float(r['p10']):.0f}, p90 {float(r['p90']):.0f}. "
        f"P(haul, 10+) = {float(r['p_haul']):.1%}. P(blank, 2 or fewer) = "
        f"{float(r['p_blank']):.1%}.",
    ]
    rank = int((frame["xpts"] > float(r["xpts"])).sum()) + 1
    lines.append(f"Ranks {rank} of {len(frame)} on expected points.")
    price = float(ctx.row["price_tenths"]) / 10.0
    lines.append(f"Value {float(r['xpts']) / price:.2f} points per £m.")
    if ctx.projection_stamp is not None:
        lines.append(
            f"Source: {'live simulation at' if ctx.projection_stamp == ctx.as_of else 'cached artefact written'} "
            f"{ctx.projection_stamp:%d %b %H:%M}Z."
        )
    if ctx.projection_stamp is not None and ctx.projection_stamp < ctx.as_of - dt.timedelta(hours=12):
        ctx.warnings.append(
            f"The point projection is {(ctx.as_of - ctx.projection_stamp).total_seconds() / 3600:.0f}h "
            "old and predates news that may be in this dossier. Re-run it, or pass --simulate."
        )
    lines.append(
        "These are draws from the decomposed points model: a scoreline is sampled from "
        "the team model, minutes from the minutes model, then goals and assists are "
        "allocated by per-90 share. Correlated within a fixture by construction."
    )
    return _ok("projection", "\n".join(lines))


def _minutes(ctx: _Ctx) -> Section:
    lines: list[str] = []
    if ctx.rates is not None and ctx.code in ctx.rates.frame.index:
        mins = float(ctx.rates.frame.loc[ctx.code, "minutes"])
        lines.append(
            f"{mins:,.0f} season-decayed minutes of history across {', '.join(ctx.history)}. "
            f"That is about {mins / 90:.1f} full matches after the 0.55-per-season decay."
        )
        if mins < 900:
            lines.append(
                "Below 900 weighted minutes, the empirical-Bayes shrinkage in the rate "
                "model pulls him most of the way onto the positional prior — his rates "
                "below describe his position more than they describe him."
            )
    else:
        lines.append(
            "No minutes history in the visible seasons: a new signing, a promoted-club "
            "player, or someone who has not played. The rate model gives him the "
            "positional prior rather than a zero."
        )
    if ctx.projection is not None and ctx.code in ctx.projection.index:
        p_blank = float(ctx.projection.loc[ctx.code, "p_blank"])
        lines.append(
            f"The simulation returns 2 points or fewer in {p_blank:.0%} of draws, which "
            "bundles rotation, a cameo and a quiet 90 minutes together."
        )
    chance = ctx.row.get("chance_of_playing_next_round")
    if pd.notna(chance):
        lines.append(f"FPL states a {int(chance)}% chance of playing next round.")
    else:
        lines.append(
            "FPL states no chance-of-playing figure. It leaves this null both for "
            "'definitely fine' and for 'we have not said', so the status code above is "
            "what breaks the tie."
        )
    lines.append(
        "Not included: the gradient-boosted minutes model's own per-bucket "
        "probabilities. It is only fitted on the --simulate path (about 95 s), so a "
        "cached dossier reports history and the simulated blank rate instead of "
        "quoting a number it did not compute."
    )
    return _ok("minutes", "\n".join(lines))


def _fixtures(ctx: _Ctx, horizon: int) -> Section:
    if ctx.fixtures is None or ctx.fixtures.empty:
        return _gap("fixtures", f"no upcoming fixtures for {ctx.season} at this instant.")
    mine = ctx.fixtures[
        (ctx.fixtures["home_team_code"].astype(int) == ctx.team_code)
        | (ctx.fixtures["away_team_code"].astype(int) == ctx.team_code)
    ]
    if mine.empty:
        return _gap(
            "fixtures",
            f"{ctx.team_name()} has no fixture in the next {horizon} gameweeks at this "
            "instant — a blank, or the schedule is not yet published.",
        )
    if ctx.goal_model is None:
        listing = "\n".join(
            f"  GW{int(f.gw)} {'H' if int(f.home_team_code) == ctx.team_code else 'A'} vs "
            f"{ctx.team_name(f.away_team_code if int(f.home_team_code) == ctx.team_code else f.home_team_code)}"
            for f in mine.itertuples()
        )
        return _ok(
            "fixtures",
            f"Fixture list only — our own difficulty ratings are unavailable "
            f"({ctx.goals_error}).\n{listing}",
        )

    preds = ctx.goal_model.predict(ctx.snap, ctx.season, sorted({int(g) for g in mine["gw"]}))
    ours = preds[preds["team_code"].astype(int) == ctx.team_code]
    lines = [
        "Difficulty from our own fitted Dixon-Coles ratings, not FPL's published FDR "
        "colours. FDR is a fixed 1-5 label set before the season; these are expected "
        "goals for and against re-fitted from results visible at this instant.",
        "",
        "  GW  H/A  Opponent                 xGF   xGA   P(CS)",
    ]
    total_for = total_against = 0.0
    for f in mine.itertuples():
        home = int(f.home_team_code) == ctx.team_code
        opp = int(f.away_team_code) if home else int(f.home_team_code)
        row = ours[ours["fixture_id"].astype(int) == int(f.fixture_id)]
        if row.empty:
            lines.append(
                f"  {int(f.gw):>2}  {'H' if home else 'A'}    {ctx.team_name(opp)[:22]:<22}  "
                "  —     —      —"
            )
            continue
        r = row.iloc[0]
        total_for += float(r["exp_goals_for"])
        total_against += float(r["exp_goals_against"])
        lines.append(
            f"  {int(f.gw):>2}  {'H' if home else 'A'}    {ctx.team_name(opp)[:22]:<22}  "
            f"{float(r['exp_goals_for']):.2f}  {float(r['exp_goals_against']):.2f}  "
            f"{float(r['p_clean_sheet']):.0%}"
        )
    lines.append("")
    lines.append(
        f"Over the run: {total_for:.2f} expected goals for, {total_against:.2f} against."
    )
    return _ok("fixtures", "\n".join(lines))


def _rates(ctx: _Ctx) -> Section:
    if ctx.rates is None:
        return _gap("rates", ctx.rates_error or "the rate model did not produce rates.")
    frame = ctx.rates.frame
    if ctx.code not in frame.index:
        prior = ctx.rates.position_priors
        pos = ctx.position
        if pos not in prior.index:
            return _gap(
                "rates",
                f"no history for code {ctx.code} and no positional prior for position {pos}.",
            )
        p = prior.loc[pos]
        return _ok(
            "rates",
            f"No history in {', '.join(ctx.history)}. The model falls back to the "
            f"{POS_NAME.get(pos, '?')} prior: xG90 {float(p['xg90']):.3f}, "
            f"xA90 {float(p['xa90']):.3f}, defensive-contribution rate "
            f"{float(p['dc_rate']):.3f}. That is a statement about his position, not "
            "about him — treat every number downstream of it accordingly.",
        )
    r = frame.loc[ctx.code]
    prior = ctx.rates.position_priors.loc[ctx.position]
    peers = frame.join(ctx.players.set_index("code")[["position"]], how="inner")
    same = peers[peers["position"].astype(int) == ctx.position]
    same = same[same["minutes"] >= 900]

    def pct(col: str) -> str:
        if same.empty:
            return "—"
        return f"{float((same[col] <= float(r[col])).mean()):.0%}"

    lines = [
        f"Per 90, shrunk toward the {POS_NAME.get(ctx.position, '?')} prior with a "
        "12-times-90-minutes pseudo-count:",
        f"  xG90   {float(r['xg90']):.3f}  (prior {float(prior['xg90']):.3f}, "
        f"{pct('xg90')} percentile among {POS_NAME.get(ctx.position, '?')}s with 900+ minutes)",
        f"  xA90   {float(r['xa90']):.3f}  (prior {float(prior['xa90']):.3f}, {pct('xa90')} percentile)",
        f"  xGI90  {float(r['xg90']) + float(r['xa90']):.3f}",
        f"  BPS90  {float(r['bps90']):.1f}",
        f"  yellow {float(r['yellow90']):.3f} per 90",
    ]
    if ctx.position == int(Position.GKP):
        lines.append(f"  saves  {float(r['save90']):.2f} per 90")
    lines.append("")
    lines.append(
        "These xG figures INCLUDE penalties. FPL pays for a penalty goal exactly as for "
        "an open-play one, so total xG is the right quantity for predicting points — but "
        "it also means a player who has lost penalty duty keeps an inflated rate until "
        "the history rolls off. Cross-check against the set-piece section below."
    )
    return _ok("rates", "\n".join(lines))


def _set_pieces(ctx: _Ctx) -> Section:
    from fpl_edge.intel.setpieces import duty_summary

    if ctx.intel is None:
        return _gap("set_pieces", ctx.intel_error or "no intel store.")
    duties = ctx.intel.duties(ctx.as_of, season=ctx.season, code=ctx.code)
    lines = [duty_summary(duties)]
    if duties:
        newest = max(d.as_of for d in duties)
        lines.append(f"As FPL stated it at {newest:%d %b %H:%M}Z.")

    changes = ctx.intel.changes(ctx.as_of, code=ctx.code, limit=10)
    if changes:
        lines.append("")
        lines.append("Changes on record:")
        for c in changes:
            lines.append(
                f"  {c.detected_at:%Y-%m-%d} {c.headline} "
                f"[{c.delta_goals_per_game:+.3f} goals/game]"
            )
    else:
        lines.append("")
        lines.append(
            "No change to his duty has been detected in the observations visible at this "
            "instant. That is a real finding, not a missing one: the detector compares "
            "consecutive FPL observations and would have recorded a move."
        )
    if any(d.duty.value == "penalties" and d.ord == 1 for d in duties):
        lines.append("")
        lines.append(
            "First-choice penalties is worth roughly 0.10 goals per game — near four "
            "goals over a season, and more than the gap between most price tiers. It is "
            "also the fact most likely to change without the price moving."
        )
    return _ok("set_pieces", "\n".join(lines))


def _defensive(ctx: _Ctx) -> Section:
    from fpl_edge.rules import rules

    if ctx.position == int(Position.GKP):
        return _ok(
            "defensive",
            "Goalkeepers score nothing for defensive contribution — the rule registry "
            "has GKP at 0 points where DEF, MID and FWD all score 2.",
        )
    threshold = (
        rules().get("defensive_contribution.def_threshold")
        if ctx.position == int(Position.DEF)
        else rules().get("defensive_contribution.mid_fwd_threshold")
    )
    actions = (
        "clearances, blocks, interceptions and tackles"
        if ctx.position == int(Position.DEF)
        else "clearances, blocks, interceptions, tackles and recoveries"
    )
    if ctx.rates is None or ctx.code not in ctx.rates.frame.index:
        return _ok(
            "defensive",
            f"Threshold {threshold} ({actions}) for 2 points, and it does not stack. "
            "No history for this player, so his hit rate is the positional prior rather "
            "than a measurement.",
        )
    rate = float(ctx.rates.frame.loc[ctx.code, "dc_rate"])
    prior = float(ctx.rates.position_priors.loc[ctx.position, "dc_rate"])
    lines = [
        f"Clears the {threshold}-action threshold ({actions}) in {rate:.1%} of "
        f"appearances, against a {POS_NAME.get(ctx.position, '?')} average of {prior:.1%}.",
        f"That is worth about {rate * 2:.2f} points per appearance. It does not stack: "
        "twice the threshold still scores 2.",
    ]
    if ctx.position == int(Position.FWD):
        lines.append(
            "Forwards are eligible but this almost never fires: 9 qualifying rows in "
            "3,278 forward appearances in 2025-26, versus 4.4% for defenders and "
            "midfielders. Treat a forward's DC as noise."
        )
    return _ok("defensive", "\n".join(lines))


def _odds(ctx: _Ctx) -> Section:
    from fpl_edge.ingest.odds import implied_prob

    key = _odds_key_for(ctx)
    if key is None:
        return _gap(
            "odds",
            "no odds row matches his next fixture's date, so no bookmaker market could be "
            "looked up. `scripts/ingest_odds.py` has not covered it yet.",
        )
    rows = ctx.snap.table(
        "fact_odds",
        where="fixture_key = ? AND market = 'anytime_scorer'",
        params=[key],
    )
    if rows.empty:
        covered = ctx.snap.table("fact_odds", where="market = 'anytime_scorer'")
        n_fixtures = 0 if covered.empty else int(covered["fixture_key"].nunique())
        return _gap(
            "odds",
            f"no anytime-scorer market ingested for {key}. The warehouse currently holds "
            f"that market for {n_fixtures} fixture(s) only; `scripts/ingest_odds.py` "
            "fetches h2h, totals and clean sheets for every fixture but the player "
            "market for far fewer.",
        )
    mine = _match_selection(rows, ctx)
    if mine.empty:
        listed = ", ".join(sorted(rows["selection"].astype(str).unique())[:6])
        return _gap(
            "odds",
            f"the anytime-scorer market for {key} exists but does not list this player "
            f"(it names {listed}…). Bookmakers price only the players they expect to "
            "start in an attacking role.",
        )
    derived = mine["bookmaker"].astype(str).str.contains("#|market_|fair", regex=True)
    quoted = mine[~derived].sort_values("price_decimal")
    fair = mine[derived]
    lines = [f"Anytime scorer, {key}:"]
    if not quoted.empty:
        lines.append(
            "  book prices: "
            + ", ".join(
                f"{r.bookmaker} {float(r.price_decimal):.2f}"
                for r in quoted.head(6).itertuples()
            )
        )
        p_raw = float(np.mean([implied_prob(float(p)) for p in quoted["price_decimal"]]))
        lines.append(
            f"  mean implied probability {p_raw:.1%} BEFORE removing the overround. The "
            "true number is lower: a single-selection market has no complementary "
            "outcome to de-vig against, so this is an upper bound."
        )
    for r in fair.itertuples():
        lines.append(
            f"  de-vigged fair price ({r.bookmaker}): {float(r.price_decimal):.2f} "
            f"→ {implied_prob(float(r.price_decimal)):.1%}"
        )
    lines.append(f"Quoted as of {pd.Timestamp(mine.iloc[0]['as_of']):%d %b %H:%M}Z.")
    return _ok("odds", "\n".join(lines))


def _normalise_selections(rows: pd.DataFrame) -> pd.DataFrame:
    """Lower-case the ``selection`` column before handing it to ``devig_frame``.

    The two odds ingest paths disagree on case. ``fpl_edge.ingest.odds``'s
    football-data parser writes ``home`` / ``over_2.5``; the odds-API path writes
    ``HOME`` / ``OVER_2.5``, and every 2026-27 row in this warehouse came from
    the latter. ``fpl_edge.models.team_goals.odds.devig_frame`` matches on the
    lower-case literals, so against live data it silently returns ``{}`` and
    every market comparison degrades to "no quote" rather than raising.

    Normalising here rather than there is deliberate: ``devig_frame`` belongs to
    the team-goals team and a case fix in their module is theirs to make. This
    keeps the dossier working today and leaves their contract untouched.
    """
    if "selection" not in rows.columns:
        return rows
    return rows.assign(selection=rows["selection"].astype(str).str.lower())


def _club_similarity(fpl_name: str, slug: str) -> float:
    """How well an FPL club name matches an odds-feed slug.

    The two vocabularies genuinely disagree: FPL says "Spurs", the odds feed says
    "tottenham-hotspur"; FPL says "Man Utd", the feed says "manchester-united";
    FPL says "Nott'm Forest", the feed says "nottingham-forest". A hand-written
    alias table would fix today's twenty clubs and rot the moment one is
    relegated, so the match is fuzzy and is resolved *pairwise within a date*
    instead -- see :func:`_odds_key_for`. Even "Spurs" against
    "tottenham-hotspur" scores near zero here and the fixture still resolves,
    because the other half of the tie ("Brentford") is exact and each club plays
    at most once on a given day.
    """
    from difflib import SequenceMatcher

    from fpl_edge.interfaces.parsing import _fold, _tokens

    a = _fold(fpl_name).replace(" ", "-")
    if a == slug:
        return 1.0
    tokens = [t for t in _tokens(_fold(fpl_name)) if len(t) >= 4]
    if any(t in slug for t in tokens):
        return 0.95
    return SequenceMatcher(None, a, slug).ratio()


def _odds_key_for(ctx: _Ctx) -> str | None:
    """Find the ``fact_odds`` key for this player's next fixture.

    2026-27 odds are stored under the natural key ``season:date:home:away``
    because no odds row has been matched to an FPL ``fixture_id`` yet, so a
    lookup by ``season:fixture_id`` finds nothing at all. Reconstructing the
    natural key from FPL's own club names does not work either, for the naming
    reasons in :func:`_club_similarity`. So: shortlist by date, then score both
    halves and take the best pair.
    """
    if ctx.fixtures is None or ctx.fixtures.empty:
        return None
    mine = ctx.fixtures[
        (ctx.fixtures["home_team_code"].astype(int) == ctx.team_code)
        | (ctx.fixtures["away_team_code"].astype(int) == ctx.team_code)
    ]
    if mine.empty:
        return None
    f = mine.iloc[0]
    ko = pd.Timestamp(f["kickoff_utc"])
    if pd.isna(ko):
        return None
    day = ko.to_pydatetime().astimezone(UTC).date().isoformat()
    home = ctx.team_name(int(f["home_team_code"]))
    away = ctx.team_name(int(f["away_team_code"]))

    candidates = ctx.snap.table(
        "fact_odds", where="fixture_key LIKE ?", params=[f"{ctx.season}:{day}:%"]
    )
    if candidates.empty:
        return None
    best_key, best_score = None, 0.0
    for key in candidates["fixture_key"].astype(str).unique():
        parts = key.split(":")
        if len(parts) != 4:
            continue
        score = _club_similarity(home, parts[2]) + _club_similarity(away, parts[3])
        if score > best_score:
            best_key, best_score = key, score
    # One exact half is enough: each club plays at most once a day, so a 0.95 on
    # either side already identifies the fixture uniquely within the shortlist.
    return best_key if best_score >= 0.95 else None


def _match_selection(rows: pd.DataFrame, ctx: _Ctx) -> pd.DataFrame:
    """Find this player among a bookmaker's selection names.

    Bookmakers write "Magalhaes Gabriel" where FPL writes "Gabriel", and neither
    matches the other's ordering, so the match is on a *surname token* rather
    than on the whole string. Accents are already folded by the parser's helper.
    """
    from fpl_edge.interfaces.parsing import _fold, _tokens

    # Some rows carry the stable player code as the selection rather than a
    # name -- the derived/fair-price rows do. An exact code match beats any
    # amount of string work, so try it first.
    by_code = rows["selection"].astype(str).str.strip() == str(ctx.code)

    second = str(ctx.row.get("second_name") or "").strip()
    first = str(ctx.row.get("first_name") or "").strip()
    needles = {t for t in _tokens(_fold(second)) if len(t) >= 4}
    needles |= {t for t in _tokens(_fold(ctx.web_name)) if len(t) >= 4}
    if not needles:
        needles = {t for t in _tokens(_fold(first)) if len(t) >= 4}
    if not needles:
        return rows[by_code]
    folded = rows["selection"].astype(str).map(lambda s: set(_tokens(_fold(s))))
    # Union, not "code first". Book prices are keyed by name and the derived
    # fair price is keyed by code; taking whichever matched first would silently
    # report one and hide the other, and the gap between them is the overround
    # the reader most wants to see.
    return rows[by_code | folded.map(lambda toks: bool(toks & needles))]


def _availability(ctx: _Ctx) -> Section:
    news = str(ctx.row.get("news") or "").strip()
    lines: list[str] = []
    if ctx.intel is not None:
        from fpl_edge.intel.items import IntelKind

        items = ctx.intel.items(
            ctx.as_of, player_code=ctx.code, kind=IntelKind.AVAILABILITY, limit=6
        )
        if items:
            for i in items:
                age = i.age_at(ctx.as_of)
                lines.append(
                    f"{i.published_at:%Y-%m-%d %H:%M}Z ({age.days}d "
                    f"{age.seconds // 3600}h ago): {i.body}"
                )
                lines.append(f"    FPL classification: {i.headline}")
            lines.append("")
            lines.append(
                "Timestamps are FPL's own `news_added`, which is why this feed is used "
                "rather than a scraped injury table: a scrape carries no publication "
                "instant, so a backtest built on one applies today's injury list to a "
                "deadline three weeks ago."
            )
            fresh = [i for i in items if i.age_at(ctx.as_of) <= dt.timedelta(hours=48)]
            if fresh:
                lines.append("")
                lines.append(
                    f"{len(fresh)} of these broke within 48 hours of this instant. That is "
                    "the window in which the market has not fully adjusted."
                )
            return _ok("availability", "\n".join(lines))
    if news:
        return _ok(
            "availability",
            f"FPL news on the state row: {news}\n"
            "(No dated intel item — run the intel collector to attach `news_added`.)",
        )
    return _ok(
        "availability",
        "No availability news. FPL is carrying no news text for this player at this "
        "instant, and his status is "
        f"{str(ctx.row.get('status') or '?')!r}. Absence of news is genuinely "
        "informative here: FPL publishes a note for every flagged player.",
    )


def _tactical(ctx: _Ctx) -> Section:
    from fpl_edge.intel import oop as oop_mod
    from fpl_edge.intel.formations import recent_shapes

    if ctx.intel is None:
        return _gap("tactical", ctx.intel_error or "no intel store.")
    signals = ctx.intel.oop(ctx.as_of, season=ctx.season, code=ctx.code)
    lines = [oop_mod.explain(signals[0] if signals else None,
                             position=ctx.position, name=ctx.web_name)]

    frame = ctx.intel.formations(ctx.as_of, season=ctx.season, team_code=ctx.team_code, limit=12)
    shapes, modal, changed = recent_shapes(frame, team_code=ctx.team_code)
    lines.append("")
    if not shapes:
        lines.append(
            f"No formation observations for {ctx.team_name()} in {ctx.season} at this "
            "instant. Shape is counted from who actually started, so it is unknowable "
            "before the season's first whistle — it is not a gap in the pipeline."
        )
    else:
        lines.append(
            f"{ctx.team_name()} recent shapes (FPL classification, newest first): "
            f"{', '.join(shapes)}. Modal {modal}."
        )
        if changed:
            lines.append(
                "The most recent shape differs from the modal one — a formation change, "
                "or a one-off. Either way his minutes are less safe than the modal "
                "shape suggests."
            )
    return _ok("tactical", "\n".join(lines))


def _press(ctx: _Ctx) -> Section:
    if ctx.intel is None:
        return _gap("press", ctx.intel_error or "no intel store.")
    from fpl_edge.intel.items import IntelKind

    items = ctx.intel.items(
        ctx.as_of, player_code=ctx.code, kind=IntelKind.PRESS_CONFERENCE, limit=5
    )
    team_items = [
        i for i in ctx.intel.items(
            ctx.as_of, team_code=ctx.team_code, kind=IntelKind.PRESS_CONFERENCE, limit=8
        )
        if i.player_code != ctx.code
    ]
    if not items and not team_items:
        probes = ctx.intel.probes(limit=12)
        note = (
            "\n".join(f"  {p.render()}" for p in probes)
            if probes else
            "  No source probe on record. Run `fpl intel collect --probe` to reach the "
            "external press-conference candidates and record their real HTTP status."
        )
        return _gap(
            "press",
            "FPL attaches no press-conference link to this player or his club at this "
            f"instant, and no external source is both permitted and reachable:\n{note}",
        )
    lines = []
    if items:
        for i in items:
            lines.append(f"{i.published_at:%Y-%m-%d %H:%M}Z {i.headline}")
            lines.append(f"    {i.source_url}")
    else:
        lines.append(
            "FPL attaches no press-conference or club-news link to this player at this "
            "instant. His club's links are below; a club-level update is weaker evidence "
            "about one player than a player-level one."
        )
    if team_items:
        lines.append("")
        lines.append(f"Also linked for {ctx.team_name()}:")
        for i in team_items[:4]:
            lines.append(f"  {i.headline}")
    lines.append("")
    lines.append(
        "These are FPL's own `scout_news_link` values — the game editorially attaching "
        "club coverage, usually a press-conference write-up or a medical update, to a "
        "player. The link is surfaced, not the article: pointing at a club's press "
        "conference is not republishing it."
    )
    return _ok("press", "\n".join(lines))


def _table_exists(wh: Warehouse, *names: str) -> bool:
    found = wh.sql(
        "SELECT table_name FROM information_schema.tables WHERE table_name IN ("
        + ", ".join("?" * len(names)) + ")",
        list(names),
    )
    return len(found) == len(names)


def _creators(ctx: _Ctx) -> Section:
    """What content creators are saying about him, weighted by their track record.

    The provider is the content team's ``fpl_edge.ingest.content`` store rather
    than anything in this module. Two reasons, and the second is the important
    one: they own claim extraction and creator scoring, and an unweighted count
    of creator opinion is the template with extra steps -- creators watch each
    other and read the same ownership page, so the modal recommendation across
    the ecosystem *is* the modal squad, which the ownership model already covers
    directly. So this section reports the weighted view alongside the raw one and
    says which is which.
    """
    try:
        from fpl_edge.ingest.content.scoring import weight_lookup
        from fpl_edge.ingest.content.store import ContentStore
    except ImportError as exc:
        return _gap(
            "creators",
            "the content package (`fpl_edge.ingest.content`) is not importable in this "
            f"checkout: {exc}. Creator opinion lives on YouTube and behind the Fantasy "
            "Football Scout paywall; the fpl-server MCP can reach individual videos "
            "with `summarise_fpl_youtube`, but a consensus needs the claim store.",
        )
    if not _table_exists(ctx.wh, "content_claim", "creator_score"):
        return _gap(
            "creators",
            "the content tables do not exist in this warehouse. Run the content "
            "team's ingest to create and fill `content_claim` and `creator_score`; "
            "until then there is no claim history to aggregate, and asserting a "
            "consensus from nothing would be worse than this gap.",
        )
    # Prefer their reader, so the point-in-time predicate is theirs and cannot
    # drift from ours. It migrates on construction and therefore needs the write
    # lock, which a dossier deliberately does not hold; when that fails, fall
    # back to the same query stated explicitly rather than giving up.
    try:
        claims = ContentStore(ctx.wh).claims_visible_at(
            ctx.as_of, season=ctx.season, gameweek=ctx.gw
        )
    except Exception:  # noqa: BLE001 - read-only connection, or a migration clash
        claims = ctx.wh.sql(
            "SELECT * FROM content_claim WHERE published_at < ? AND season = ? "
            "AND gameweek = ? ORDER BY published_at, claim_id",
            [ctx.as_of, ctx.season, ctx.gw],
        )
    if claims.empty:
        return _gap(
            "creators",
            f"no creator claim about any player was published before "
            f"{ctx.as_of:%Y-%m-%d %H:%M}Z for GW{ctx.gw}. The store was queried and the "
            "point-in-time filter matched nothing.",
        )
    mine = claims[claims["player_code"].astype(int) == ctx.code]
    if mine.empty:
        return _ok(
            "creators",
            f"No creator named him for GW{ctx.gw} in the "
            f"{len(claims)} claims published before this instant. Silence about a "
            "player is weak evidence in either direction, but it does mean he is not "
            "part of the discourse driving the template right now.",
        )
    try:
        weights = weight_lookup(ctx.wh.sql("SELECT * FROM creator_score"))
    except Exception:  # noqa: BLE001 - a missing score table is not fatal here
        weights = {}
    lines = []
    for action, grp in mine.groupby("action"):
        creators = sorted(str(c) for c in grp["creator"].unique())
        earned = sum(weights.get(c, 0.0) for c in creators)
        lines.append(
            f"{action}: {len(creators)} creator(s) — {', '.join(creators[:6])}"
            f" [earned weight {earned:.2f}]"
        )
    lines.append("")
    for r in mine.sort_values("published_at", ascending=False).head(4).itertuples():
        lines.append(
            f"{pd.Timestamp(r.published_at):%d %b %H:%M}Z {r.creator} — {r.action}: "
            f"{str(r.rationale)[:200]}"
        )
        lines.append(f"    {r.source_url}")
    lines.append("")
    lines.append(
        "Counts are for reading; the earned weight is for deciding. A creator's weight "
        "is measured from their own past claims, and most creators have not earned one."
    )
    return _ok("creators", "\n".join(lines))


def _elite(ctx: _Ctx) -> Section:
    """Ownership among managers with a measured record, not a modelled tilt.

    The ownership section's top-10k figure is a *prior* until real squads are
    sampled. This one is the measurement, and it comes from the rivals team's
    ``fact_manager_pick`` -- actual locked squads, with the FPL multiplier
    preserved, so captaincy is visible rather than collapsed into a boolean.
    """
    if not _table_exists(ctx.wh, "fact_manager_pick", "fact_manager_season", "dim_player"):
        return _gap(
            "elite",
            "no elite-manager squad sample in this warehouse. The rivals crawler "
            "(`fpl_edge.ingest.rivals`) fills `fact_manager_pick`, and "
            "`ElitePicksSampler` can draw top-10k picks from the FPL API — but "
            "`/entry/{id}/event/{gw}/picks/` returns 404 for a gameweek that has not "
            "started, so before the GW1 deadline there is nothing to sample. The "
            "ownership section's top-10k number is flagged as a prior for this reason.",
        )
    picks = ctx.wh.sql(
        """
        WITH p AS (
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY entry_id, season, gw, element_id ORDER BY as_of DESC
                ) rn
                FROM fact_manager_pick WHERE as_of <= ? AND season = ? AND gw = ?
            ) WHERE rn = 1
        ), best AS (
            SELECT entry_id, min(overall_rank) AS best_rank
            FROM (
                SELECT * EXCLUDE (rn) FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY entry_id, season ORDER BY as_of DESC
                    ) rn
                    FROM fact_manager_season WHERE as_of <= ?
                ) WHERE rn = 1
            ) GROUP BY entry_id
        ), d AS (
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY season, code ORDER BY as_of DESC) rn
                FROM dim_player WHERE as_of <= ? AND season = ?
            ) WHERE rn = 1
        )
        SELECT p.entry_id, d.code, p.multiplier, p.is_captain, best.best_rank
        FROM p JOIN d ON d.element_id = p.element_id
        LEFT JOIN best USING (entry_id)
        """,
        [ctx.as_of, ctx.season, ctx.gw, ctx.as_of, ctx.as_of, ctx.season],
    )
    if picks.empty:
        return _gap(
            "elite",
            f"`fact_manager_pick` exists but holds no squad for {ctx.season} GW{ctx.gw} "
            f"visible at {ctx.as_of:%Y-%m-%d %H:%M}Z. Squads only become readable after "
            "the deadline locks them, so before GW1 this is expected rather than broken.",
        )
    managers = picks["entry_id"].nunique()
    mine = picks[picks["code"].astype(int) == ctx.code]
    owned = mine["entry_id"].nunique()
    capped = mine[mine["is_captain"].fillna(False).astype(bool)]["entry_id"].nunique()
    started = mine[mine["multiplier"].fillna(0).astype(int) > 0]["entry_id"].nunique()
    lines = [
        f"Sampled {managers} managers with squads visible at this instant.",
        f"Owned by {owned} ({owned / managers:.1%}), started by {started} "
        f"({started / managers:.1%}), captained by {capped} ({capped / managers:.1%}).",
    ]
    skilled = picks[picks["best_rank"].notna() & (picks["best_rank"] <= 100_000)]
    if not skilled.empty:
        n_skilled = skilled["entry_id"].nunique()
        mine_skilled = skilled[skilled["code"].astype(int) == ctx.code]
        own_skilled = mine_skilled["entry_id"].nunique()
        lines.append(
            f"Among the {n_skilled} with a top-100k season on record: owned by "
            f"{own_skilled} ({own_skilled / n_skilled:.1%})."
        )
    else:
        lines.append(
            "None of the sampled managers has a top-100k season on record, so no "
            "skill-filtered figure is available — this is the whole pool, not an elite one."
        )
    lines.append(
        "The pool is selected by the rivals crawler, not drawn at random. A pool "
        "assembled from past performance is biased upward by construction, which is "
        "why `source` is recorded there and why this is a measurement of THIS pool "
        "rather than of 'the top 10k'."
    )
    return _ok("elite", "\n".join(lines))


def _disagreement(ctx: _Ctx) -> Section:
    """Where our fitted model and the betting market price the same event differently.

    Deliberately at the *team* level. Player-level disagreement would need an
    anytime-scorer quote for every player, and the warehouse has that market for
    one fixture; a comparison that only ever works for eleven Arsenal players is
    not a feature, it is a coincidence. Team goal rates, by contrast, are quoted
    for every fixture and are the single largest input to a player's points.
    """
    from fpl_edge.models.team_goals.market import invert_odds
    from fpl_edge.models.team_goals.odds import devig_frame

    key = _odds_key_for(ctx)
    if key is None or ctx.goal_model is None:
        return _gap(
            "disagreement",
            "needs both our fitted goal model and a market quote for his next fixture; "
            + ("the goal model did not fit." if ctx.goal_model is None else
           "no odds row matches his next fixture."),
        )
    rows = ctx.snap.table("fact_odds", where="fixture_key = ?", params=[key])
    if rows.empty:
        return _gap("disagreement", f"no odds ingested for {key}.")
    # Book quotes only. The ingest writes derived 'market_avg', 'fair#shin' and
    # '#open' rows into the same table; mixing a consensus row into a de-vig
    # would double-count the same information and understate the overround.
    books = rows[~rows["bookmaker"].astype(str).str.contains("#|market_|fair", regex=True)]
    if books.empty:
        books = rows
    try:
        quotes = devig_frame(_normalise_selections(books), method="proportional")
        quote = quotes.get(key)
        if quote is None:
            raise ValueError("de-vig produced no quote for this fixture")
        inv = invert_odds(quote)
    except (ValueError, KeyError, IndexError) as exc:
        return _gap("disagreement", f"could not invert the market quote: {type(exc).__name__}: {exc}")

    fixture = ctx.fixtures[
        (ctx.fixtures["home_team_code"].astype(int) == ctx.team_code)
        | (ctx.fixtures["away_team_code"].astype(int) == ctx.team_code)
    ].iloc[0]
    home = int(fixture["home_team_code"]) == ctx.team_code
    market_for = inv.rates.home if home else inv.rates.away
    market_against = inv.rates.away if home else inv.rates.home

    preds = ctx.goal_model.predict(ctx.snap, ctx.season, [int(fixture["gw"])])
    ours = preds[preds["fixture_id"].astype(int) == int(fixture["fixture_id"])]
    ours = ours[ours["team_code"].astype(int) == ctx.team_code]
    if ours.empty:
        return _gap("disagreement", "our goal model produced no row for this fixture.")
    r = ours.iloc[0]
    d_for = float(r["exp_goals_for"]) - float(market_for)
    d_against = float(r["exp_goals_against"]) - float(market_against)

    lines = [
        f"{ctx.team_name()} in {key}:",
        f"  goals for      ours {float(r['exp_goals_for']):.2f}  market {float(market_for):.2f}  "
        f"({d_for:+.2f})",
        f"  goals against  ours {float(r['exp_goals_against']):.2f}  market "
        f"{float(market_against):.2f}  ({d_against:+.2f})",
        f"  de-vig residual {inv.residual:.4f}"
        + ("; totals line used" if inv.used_totals else "; no totals line, h2h only"),
        "",
    ]
    if abs(d_for) < 0.15 and abs(d_against) < 0.15:
        lines.append(
            "No meaningful disagreement. Our fit and the market are within 0.15 goals on "
            "both sides, which is inside the noise of a de-vig."
        )
    elif d_for > 0:
        lines.append(
            f"We are {d_for:+.2f} goals MORE bullish than the market on his team scoring. "
            "That is the direction in which our attackers look underpriced — and also the "
            "direction in which we are most likely to be the one who is wrong, because "
            "the closing line is the sharpest forecast available."
        )
    else:
        lines.append(
            f"We are {d_for:+.2f} goals LESS bullish than the market on his team scoring. "
            "Our projection for his attacking returns is conservative relative to the "
            "closing price."
        )
    if abs(d_against) >= 0.15:
        lines.append(
            f"On goals conceded we differ by {d_against:+.2f}, which moves clean-sheet "
            "value for his defenders and keeper in the opposite direction."
        )
    lines.append("")
    lines.append(
        f"Books used: {books['bookmaker'].nunique()} "
        f"({', '.join(sorted(books['bookmaker'].astype(str).unique())[:5])}…)."
    )
    return _ok("disagreement", "\n".join(lines))


# -- assembly -----------------------------------------------------------------


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


# -- surface registration -----------------------------------------------------


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
