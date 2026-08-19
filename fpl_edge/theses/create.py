"""Creation paths: CLI, API for other teams, and the ideas-pipeline bridge.

Everything funnels into :func:`create_thesis`, which owns the two rules that
make the registry trustworthy:

* **The model verdict is captured now or never.** Every number in
  ``model_verdict_at_creation`` is read from a Snapshot at the creation
  instant, inside this function, before the file is written. There is no code
  path that fills it in later, and the leakage test in
  ``tests/unit/test_theses_leakage.py`` holds this function to that.
* **The comparator freezes now.** Peer sets and captain pools are resolved
  from the same Snapshot and written into the file as codes. Resolution reads
  only realised points for players already in the set.

Other teams call :func:`create_thesis` directly (the content team files creator
claims with ``source=ThesisSource.CREATOR``; the elite-mining team files cohort
ideas with ``source=ThesisSource.ELITE_MANAGER``). The Telegram/ideas pipeline
is bridged by :func:`sync_from_registry`, which mirrors registry ideas into
thesis files idempotently via the stored ``idea_id``.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from fpl_edge.config import USER
from fpl_edge.interfaces.features import (
    captain_pool,
    club_team_code,
    comparator_set,
    player_history,
    player_universe,
    price_peers,
    safe_float,
    safe_int,
)
from fpl_edge.interfaces.ideas import Comparator, Idea, IdeaKind
from fpl_edge.interfaces.parsing import MessageParser, PlayerResolver
from fpl_edge.store import Snapshot, Warehouse
from fpl_edge.theses.grammar import UngradeableClaimError, default_prediction, parse, render
from fpl_edge.theses.model import (
    DEFAULT_HORIZON,
    ClaimType,
    Thesis,
    ThesisSource,
    make_thesis_id,
)
from fpl_edge.theses.store import ThesesStore

log = logging.getLogger("fpl_edge.theses")

UTC = dt.timezone.utc

#: The GW1 projection the models team shipped, used to enrich the creation
#: verdict when the thesis window starts at GW1 of the live season. A file, not
#: a PIT table, so its provenance is recorded in the verdict rather than assumed.
GW1_PROJECTION = Path("data/warehouse/gw1_projection.parquet")

IDEA_KIND_TO_CLAIM: dict[IdeaKind, ClaimType] = {
    IdeaKind.CAPTAIN: ClaimType.CAPTAIN,
    IdeaKind.TRANSFER_IN: ClaimType.BUY,
    IdeaKind.FADE: ClaimType.AVOID,
    IdeaKind.DIFFERENTIAL: ClaimType.BUY,
    IdeaKind.COMPARE: ClaimType.BUY,
    IdeaKind.WATCH: ClaimType.WATCH,
}


class PlayerResolutionError(ValueError):
    """The text does not determine one player. Carries the candidates so the
    calling surface can ask instead of guessing -- a mis-resolved subject
    silently poisons both the hit rate and the bias record."""

    def __init__(self, query: str, candidates: tuple = ()) -> None:
        self.query = query
        self.candidates = candidates
        options = "".join(f"\n  - {c.label} ({c.hint})" for c in candidates)
        super().__init__(
            f"could not resolve {query!r} to exactly one player.{options or ''}\n"
            "Pass --player with an unambiguous name."
        )


def _default_gw(snapshot: Snapshot, season: str) -> int:
    """Next deadline; for archive seasons without an event calendar, the
    gameweek after the last finalised one (same inference the inbox makes)."""
    try:
        return int(snapshot.next_gw(season))
    except KeyError:
        pass
    results = snapshot.results_before(season)
    if not results.empty and "gw" in results.columns:
        return int(results["gw"].max()) + 1
    return 1


def _resolve_player(players: pd.DataFrame, query: str):
    resolution = PlayerResolver(players).resolve(query)
    best = resolution.best
    if resolution.ambiguous or not resolution.matched or best is None:
        raise PlayerResolutionError(query, resolution.candidates)
    return best


def capture_model_verdict(
    snapshot: Snapshot,
    *,
    season: str,
    player_code: int,
    gw_start: int,
    projection_path: Path = GW1_PROJECTION,
) -> dict:
    """The model's numbers for this player, at this instant. Called exactly once
    per thesis, at creation. Nothing here may be recomputed later."""
    players = snapshot.players(season)
    row = None
    if not players.empty:
        hit = players[players["code"] == int(player_code)]
        row = hit.iloc[0] if not hit.empty else None

    supported = club_team_code(snapshot, season, USER.supported_club)
    verdict: dict = {
        "as_of": snapshot.as_of,
        "price": None,
        "ownership_pct": None,
        "status": None,
        "team_code": None,
        "position": None,
        "is_supported_club": None,
    }
    if row is not None:
        price_tenths = safe_int(row["price_tenths"])
        verdict.update(
            price=(None if price_tenths is None else price_tenths / 10.0),
            ownership_pct=safe_float(row["selected_by_pct"]),
            status=(None if pd.isna(row.get("status")) else str(row["status"])),
            team_code=safe_int(row["team_code"]),
            position=safe_int(row["position"]),
            is_supported_club=(
                None if supported is None or safe_int(row["team_code"]) is None
                else bool(int(row["team_code"]) == int(supported))
            ),
        )

    history = player_history(snapshot, season)
    mine = history[history["code"] == int(player_code)] if not history.empty else history
    if not mine.empty:
        h = mine.iloc[0]
        verdict.update(
            season_ppg=safe_float(h["season_ppg"]),
            form_points_last3=safe_float(h["form_points_last3"]),
            minutes_last3=safe_int(h["minutes_last3"]),
        )
    else:
        verdict.update(season_ppg=None, form_points_last3=None, minutes_last3=None)

    # Projection numbers, only when a projection for this exact window exists.
    # The GW1 file is the only one shipped so far; it applies to live-season
    # GW1 theses and to nothing else.
    if (
        projection_path.exists()
        and season == USER.season
        and gw_start == 1
    ):
        try:
            proj = pd.read_parquet(projection_path)
            if int(player_code) in proj.index:
                p = proj.loc[int(player_code)]
                verdict.update(
                    xpts=safe_float(p.get("xpts")),
                    xpts_p10=safe_float(p.get("p10")),
                    xpts_p90=safe_float(p.get("p90")),
                    p_haul=safe_float(p.get("p_haul")),
                    p_blank=safe_float(p.get("p_blank")),
                    projection_source=projection_path.name,
                )
        except Exception:
            log.exception("projection enrichment failed for %s", player_code)
    return verdict


def _freeze_comparator(
    players: pd.DataFrame,
    subject: pd.Series | None,
    claim_type: ClaimType,
    template_id: str,
) -> tuple[tuple[int, ...], str]:
    """Comparator membership for set-based templates, frozen from the snapshot."""
    if template_id in ("beats_peer_median", "beats_peer_median_by", "trails_peer_median"):
        if subject is None:
            return (), "no subject row: peer set could not be frozen"
        peers, band = price_peers(players, subject)
        codes = tuple(int(c) for c in peers["code"])
        price = float(subject.get("price_tenths", 0) or 0) / 10.0
        band_txt = "nearest in price" if band < 0 else f"±£{band / 10:.1f}m"
        return codes, (
            f"{len(codes)} same-position players {band_txt} of £{price:.1f}m, "
            f"frozen at creation"
        )
    if template_id == "beats_captain_pool_median":
        pool = captain_pool(players)
        subject_code = int(subject["code"]) if subject is not None else None
        # The subject must not sit inside its own yardstick: "beats the median
        # of the other captaincy options" is the claim being made.
        codes = tuple(int(c) for c in pool["code"] if int(c) != subject_code)
        return codes, (
            f"{len(codes)} most-owned outfield players excluding the subject "
            f"(captaincy proxy), frozen at creation"
        )
    return (), ""


def create_thesis(
    warehouse: Warehouse,
    *,
    raw_input: str,
    source: ThesisSource | str,
    season: str | None = None,
    player: str | None = None,
    player_code: int | None = None,
    claim_type: ClaimType | str | None = None,
    creator: str | None = None,
    gw_start: int | None = None,
    horizon_gws: int | None = None,
    prediction: str | None = None,
    prose: str = "",
    acted: bool = False,
    idea_id: str | None = None,
    as_of: dt.datetime | None = None,
    store: ThesesStore | None = None,
    demote_unfalsifiable: bool = False,
) -> tuple[Thesis, Path]:
    """Create one thesis file. This is the API other teams call.

    ``player`` is a name query resolved against the player universe at
    ``as_of`` (or pass ``player_code`` directly). ``prediction`` overrides the
    claim type's default sentence and must parse against the grammar; with
    ``demote_unfalsifiable=True`` an unparseable prediction demotes the thesis
    to ``watch`` with the attempted sentence preserved in the prose -- a note,
    never a fake claim.
    """
    source = ThesisSource(str(source))
    season = season or USER.season
    store = store or ThesesStore()
    when = (as_of or dt.datetime.now(UTC)).astimezone(UTC)
    snapshot = warehouse.snapshot_at(when)

    players = player_universe(snapshot, season)
    if players.empty:
        raise RuntimeError(
            f"no {season} player universe at {when:%Y-%m-%d %H:%M}Z; run `make ingest`"
        )

    if player_code is not None:
        hit = players[players["code"] == int(player_code)]
        if hit.empty:
            raise PlayerResolutionError(f"code {player_code}")
        subject = hit.iloc[0]
        player_name = str(subject["web_name"])
    else:
        if not player and not raw_input:
            raise ValueError("need a player query or raw_input to resolve one")
        try:
            best = _resolve_player(players, player) if player else None
        except PlayerResolutionError:
            best = None
            if player:
                raise
        if best is None:
            # Fall back to parsing the raw text the way the inbox would.
            parsed = MessageParser(
                PlayerResolver(players), default_gw=_default_gw(snapshot, season)
            ).parse(raw_input)
            if parsed.subject is None or parsed.subject.best is None:
                raise PlayerResolutionError(
                    player or raw_input,
                    parsed.subject.candidates if parsed.subject else (),
                )
            best = parsed.subject.best
            if claim_type is None:
                claim_type = IDEA_KIND_TO_CLAIM[parsed.kind]
        subject = players[players["code"] == int(best.code)].iloc[0]
        player_name = str(subject["web_name"])

    claim = ClaimType(str(claim_type)) if claim_type else ClaimType.WATCH
    start = int(gw_start) if gw_start else _default_gw(snapshot, season)
    horizon = int(horizon_gws) if horizon_gws else DEFAULT_HORIZON[claim]

    note = ""
    if prediction is not None:
        try:
            template, _ = parse(prediction)
            sentence: str | None = " ".join(prediction.split())
        except UngradeableClaimError as exc:
            if not demote_unfalsifiable:
                raise
            claim = ClaimType.WATCH
            sentence = None
            note = (
                f"\n\nNOTE: demoted to watch. The attempted prediction "
                f"{prediction!r} matches no claim template, and an idea that "
                f"cannot be graded gets a note, not a fake prediction. "
                f"({type(exc).__name__})"
            )
        else:
            del template
    else:
        if claim is ClaimType.CAPTAIN:
            pool = captain_pool(players)
            if pool.empty or int(pool.iloc[0]["code"]) == int(subject["code"]):
                # No pool, or the subject IS the field's captain: "outscores the
                # most-captained player" would grade the subject against itself.
                # The claim becomes beating the median of the other options.
                sentence = default_prediction(claim, gw_start=start, horizon_gws=horizon)
            else:
                top = pool.iloc[0]
                sentence = default_prediction(
                    claim, gw_start=start, horizon_gws=horizon,
                    captain_name=str(top["web_name"]), captain_code=int(top["code"]),
                )
        else:
            sentence = default_prediction(claim, gw_start=start, horizon_gws=horizon)

    if claim is ClaimType.WATCH:
        sentence = None
        comparator_codes: tuple[int, ...] = ()
        comparator_label = ""
    else:
        assert sentence is not None
        template, _ = parse(sentence)
        comparator_codes, comparator_label = _freeze_comparator(
            players, subject, claim, template.id
        )

    verdict = capture_model_verdict(
        snapshot, season=season, player_code=int(subject["code"]), gw_start=start
    )

    thesis = Thesis(
        id=store.unique_id(make_thesis_id(when, player_name, claim)),
        created=when,
        source=source,
        creator=creator,
        raw_input=raw_input,
        player=player_name,
        player_code=int(subject["code"]),
        season=season,
        claim_type=claim,
        gw_start=start,
        horizon_gws=horizon,
        falsifiable_prediction=sentence,
        comparator_codes=comparator_codes,
        comparator_label=comparator_label,
        model_verdict_at_creation=verdict,
        acted=acted,
        idea_id=idea_id,
        prose=(prose.rstrip() + note).strip(),
    )
    path = store.write_open(thesis)
    return thesis, path


# -- the ideas-pipeline bridge ----------------------------------------------


def thesis_from_idea(
    warehouse: Warehouse,
    idea: Idea,
    *,
    store: ThesesStore | None = None,
) -> tuple[Thesis, Path]:
    """Mirror one registry idea (Telegram, CLI inbox) into a thesis file.

    The idea's own frozen semantics are preserved: the comparator set is rebuilt
    from a Snapshot at ``idea.as_of`` -- the instant the idea was had -- exactly
    as the ideas tracker does, so the file grades the same claim the inbox
    logged. ``model_verdict_at_creation`` is likewise captured at ``idea.as_of``,
    which for a mirrored idea IS creation time.
    """
    store = store or ThesesStore()
    snapshot = warehouse.snapshot_at(idea.as_of)
    season = str(idea.season)
    players = player_universe(snapshot, season)
    if idea.subject_code is None:
        raise ValueError(f"{idea.idea_id} has no resolved subject; nothing to file")

    claim = IDEA_KIND_TO_CLAIM[idea.kind]
    a, b = int(idea.gw), int(idea.gw) + int(idea.horizon_gws) - 1

    sentence: str | None
    comparator_codes: tuple[int, ...] = ()
    comparator_label = ""
    if claim is ClaimType.WATCH:
        sentence = None
    elif idea.comparator is Comparator.NAMED_PLAYER and idea.comparator_code is not None:
        rival = players[players["code"] == int(idea.comparator_code)]
        rival_name = (
            str(rival.iloc[0]["web_name"]) if not rival.empty else f"code {idea.comparator_code}"
        )
        sentence = render(
            "beats_named_player", name=rival_name, code=int(idea.comparator_code), a=a, b=b
        )
    else:
        subject_rows = players[players["code"] == int(idea.subject_code)]
        subject = subject_rows.iloc[0] if not subject_rows.empty else None
        codes, label = comparator_set(
            players, subject, idea.comparator, comparator_code=idea.comparator_code
        )
        comparator_codes = tuple(int(c) for c in codes)
        comparator_label = f"{label} (frozen at {idea.as_of:%Y-%m-%d %H:%M}Z)"
        if idea.comparator is Comparator.MEDIAN_CAPTAIN:
            sentence = render("beats_captain_pool_median", a=a, b=b)
        elif idea.kind is IdeaKind.FADE:
            sentence = render("trails_peer_median", a=a, b=b)
        else:
            sentence = render("beats_peer_median", a=a, b=b)

    verdict = capture_model_verdict(
        snapshot, season=season, player_code=int(idea.subject_code), gw_start=a
    )

    subject_rows = players[players["code"] == int(idea.subject_code)]
    player_name = (
        str(subject_rows.iloc[0]["web_name"])
        if not subject_rows.empty
        else (idea.subject_name or f"code {idea.subject_code}")
    )

    thesis = Thesis(
        id=store.unique_id(make_thesis_id(idea.created_utc, player_name, claim)),
        created=idea.created_utc,
        source=ThesisSource.USER_CHAT,
        creator=None,
        raw_input=idea.raw_text,
        player=player_name,
        player_code=int(idea.subject_code),
        season=season,
        claim_type=claim,
        gw_start=a,
        horizon_gws=int(idea.horizon_gws),
        falsifiable_prediction=sentence,
        comparator_codes=comparator_codes,
        comparator_label=comparator_label,
        model_verdict_at_creation=verdict,
        acted=bool(idea.acted),
        idea_id=idea.idea_id,
        prose=f"Filed from the ideas registry.\n\nOriginal thesis: {idea.thesis}",
    )
    path = store.write_open(thesis)
    return thesis, path


def sync_from_registry(
    warehouse: Warehouse,
    *,
    season: str | None = None,
    store: ThesesStore | None = None,
) -> list[tuple[Thesis, Path]]:
    """File a thesis for every registry idea not yet on disk. Idempotent.

    This is the wire between the live Telegram bot and the file registry: the
    bot writes ideas through the existing inbox, and each resolve/sync run
    mirrors anything new. Ideas without a resolved subject are skipped (the
    inbox is still asking the user which player they meant).
    """
    from fpl_edge.interfaces.registry import IdeaRegistry

    store = store or ThesesStore()
    season = season or USER.season
    registry = IdeaRegistry(warehouse)
    have = store.idea_ids()
    created: list[tuple[Thesis, Path]] = []
    for idea in registry.ideas(season=season):
        if idea.idea_id in have or idea.subject_code is None:
            continue
        created.append(thesis_from_idea(warehouse, idea, store=store))
    return created
