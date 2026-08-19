"""Adapters that turn each estimate -- ours, the market's, a stranger's -- into
one :mod:`.frame`-shaped projection.

Four sources, and what makes each of them independent of the others:

===========  =============================================================
``internal`` Our Dixon-Coles scorelines x minutes model x per-90 shares,
             simulated. Sees the FPL archive and nothing else.
``market``   The same simulator with the team-goal component replaced by
             rates inverted from de-vigged bookmaker 1X2 and over/under
             prices. Independent exactly where it matters -- team strength
             and fixture difficulty -- and deliberately shares the minutes
             model, so a difference between the two columns is a difference
             of opinion about *teams*, not a difference of plumbing.
``ppg``      Points per appearance over visible history times an empirical
             appearance rate. No model at all. This is the baseline every
             other source has to beat before it is worth anything.
``fplform``  A third party's own model, read out of ``fact_projection``.
===========  =============================================================

Every source takes a ``Snapshot``, so none of them can see past its own
deadline. ``ppg`` looks like the one place that could cheat -- it is a mean of
past results -- and it is guarded by reading through ``snapshot.results_before``
rather than the raw table.

Bridging the odds keys
----------------------
``fact_odds`` stores a natural key (``season:date:home-slug:away-slug``) for any
fixture whose FPL ``fixture_id`` was never resolved, and the odds team's
``--match-fixtures`` step has not been run end-to-end. :func:`odds_with_fixture_keys`
does that resolution here, at read time, using *both* alias tables the odds
module publishes -- football-data's short club names and The Odds API's long
ones -- because the historical rows and the 2026-27 rows came from different
feeds with different naming. Unmatched fixtures are reported, never guessed.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from fpl_edge.ingest.odds import (
    FD_TEAM_ALIASES,
    ODDS_API_TEAM_ALIASES,
    _slugify,
)
from fpl_edge.models.points.model import DecomposedPointsModel
from fpl_edge.models.team_goals.market import MarketImpliedModel
from fpl_edge.models.team_goals.odds import FrameOddsProvider
from fpl_edge.store import Snapshot, Warehouse


#: Clubs neither published alias table covers. football-data.co.uk writes
#: "Man United" where FPL writes "Man Utd", and The Odds API's table only knows
#: the long form "Manchester United", so 38 of 380 fixtures per season -- every
#: Manchester United match -- failed to resolve until this was added. Kept here
#: rather than pushed into fpl_edge/ingest/odds.py because that file belongs to
#: another team; if they adopt it this table can shrink to nothing.
SUPPLEMENTARY_TEAM_ALIASES: dict[str, str] = {
    "Man United": "Man Utd",
    "Nott'm Forest": "Nottingham Forest",
    "Sheffield United": "Sheffield Utd",
    "Wolverhampton Wanderers": "Wolves",
}


class NoOddsCoverageError(RuntimeError):
    """No fixture in the gameweek could be matched to a bookmaker quote."""


def odds_with_fixture_keys(
    warehouse: Warehouse, season: str, as_of: dt.datetime
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``fact_odds`` rows re-keyed to ``season:fixture_id``.

    Returns ``(odds, unmatched)``. Rows already carrying a numeric key pass
    through untouched.
    """
    snap = warehouse.snapshot_at(as_of)
    fixtures = snap.table("fact_fixture", where="season = ?", params=[season])
    teams = snap.table("dim_team", where="season = ?", params=[season])
    if fixtures.empty or teams.empty:
        return pd.DataFrame(), pd.DataFrame()

    name_by_code = dict(zip(teams["team_code"], teams["name"]))
    lookup: dict[tuple[str, str, str], int] = {}
    for _, fixture in fixtures.iterrows():
        kickoff = fixture["kickoff_utc"]
        day = kickoff.date().isoformat() if pd.notna(kickoff) else "unknown"
        lookup[(day,
                _slugify(name_by_code.get(fixture["home_team_code"], "")),
                _slugify(name_by_code.get(fixture["away_team_code"], "")))] = \
            int(fixture["fixture_id"])

    # Two alias tables that point in OPPOSITE directions. football-data's maps
    # its own short name to the long form ("Brighton" -> "Brighton & Hove
    # Albion"); The Odds API's maps the long form to FPL's short one
    # ("Brighton and Hove Albion" -> "Brighton"). Applying either one
    # unconditionally breaks the matches that already worked: run blind over
    # 2025-26 it rewrites "brighton" to "brighton-hove-albion", which matches
    # nothing, and 248 of 380 fixtures fail to resolve while looking like a
    # name-coverage problem. So aliases are CANDIDATES, tried after the raw slug.
    variants: dict[str, set[str]] = {}
    for table in (FD_TEAM_ALIASES, ODDS_API_TEAM_ALIASES,
                  SUPPLEMENTARY_TEAM_ALIASES):
        for raw, mapped in table.items():
            a, b = _slugify(raw), _slugify(mapped)
            variants.setdefault(a, set()).add(b)
            variants.setdefault(b, set()).add(a)

    def _candidates(slug: str) -> list[str]:
        return [slug, *sorted(variants.get(slug, set()))]

    odds = warehouse.sql(
        "SELECT * FROM fact_odds WHERE as_of <= ? AND fixture_key LIKE ?",
        [as_of, f"{season}:%"],
    )
    if odds.empty:
        return odds, pd.DataFrame()

    resolved: list[str | None] = []
    for key in odds["fixture_key"]:
        parts = str(key).split(":")
        if len(parts) == 2:
            resolved.append(str(key))
            continue
        if len(parts) != 4:
            resolved.append(None)
            continue
        _, day, home, away = parts
        fid = None
        for h in _candidates(home):
            for a in _candidates(away):
                fid = lookup.get((day, h, a))
                if fid is not None:
                    break
            if fid is not None:
                break
        resolved.append(f"{season}:{fid}" if fid is not None else None)

    odds = odds.assign(matched_key=resolved)
    unmatched = odds[odds["matched_key"].isna()][["fixture_key"]].drop_duplicates()
    matched = odds[odds["matched_key"].notna()].copy()
    matched["fixture_key"] = matched["matched_key"]
    return matched.drop(columns=["matched_key"]), unmatched.reset_index(drop=True)


def _to_frame(provider: str, season: str, gw: int, codes: np.ndarray,
              xp: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame({
        "provider": provider, "season": season, "gw": int(gw),
        "code": np.asarray(codes, dtype=int), "xp": np.asarray(xp, dtype=float),
    })


def simulate_source(
    provider: str,
    model: DecomposedPointsModel,
    snapshot: Snapshot,
    season: str,
    gw: int,
    *,
    n_sims: int = 1500,
    seed: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run a points simulator and return ``(frame, samples)``.

    ``samples`` is a frame indexed by ``code`` holding the raw draws, kept so
    the scoring step can compute a true CRPS and event Briers rather than
    collapsing a distribution to its mean and then pretending to score it.
    """
    sample = model.simulate(snapshot, season, gw, n_sims=n_sims, seed=seed)
    frame = _to_frame(provider, season, gw, sample.codes, sample.mean())
    draws = pd.DataFrame(sample.points, index=pd.Index(sample.codes, name="code"))
    return frame, draws


def market_goal_model(
    warehouse: Warehouse, season: str, as_of: dt.datetime, *, rho: float = 0.0,
    devig: str = "power",
) -> tuple[MarketImpliedModel, pd.DataFrame]:
    """A :class:`MarketImpliedModel` wired to re-keyed real bookmaker odds."""
    odds, unmatched = odds_with_fixture_keys(warehouse, season, as_of)
    if odds.empty:
        raise NoOddsCoverageError(f"no {season} odds visible at {as_of:%Y-%m-%d %H:%MZ}")
    return MarketImpliedModel(FrameOddsProvider(odds, method=devig), rho=rho), unmatched


def ppg_source(snapshot: Snapshot, season: str, gw: int, *,
               history: list[str], min_apps: int = 3) -> pd.DataFrame:
    """The no-model baseline: points per appearance x empirical appearance rate.

    Read through ``snapshot.results_before``, so it can only average results the
    manager could have seen. A player with fewer than ``min_apps`` appearances
    is shrunk toward the positional mean rather than trusted at face value --
    otherwise one 13-point cameo makes a bench player the best pick in the game
    and the baseline stops being a baseline.
    """
    parts = []
    for hist_season in history:
        rows = snapshot.results_before(hist_season)
        if not rows.empty:
            parts.append(rows[["code", "minutes", "total_points", "gw"]])
    if not parts:
        raise ValueError("no visible history for the ppg baseline")
    past = pd.concat(parts, ignore_index=True)

    players = snapshot.selectable(season)
    appeared = past[past["minutes"] > 0]
    per_player = appeared.groupby("code").agg(
        pts=("total_points", "mean"), apps=("total_points", "size")
    )
    slots = past.groupby("code").size().rename("slots")
    rate = (per_player["apps"] / slots).clip(0, 1).rename("p_appear")

    joined = players.set_index("code").join([per_player, rate])
    pos_mean = joined.groupby("position")["pts"].transform("mean")
    apps = joined["apps"].fillna(0.0)
    # Shrinkage weight: n / (n + min_apps). Never 1, so even a heavy starter
    # keeps a little of the positional prior.
    w = apps / (apps + float(min_apps))
    pts = w * joined["pts"].fillna(pos_mean) + (1 - w) * pos_mean
    p_appear = joined["p_appear"].fillna(0.0)

    out = _to_frame("ppg", season, gw, joined.index.to_numpy(),
                    (pts * p_appear).to_numpy())
    out["p_appear"] = p_appear.to_numpy()
    out["xp_if_appears"] = pts.to_numpy()
    return out.dropna(subset=["xp"]).reset_index(drop=True)


def warehouse_source(store, provider: str, season: str, gw: int,
                     as_of: dt.datetime) -> pd.DataFrame:
    """A third-party projection read out of ``fact_projection``, point-in-time."""
    rows = store.as_of(
        "fact_projection", as_of,
        where="provider = ? AND season = ? AND gw = ?", params=[provider, season, gw],
    )
    if rows.empty:
        return pd.DataFrame(columns=["provider", "season", "gw", "code", "xp"])
    keep = ["provider", "season", "gw", "code", "xp", "xp_if_appears", "p_appear"]
    return rows[[c for c in keep if c in rows.columns]].reset_index(drop=True)


class KickoffOddsProvider:
    """Odds pinned at kickoff rather than at the deadline. **Not fair.**

    Every historical bookmaker row we hold is a *closing* line, and
    ``fact_odds`` correctly stamps it at its fixture's kickoff instant -- the
    last moment it was observable. An FPL deadline is at least 90 minutes
    earlier, so at a deadline snapshot the entire historical odds archive is
    invisible, and a deadline-honest backtest of a market source over 2025-26
    scores exactly zero fixtures.

    That is not a bug in the warehouse; it is the warehouse being right. You
    could not have bet the close when you picked your team.

    This class exists so the market can still be *bounded*, not so it can be
    scored fairly. It hands the market model a 90-minute information advantage
    -- team news, confirmed lineups, late money -- that no manager had. Read any
    number produced through it as an upper bound on the market's usable skill.
    The name is deliberately ugly so it cannot appear in a decision path by
    accident.
    """

    def __init__(self, odds: pd.DataFrame, *, method: str = "power") -> None:
        self._inner = FrameOddsProvider(odds, method=method)

    def odds_for(self, fixture_keys: list[str], as_of: dt.datetime) -> dict:
        del as_of  # deliberately ignored; see the class docstring
        return self._inner.odds_for(fixture_keys, dt.datetime.max.replace(
            tzinfo=dt.timezone.utc))


def market_goal_model_at_kickoff(
    warehouse: Warehouse, season: str, as_of: dt.datetime, *,
    rho: float = 0.0, devig: str = "power",
) -> tuple[MarketImpliedModel, pd.DataFrame]:
    """Market model over closing lines. See :class:`KickoffOddsProvider`."""
    odds, unmatched = odds_with_fixture_keys(
        warehouse, season, dt.datetime.now(dt.timezone.utc)
    )
    if odds.empty:
        raise NoOddsCoverageError(f"no {season} odds at all")
    del as_of
    return MarketImpliedModel(KickoffOddsProvider(odds, method=devig), rho=rho), unmatched


def form_source(snapshot: Snapshot, season: str, gw: int, *,
                window: int = 6, prior_apps: float = 2.0) -> pd.DataFrame:
    """Recent form: mean points per appearance over the last ``window`` gameweeks.

    Independent of :func:`ppg_source` in the way that matters -- it weights the
    last six gameweeks and nothing else, so it tracks a player who has just won
    a starting place and ignores one who was good two seasons ago. Shrunk toward
    the same-position mean by ``prior_apps`` pseudo-appearances, because over a
    six-gameweek window a single haul is otherwise decisive.
    """
    rows = snapshot.results_before(season)
    if rows.empty:
        raise ValueError(f"no visible {season} results for the form baseline")
    recent = rows[rows["gw"] >= max(1, gw - window)]
    if recent.empty:
        recent = rows

    played = recent[recent["minutes"] > 0]
    agg = played.groupby("code").agg(pts=("total_points", "mean"),
                                     apps=("total_points", "size"))
    slots = recent.groupby("code").size().rename("slots")
    rate = (agg["apps"] / slots).clip(0, 1).rename("p_appear")

    players = snapshot.selectable(season).set_index("code")
    joined = players.join([agg, rate])
    pos_mean = joined.groupby("position")["pts"].transform("mean")
    apps = joined["apps"].fillna(0.0)
    w = apps / (apps + prior_apps)
    pts = w * joined["pts"].fillna(pos_mean) + (1 - w) * pos_mean
    p_appear = joined["p_appear"].fillna(0.0)

    out = _to_frame("form", season, gw, joined.index.to_numpy(),
                    (pts * p_appear).to_numpy())
    out["p_appear"] = p_appear.to_numpy()
    out["xp_if_appears"] = pts.to_numpy()
    return out.dropna(subset=["xp"]).reset_index(drop=True)


#: Points an FPL goal is worth by position (1=GKP .. 4=FWD), from the verified
#: rule registry's ``scoring.goal``.
_GOAL_POINTS = {1: 10, 2: 6, 3: 5, 4: 4}
_ASSIST_POINTS = 3


def xstat_source(snapshot: Snapshot, season: str, gw: int, *,
                 history: list[str], prior_minutes: float = 900.0) -> pd.DataFrame:
    """Opta xG/xA per 90, converted to points. A genuinely different input.

    ``ppg`` and ``form`` both average *realised points*, so they inherit the
    same finishing luck. This one reads ``expected_goals`` and
    ``expected_assists`` -- Opta's model, shipped free in the FPL API and
    already in ``fact_player_fixture`` -- and so disagrees with them precisely
    where a player has been over- or under-finishing. That is the disagreement
    an ensemble is supposed to exploit.

    Deliberately partial: it prices attacking returns and appearance points and
    nothing else. No clean sheets, no bonus, no defensive contribution. It will
    therefore be biased low, especially for defenders, and the stacking fitter
    is expected to notice.
    """
    parts = []
    for hist_season in [*history, season]:
        rows = snapshot.results_before(hist_season)
        if not rows.empty:
            parts.append(rows[["code", "minutes", "expected_goals",
                               "expected_assists", "total_points"]])
    if not parts:
        raise ValueError("no visible history for the xstat source")
    past = pd.concat(parts, ignore_index=True)

    agg = past.groupby("code").agg(
        mins=("minutes", "sum"), xg=("expected_goals", "sum"),
        xa=("expected_assists", "sum"), slots=("minutes", "size"),
        apps=("minutes", lambda s: int((s > 0).sum())),
    )
    players = snapshot.selectable(season).set_index("code")
    joined = players.join(agg)
    mins = joined["mins"].fillna(0.0)
    # Shrink per-90 rates toward zero by prior_minutes of nothing. A player with
    # 40 career minutes and one xG is not a 2.2 xG/90 striker.
    denom = mins + prior_minutes
    xg90 = 90.0 * joined["xg"].fillna(0.0) / denom
    xa90 = 90.0 * joined["xa"].fillna(0.0) / denom

    goal_pts = joined["position"].map(_GOAL_POINTS).astype(float)
    exp_minutes = (mins / joined["slots"].replace(0, np.nan)).fillna(0.0).clip(0, 90)
    p_appear = (joined["apps"] / joined["slots"].replace(0, np.nan)).fillna(0.0).clip(0, 1)

    per90 = xg90 * goal_pts + xa90 * _ASSIST_POINTS
    appearance_pts = np.where(exp_minutes >= 60, 2.0, np.where(exp_minutes > 0, 1.0, 0.0))
    xp = p_appear * (per90 * exp_minutes / 90.0 + appearance_pts)

    out = _to_frame("xstat", season, gw, joined.index.to_numpy(), np.asarray(xp))
    out["p_appear"] = p_appear.to_numpy()
    return out.dropna(subset=["xp"]).reset_index(drop=True)
