"""Snapshot-derived features: what was true about a player when the user spoke.

Everything here reads through a :class:`~fpl_edge.store.Snapshot`, which is the
codebase's rule for model input and is doubly important on this path. The user
messages at 22:40 on a Tuesday; the review runs in March. If the review
recomputed "his form when I said it" from today's tables it would use a form
figure that includes the very gameweeks the idea was predicting, and the bias
analysis would be measuring the future's influence on the past. So the features
are captured once, at submission, frozen into ``idea_context``, and never
recomputed.

The comparator sets are frozen for the same reason and one further one: a
comparator chosen after the fact is not a comparator. "He beat the median
captain" must mean the median of the players you would plausibly have captained
*instead*, decided before kickoff.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from fpl_edge.interfaces.ideas import (
    CAPTAIN_POOL_SIZE,
    PRICE_PEER_BAND_TENTHS,
    Comparator,
)
from fpl_edge.store import Snapshot

#: FPL community definition of a haul, and a round number rather than a fitted
#: one on purpose: the recency probe asks whether the user chases *salient*
#: recent scores, and 10 is the number that shows up in a highlights package.
HAUL_POINTS = 10

#: "Recently" for the recency probe, in finished gameweeks.
RECENT_HAUL_WINDOW = 2

#: Gameweeks of history behind the form figure. Three is what the FPL site shows
#: and therefore what the user is actually reacting to.
FORM_LOOKBACK_GWS = 3

#: Availability codes a comparator member may carry. NULL is included on
#: purpose: historical seasons ingested from archives have no `status` column at
#: all, and treating a missing injury flag as an injury emptied every comparator
#: set on 2025-26 -- which voided every idea rather than settling it.
SELECTABLE_STATUS = ("a", "d")


def _selectable(frame: pd.DataFrame) -> pd.DataFrame:
    if "status" not in frame.columns:
        return frame
    return frame[frame["status"].isin(SELECTABLE_STATUS) | frame["status"].isna()]


#: Minutes over the lookback below which a player is not a live alternative.
#: Without this the form-percentile null becomes "picks at random from 592
#: players including fourth-choice goalkeepers", which no human competes against
#: and which would manufacture a bias finding out of nothing.
PEER_MIN_MINUTES = 60


def player_universe(snapshot: Snapshot, season: str) -> pd.DataFrame:
    """The player list a name has to resolve against, at this instant.

    ``Snapshot.players`` returns web_name only, which is enough to price a squad
    but not to recognise a person: Virgil van Dijk's web_name is "Virgil", so a
    message saying "van dijk" matches nothing at all in it -- or worse, fuzzy-
    matches "van Ewijk", a £4.0m defender, and the idea is silently attributed to
    the wrong player. So the first and second names are joined back on from
    ``dim_player``, through the same Snapshot and therefore under the same
    point-in-time guarantee.

    Both reads go through Snapshot deliberately: resolving August's message
    against today's player list would let a January signing answer it.
    """
    players = snapshot.players(season)
    if players.empty:
        return players
    names = snapshot.table("dim_player", where="season = ?", params=[season])
    cols = [c for c in ("code", "first_name", "second_name") if c in names.columns]
    if len(cols) < 2:
        return players
    return players.merge(names[cols], on="code", how="left")


def club_team_code(snapshot: Snapshot, season: str, club_name: str) -> int | None:
    """Stable ``team_code`` for a club name, at this instant.

    Resolved per season through the Snapshot rather than hardcoded, because the
    per-season ``team_id`` is reassigned alphabetically every year -- Man Utd is
    id 14 in 2022-23 and id 16 in 2026-27. Only ``team_code`` (1 for Man Utd) is
    stable, and getting this wrong would attribute the club-affinity probe to
    whichever club happens to sort into that slot.
    """
    teams = snapshot.table("dim_team", where="season = ?", params=[season])
    if teams.empty:
        return None
    needle = club_name.strip().lower()
    for col in ("name", "short_name"):
        if col not in teams.columns:
            continue
        hit = teams[teams[col].astype(str).str.lower() == needle]
        if not hit.empty:
            return int(hit.iloc[0]["team_code"])
    hit = teams[teams["name"].astype(str).str.lower().str.contains(needle, regex=False)]
    return int(hit.iloc[0]["team_code"]) if len(hit) == 1 else None


def club_share(players: pd.DataFrame, team_code: int | None) -> tuple[float | None, int]:
    """Share of the selectable universe that plays for a club, and its size.

    The null for the club-affinity probe. Computed over the whole selectable
    squad list rather than the minutes-filtered pool used elsewhere, because this
    probe has to work in GW1 when nobody has minutes yet -- and because the choice
    the user faced really was "any of these 592 players".
    """
    if players.empty or team_code is None or "team_code" not in players.columns:
        return None, 0
    pool = _selectable(players)
    if pool.empty:
        return None, 0
    return float((pool["team_code"] == int(team_code)).mean()), int(len(pool))


def _finished_results(snapshot: Snapshot, season: str) -> pd.DataFrame:
    """Per-player per-fixture returns visible at this instant, with gw."""
    res = snapshot.results_before(season)
    if res.empty:
        return res
    keep = [
        c for c in ("code", "gw", "fixture_id", "minutes", "total_points", "was_home")
        if c in res.columns
    ]
    return res[keep].copy()


def player_history(snapshot: Snapshot, season: str) -> pd.DataFrame:
    """One row per player with the features the review and verdicts need.

    Columns: code, games, minutes_total, season_ppg, form_points_last3,
    minutes_last3, last_match_gw, last_match_points, last_match_was_home,
    gws_since_haul, hauled_recently, eligible.

    Empty (but correctly shaped) before any gameweek has finalised, which is the
    state on 2026-08-18. Callers must handle that rather than assume history
    exists; the first ideas of a season legitimately have no form context and
    saying so is more useful than imputing one.
    """
    cols = [
        "code", "games", "minutes_total", "season_ppg", "form_points_last3",
        "minutes_last3", "last_match_gw", "last_match_points",
        "last_match_was_home", "gws_since_haul", "hauled_recently", "eligible",
    ]
    res = _finished_results(snapshot, season)
    if res.empty:
        return pd.DataFrame(columns=cols)

    res = res.sort_values(["code", "gw", "fixture_id"])
    latest_gw = int(res["gw"].max())
    recent_gws = set(range(max(1, latest_gw - FORM_LOOKBACK_GWS + 1), latest_gw + 1))
    haul_gws = set(range(max(1, latest_gw - RECENT_HAUL_WINDOW + 1), latest_gw + 1))

    rows = []
    for code, grp in res.groupby("code", sort=False):
        pts = grp["total_points"].fillna(0).astype(float)
        mins = grp["minutes"].fillna(0).astype(int)
        recent = grp[grp["gw"].isin(recent_gws)]
        last = grp.iloc[-1]
        hauls = grp[pts.values >= HAUL_POINTS]
        last_haul_gw = int(hauls["gw"].max()) if not hauls.empty else None
        rows.append(
            {
                "code": int(code),
                "games": int((mins > 0).sum()),
                "minutes_total": int(mins.sum()),
                "season_ppg": float(pts.mean()) if len(pts) else 0.0,
                "form_points_last3": float(recent["total_points"].fillna(0).sum()),
                "minutes_last3": int(recent["minutes"].fillna(0).sum()),
                "last_match_gw": int(last["gw"]),
                "last_match_points": int(last["total_points"] or 0),
                "last_match_was_home": (
                    None if pd.isna(last.get("was_home")) else bool(last.get("was_home"))
                ),
                "gws_since_haul": (None if last_haul_gw is None else latest_gw - last_haul_gw),
                "hauled_recently": bool(last_haul_gw is not None and last_haul_gw in haul_gws),
            }
        )
    out = pd.DataFrame(rows, columns=[c for c in cols if c != "eligible"])
    out["eligible"] = out["minutes_last3"] >= PEER_MIN_MINUTES
    return out


def form_percentile(history: pd.DataFrame, code: int, position: int | None) -> float | None:
    """Where the subject's recent form sits among comparable, playing peers.

    Uniform on [0, 1] under the null "the user's attention is uncorrelated with
    recent scoring", which is exactly the null the form-chasing probe tests. The
    peer set is same-position and minutes-filtered so the null describes a choice
    a human could actually have made.
    """
    if history.empty or "form_points_last3" not in history.columns:
        return None
    peers = history[history["eligible"]]
    if position is not None and "position" in history.columns:
        peers = peers[peers["position"] == position]
    if len(peers) < 5:
        peers = history[history["eligible"]]
    if len(peers) < 5:
        return None
    row = history[history["code"] == int(code)]
    if row.empty:
        return None
    mine = float(row.iloc[0]["form_points_last3"])
    vals = peers["form_points_last3"].astype(float).to_numpy()
    # Mid-rank so that ties (very common: lots of players on 2 points) do not
    # systematically push every tied player to the top or bottom of the scale.
    below = float((vals < mine).sum())
    equal = float((vals == mine).sum())
    return float((below + 0.5 * equal) / len(vals))


def population_base_rates(history: pd.DataFrame) -> dict[str, float | int | None]:
    """Null hypotheses drawn from the same instant the user was looking at.

    ``pop_home_rate`` is not 0.5 in general. Mid-gameweek, or after a round with
    an uneven split of postponements, the fraction of players whose most recent
    match was at home drifts a long way from a half, and testing a home-bias
    claim against a hardcoded 0.5 would find "bias" in the fixture list.
    """
    eligible = history[history["eligible"]] if not history.empty else history
    if eligible.empty:
        return {
            "pop_home_rate": None, "pop_mean_gws_since_haul": None,
            "pop_recent_haul_rate": None, "pop_mean_form_pct": None, "pop_n": 0,
        }
    home = eligible["last_match_was_home"].dropna()
    since = eligible["gws_since_haul"].dropna()
    return {
        "pop_home_rate": float(home.mean()) if len(home) else None,
        "pop_mean_gws_since_haul": float(since.mean()) if len(since) else None,
        "pop_recent_haul_rate": float(eligible["hauled_recently"].astype(bool).mean()),
        "pop_mean_form_pct": 0.5,
        "pop_n": int(len(eligible)),
    }


def captain_pool(players: pd.DataFrame, *, size: int = CAPTAIN_POOL_SIZE) -> pd.DataFrame:
    """The players the field was plausibly captaining, at this instant.

    Ownership rank, outfield only, availability-filtered. This is a *proxy*: the
    quantity that belongs here is ``captaincy_share`` from
    :class:`~fpl_edge.models.contracts.OwnershipModel`, which is a different and
    much more concentrated distribution than raw ownership. The proxy is recorded
    in the comparator label so no report can quietly present it as the real
    thing, and :func:`comparator_set` swaps to the model the moment one is
    passed in.
    """
    pool = players.copy()
    if "position" in pool.columns:
        pool = pool[pool["position"] != 1]
    pool = _selectable(pool)
    if "selected_by_pct" not in pool.columns or pool.empty:
        return pool.head(size)
    if pool["selected_by_pct"].isna().all():
        # Archive seasons carry no ownership. Price is the only ranking left,
        # and saying so in the label beats silently returning an empty pool.
        if "price_tenths" in pool.columns:
            return pool.sort_values("price_tenths", ascending=False).head(size)
        return pool.head(size)
    return pool.sort_values("selected_by_pct", ascending=False).head(size)


#: A comparator set this small is not a median, it is a coin flip against one
#: other player. Below it, the price band widens.
MIN_PRICE_PEERS = 4

#: Widening steps in tenths of a million. The band starts at the class-defining
#: half-million and grows only as far as it must.
PRICE_BAND_STEPS: tuple[int, ...] = (PRICE_PEER_BAND_TENTHS, 10, 20, 40)


def price_peers(
    players: pd.DataFrame, subject: pd.Series, *, band_tenths: int = PRICE_PEER_BAND_TENTHS
) -> tuple[pd.DataFrame, int]:
    """Same position, similar price, excluding the subject.

    Returns ``(peers, band_used_tenths)``; a band of ``-1`` means the fallback
    below was used.

    "Similar price" rather than "similar expected points" on purpose: the
    counterfactual to buying a player is buying a different player you could
    afford in the same slot, so price and position define the choice set the user
    actually faced.

    The band widens when it has to. The £14.5m striker is the case that forces
    this: at a fixed +/-£0.5m he has no peers at all, the comparator resolves to
    an empty set, and the tracker has no choice but to void the idea -- so
    "I like Haaland" would be the one kind of idea the registry could never
    settle. Widening keeps the thesis falsifiable, and the band actually used is
    written into the comparator label so the claim stays precise about what it
    was measured against.
    """
    base = players
    if "position" in base.columns and "position" in subject:
        base = base[base["position"] == subject["position"]]
    base = _selectable(base)
    base = base[base["code"] != subject["code"]]
    if base.empty or "price_tenths" not in base.columns or "price_tenths" not in subject:
        return base, band_tenths

    mine = int(subject["price_tenths"])
    steps = tuple(b for b in PRICE_BAND_STEPS if b >= band_tenths) or (band_tenths,)
    for band in steps:
        peers = base[
            (base["price_tenths"] >= mine - band) & (base["price_tenths"] <= mine + band)
        ]
        if len(peers) >= MIN_PRICE_PEERS:
            return peers, band
    # Nothing in any band: fall back to the nearest-priced players in the
    # position. Always non-empty when the position has anyone else in it, which
    # is what stops a premium punt from being unfalsifiable by construction.
    nearest = base.assign(_gap=(base["price_tenths"].astype(int) - mine).abs())
    return nearest.nsmallest(min(MIN_PRICE_PEERS + 1, len(nearest)), "_gap").drop(columns="_gap"), -1


def comparator_set(
    players: pd.DataFrame,
    subject: pd.Series | None,
    comparator: Comparator,
    *,
    comparator_code: int | None = None,
    captaincy_share: pd.DataFrame | None = None,
) -> tuple[list[int], str]:
    """Resolve a comparator to a concrete, frozen set of player codes and a label.

    The label goes into the thesis sentence, so it must describe the set
    precisely enough that reading the idea back in April tells you what was
    actually being claimed.
    """
    if comparator is Comparator.NAMED_PLAYER:
        if comparator_code is None:
            raise ValueError("named_player comparator requires comparator_code")
        row = players[players["code"] == int(comparator_code)]
        name = str(row.iloc[0]["web_name"]) if not row.empty else f"player {comparator_code}"
        return [int(comparator_code)], name

    if comparator is Comparator.MEDIAN_CAPTAIN:
        if captaincy_share is not None and not captaincy_share.empty:
            pool = (
                captaincy_share.sort_values("captaincy_share", ascending=False)
                .head(CAPTAIN_POOL_SIZE)
            )
            codes = [int(c) for c in pool["code"]]
            return codes, f"the median of the {len(codes)} most-captained players"
        pool = captain_pool(players)
        codes = [int(c) for c in pool["code"]]
        return codes, f"the median of the {len(codes)} most-owned outfield players (captaincy proxy)"

    if comparator is Comparator.PRICE_PEER_MEDIAN:
        if subject is None:
            return [], "the median starting player"
        peers, band_used = price_peers(players, subject)
        codes = [int(c) for c in peers["code"]]
        pos = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}.get(int(subject.get("position", 0) or 0), "player")
        price = float(subject.get("price_tenths", 0) or 0) / 10.0
        if not codes:
            return [], f"the median other {pos}"
        if band_used < 0:
            return codes, (
                f"the median of the {len(codes)} {pos}s closest in price to £{price:.1f}m"
            )
        return codes, (
            f"the median of the {len(codes)} {pos}s priced within "
            f"£{band_used / 10:.1f}m of £{price:.1f}m"
        )

    codes = [int(c) for c in players["code"]]
    return codes, "the median player who took the field"


def realised_points(
    results: pd.DataFrame, codes: list[int], gws: range, *, started_only: bool = False
) -> pd.Series:
    """Total points per code over a gameweek window.

    Players with no row for a gameweek score zero for it, not NaN. That is the
    FPL-correct semantics -- an unused player returns nothing -- and treating it
    as missing would let a blank quietly drop out of a median.
    """
    if results.empty or not codes:
        return pd.Series(dtype=float)
    window = results[results["gw"].isin(list(gws)) & results["code"].isin(codes)]
    if started_only and "minutes" in window.columns:
        window = window[window["minutes"].fillna(0) > 0]
    totals = window.groupby("code")["total_points"].sum()
    return totals.reindex(codes).fillna(0.0).astype(float)


def gws_with_results(results: pd.DataFrame) -> set[int]:
    return set() if results.empty else {int(g) for g in results["gw"].dropna().unique()}


def utc(ts: dt.datetime) -> dt.datetime:
    return ts.astimezone(dt.timezone.utc)


def safe_int(value: object) -> int | None:
    """None for anything not a finite number, including NaN.

    pandas stores an absent integer as float NaN, and ``x is None`` is False for
    NaN, so the obvious guard silently lets NaN through to ``int()`` and raises.
    That failure mode cost twelve of eighteen context rows before it was caught:
    a player who has never hauled has ``gws_since_haul`` NaN, and every such idea
    was dropped from the bias analysis with no visible symptom.
    """
    f = safe_float(value)
    return None if f is None else int(f)


def safe_bool(value: object) -> bool | None:
    """None for NaN/None, bool otherwise. Same NaN trap as safe_int."""
    if value is None:
        return None
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            return None
    except (TypeError, ValueError):
        pass
    return bool(value)


def safe_float(value: object) -> float | None:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f
