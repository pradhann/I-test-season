"""Feature engineering for the minutes model.

Everything in this module reads from a :class:`~fpl_edge.store.Snapshot` and
nothing else. There is no path from here to the warehouse, to a file, or to a
"current" frame passed in by a caller, which is what makes the leakage property
checkable by inspection: if the snapshot cannot see it, the model cannot use it.

The feature frame is built for a *target* (player, fixture) grid and is the same
frame at training time and at prediction time. Training rows come from snapshots
taken at historical deadlines; prediction rows come from a snapshot at the
upcoming deadline. Nothing about a row's construction tells the model which it
is, which is the point.

Two facts about FPL minutes shape the design:

* The opportunity denominator is *team fixtures*, not player appearances. A
  player who has not featured for six weeks has six pieces of evidence, not
  zero, and a model that only reads rows present in ``fact_player_fixture``
  throws that evidence away. The grid is therefore expanded to every squad
  player of every team that played.
* Availability is published in three partly-redundant channels (``status``,
  ``chance_of_playing_next_round``, free-text ``news``) with different lags and
  different reliability. All three are exposed and the models learn what to do
  with them rather than trusting any one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from itertools import pairwise

import numpy as np
import pandas as pd

from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, MinutesBucket, Season

#: Typical starting slots per FPL position, used to turn a within-club depth
#: rank into "is this player inside the XI on merit".
SLOTS: dict[int, int] = {1: 1, 2: 4, 3: 4, 4: 2}

#: Identifier columns carried alongside the features.
ID_COLUMNS: tuple[str, ...] = ("season", "code", "fixture_id", "gw", "team_code", "position")

#: Every engineered feature. Order is fixed: the GBM's design matrix is
#: ``frame[list(FEATURE_COLUMNS)].to_numpy()`` and a reordering would silently
#: change the model.
FEATURE_COLUMNS: tuple[str, ...] = (
    # --- current-season form (all NaN at a cold start) -------------------
    "n_obs_season",
    "full_rate_season", "cameo_rate_season", "unavail_rate_season",
    "start_rate_season", "mean_min_season",
    "full_rate_5", "cameo_rate_5", "unavail_rate_5", "start_rate_5", "mean_min_5",
    "start_rate_3", "mean_min_3", "minutes_trend",
    "sub_off_rate", "sub_on_rate",
    "team_fixtures_since_start",
    "days_since_last_appearance",
    # --- prior-season carryover -----------------------------------------
    "prev_n_obs", "prev_full_rate", "prev_cameo_rate", "prev_unavail_rate",
    "prev_start_rate", "prev_mean_min",
    "is_new_signing", "is_unseen",
    # --- club depth and manager tendency --------------------------------
    "depth_rank", "depth_surplus", "squad_size_pos",
    "team_rotation_index", "team_subs_per_game",
    # --- fixture congestion ---------------------------------------------
    "days_rest", "is_season_opener", "is_midweek", "team_fixtures_next_14d",
    "euro_club", "euro_congestion",
    # --- published availability -----------------------------------------
    "status_known", "status_flagged", "status_injured", "status_doubtful",
    "status_suspended", "chance_next", "has_chance",
    "news_len", "news_injury", "news_suspension", "news_doubt", "news_return",
    # --- market and meta -------------------------------------------------
    "price_tenths", "selected_by_pct", "transfers_net_frac",
    "position", "is_gk", "is_home", "gw_idx", "is_cold_start",
)

#: The subset that is defined before a ball has been kicked in the season.
#: The cold-start code path trains and predicts on exactly these; a feature not
#: on this list is structurally unavailable at the GW1 deadline, not merely
#: missing, and imputing it would invent evidence.
COLD_FEATURE_COLUMNS: tuple[str, ...] = (
    "prev_n_obs", "prev_full_rate", "prev_cameo_rate", "prev_unavail_rate",
    "prev_start_rate", "prev_mean_min",
    "is_new_signing", "is_unseen",
    "depth_rank", "depth_surplus", "squad_size_pos",
    "team_rotation_index", "team_subs_per_game",
    "is_season_opener", "is_midweek", "team_fixtures_next_14d", "euro_club",
    "status_known", "status_flagged", "status_injured", "status_doubtful",
    "status_suspended", "chance_next", "has_chance",
    "news_len", "news_injury", "news_suspension", "news_doubt", "news_return",
    "price_tenths", "selected_by_pct", "transfers_net_frac",
    "position", "is_gk", "is_home", "gw_idx",
)

_INJURY_RE = re.compile(
    r"knock|injur|strain|hamstring|knee|ankle|calf|groin|thigh|muscl|surger|fitness|ill",
    re.IGNORECASE,
)
_SUSPENSION_RE = re.compile(r"suspend|ban\b|red card|accumulat", re.IGNORECASE)
_DOUBT_RE = re.compile(r"doubt|assess|chance of playing|late test", re.IGNORECASE)
_RETURN_RE = re.compile(r"expected back|returns|available", re.IGNORECASE)


def bucket_of_minutes(minutes: float | None) -> MinutesBucket:
    """Map realised minutes to the FPL-relevant bucket."""
    if minutes is None or (isinstance(minutes, float) and np.isnan(minutes)):
        return MinutesBucket.UNAVAILABLE
    m = int(minutes)
    if m <= 0:
        return MinutesBucket.UNAVAILABLE
    if m < 60:
        return MinutesBucket.CAMEO
    return MinutesBucket.FULL


def buckets_of_minutes(minutes: pd.Series) -> pd.Series:
    m = pd.to_numeric(minutes, errors="coerce").fillna(0)
    return pd.Series(
        np.where(m <= 0, 0, np.where(m < 60, 1, 2)), index=minutes.index, dtype="int64"
    )


def _prev_season(season: str, known: Iterable[str]) -> str | None:
    earlier = sorted(s for s in set(known) if s < season)
    return earlier[-1] if earlier else None


class SnapshotView:
    """The four tables a minutes model is allowed to see, read once.

    Held as a class only to avoid re-querying DuckDB once per gameweek during a
    walk-forward backtest; it is a cache of one Snapshot's reads, never a way
    around it.
    """

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self.as_of = snapshot.as_of
        self.results = snapshot.table("fact_player_fixture")
        self.fixtures = snapshot.table("fact_fixture")
        self.dim_player = snapshot.table("dim_player")
        self._players: dict[str, pd.DataFrame] = {}

    def players(self, season: str) -> pd.DataFrame:
        if season not in self._players:
            self._players[season] = self.snapshot.players(season)
        return self._players[season]

    # -- derived ---------------------------------------------------------

    def team_fixture_rows(self) -> pd.DataFrame:
        """One row per (fixture, team) for every scheduled fixture."""
        fx = self.fixtures
        if fx.empty:
            return pd.DataFrame(
                columns=["season", "fixture_id", "gw", "kickoff_utc", "team_code",
                         "opponent_code", "is_home"]
            )
        home = fx.rename(columns={"home_team_code": "team_code", "away_team_code": "opponent_code"})
        home["is_home"] = 1
        away = fx.rename(columns={"away_team_code": "team_code", "home_team_code": "opponent_code"})
        away["is_home"] = 0
        cols = ["season", "fixture_id", "gw", "kickoff_utc", "team_code", "opponent_code", "is_home"]
        return pd.concat([home[cols], away[cols]], ignore_index=True)

    def opportunity_grid(self) -> pd.DataFrame:
        """Every (squad player, played team fixture) pair with realised minutes.

        A fixture counts as played once *any* result row for it is visible; a
        squad player with no row for a played fixture did not feature, which is
        an observation of zero minutes rather than a missing value.
        """
        res = self.results
        if res.empty:
            return pd.DataFrame(
                columns=["season", "code", "fixture_id", "gw", "kickoff_utc", "team_code",
                         "position", "minutes", "starts", "bucket"]
            )
        played = res[["season", "fixture_id"]].drop_duplicates()
        tf = self.team_fixture_rows().merge(played, on=["season", "fixture_id"], how="inner")
        dp = self.dim_player[["season", "code", "team_code", "position"]]
        grid = tf.merge(dp, on=["season", "team_code"], how="inner")
        grid = grid.merge(
            res[["season", "code", "fixture_id", "minutes", "starts"]],
            on=["season", "code", "fixture_id"],
            how="left",
        )
        grid["minutes"] = pd.to_numeric(grid["minutes"], errors="coerce").fillna(0.0)
        grid["starts"] = pd.to_numeric(grid["starts"], errors="coerce")
        grid["starts"] = grid["starts"].fillna((grid["minutes"] >= 60).astype(float))
        grid["bucket"] = buckets_of_minutes(grid["minutes"])
        return grid.sort_values(["season", "code", "kickoff_utc"]).reset_index(drop=True)


# --------------------------------------------------------------------------
# per-player aggregates
# --------------------------------------------------------------------------


def _rate_block(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
    g = df.groupby("code")
    out = pd.DataFrame(
        {
            f"full_rate_{suffix}": g["bucket"].apply(lambda s: (s == 2).mean()),
            f"cameo_rate_{suffix}": g["bucket"].apply(lambda s: (s == 1).mean()),
            f"unavail_rate_{suffix}": g["bucket"].apply(lambda s: (s == 0).mean()),
            f"start_rate_{suffix}": g["starts"].mean(),
            f"mean_min_{suffix}": g["minutes"].mean(),
        }
    )
    return out


def _season_player_features(cur: pd.DataFrame, target_ko: pd.Series | None) -> pd.DataFrame:
    """Form features from the current season's visible history."""
    if cur.empty:
        return pd.DataFrame(
            columns=[
                "n_obs_season", "full_rate_season", "cameo_rate_season", "unavail_rate_season",
                "start_rate_season", "mean_min_season", "full_rate_5", "cameo_rate_5",
                "unavail_rate_5", "start_rate_5", "mean_min_5", "start_rate_3", "mean_min_3",
                "minutes_trend", "sub_off_rate", "sub_on_rate", "team_fixtures_since_start",
                "last_appearance_ko",
            ]
        )
    parts = [
        _rate_block(cur, "season"),
        _rate_block(cur.groupby("code").tail(5), "5"),
    ]
    tail3 = cur.groupby("code").tail(3).groupby("code")
    tail10 = cur.groupby("code").tail(10).groupby("code")
    extra = pd.DataFrame(
        {
            "n_obs_season": cur.groupby("code").size(),
            "start_rate_3": tail3["starts"].mean(),
            "mean_min_3": tail3["minutes"].mean(),
            "_mean_min_10": tail10["minutes"].mean(),
        }
    )
    started = cur[cur["starts"] >= 0.5]
    benched = cur[cur["starts"] < 0.5]
    beh = pd.DataFrame(
        {
            "sub_off_rate": started.groupby("code")["minutes"].apply(lambda s: (s < 60).mean()),
            "sub_on_rate": benched.groupby("code")["minutes"].apply(lambda s: (s > 0).mean()),
        }
    )
    # how many team fixtures ago was the player's last start
    cur = cur.copy()
    cur["_idx"] = cur.groupby("code").cumcount()
    last_idx = cur.groupby("code")["_idx"].max()
    last_start = cur[cur["starts"] >= 0.5].groupby("code")["_idx"].max()
    since_start = (last_idx - last_start).reindex(last_idx.index)
    since_start = since_start.fillna(last_idx + 1)
    appeared = cur[cur["minutes"] > 0]
    last_app = appeared.groupby("code")["kickoff_utc"].max()

    out = pd.concat(parts + [extra, beh], axis=1)
    out["team_fixtures_since_start"] = since_start
    out["minutes_trend"] = out["mean_min_3"] - out["_mean_min_10"]
    out["last_appearance_ko"] = last_app
    return out.drop(columns=["_mean_min_10"])


def _team_style(grid: pd.DataFrame) -> pd.DataFrame:
    """Manager tendency: how much the XI churns, and how freely subs are used."""
    if grid.empty:
        return pd.DataFrame(columns=["season", "team_code", "team_rotation_index",
                                     "team_subs_per_game"])
    rows = []
    for (season, team), g in grid.groupby(["season", "team_code"], sort=False):
        per_fx = g.sort_values("kickoff_utc").groupby("fixture_id", sort=False)
        xis, subs = [], []
        for _, fx in per_fx:
            xis.append(frozenset(fx.loc[fx["starts"] >= 0.5, "code"]))
            subs.append(int(((fx["starts"] < 0.5) & (fx["minutes"] > 0)).sum()))
        churn = [
            len(a ^ b) / max(len(a | b), 1) for a, b in pairwise(xis)
        ]
        rows.append(
            {
                "season": season,
                "team_code": team,
                "team_rotation_index": float(np.mean(churn)) if churn else np.nan,
                "team_subs_per_game": float(np.mean(subs)) if subs else np.nan,
            }
        )
    return pd.DataFrame(rows)


def _league_rank(grid_fixtures: pd.DataFrame, season: str) -> dict[int, int]:
    """Prior-season league position, used as the "plays in Europe" proxy.

    European and domestic-cup fixtures are not in the warehouse, so midweek
    congestion cannot be observed directly. Finishing position is public,
    strictly historical, and predicts which clubs carry a Thursday/Wednesday
    fixture load - which is what actually drives rotation.
    """
    fx = grid_fixtures[
        (grid_fixtures["season"] == season) & grid_fixtures["finished"].fillna(False).astype(bool)
    ]
    if fx.empty:
        return {}
    pts: dict[int, int] = {}
    for row in fx.itertuples():
        h, a = int(row.home_team_code), int(row.away_team_code)
        hs, as_ = row.home_score, row.away_score
        if pd.isna(hs) or pd.isna(as_):
            continue
        pts.setdefault(h, 0)
        pts.setdefault(a, 0)
        if hs > as_:
            pts[h] += 3
        elif hs < as_:
            pts[a] += 3
        else:
            pts[h] += 1
            pts[a] += 1
    order = sorted(pts, key=lambda t: -pts[t])
    return {t: i + 1 for i, t in enumerate(order)}


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------


def build_feature_frame(
    snapshot: Snapshot | SnapshotView,
    season: Season,
    gws: list[GwId],
    *,
    view: SnapshotView | None = None,
) -> pd.DataFrame:
    """One row per (squad player, fixture) for ``gws``, with all features.

    Rows are produced for every player in the club playing the fixture, because
    "did not make the squad" is a prediction the model must be able to make.
    """
    if isinstance(snapshot, SnapshotView):
        view = snapshot
    elif view is None:
        view = SnapshotView(snapshot)

    players = view.players(season)
    tf = view.team_fixture_rows()
    tf = tf[(tf["season"] == season) & (tf["gw"].isin(list(gws)))]
    if players.empty or tf.empty:
        return pd.DataFrame(columns=[*ID_COLUMNS, *FEATURE_COLUMNS])

    base = tf.merge(
        players[
            ["code", "team_code", "position", "price_tenths", "selected_by_pct", "status",
             "chance_of_playing_next_round", "news", "transfers_in_event", "transfers_out_event"]
        ],
        on="team_code",
        how="inner",
    )
    base = base.rename(columns={"kickoff_utc": "target_ko"})

    grid = view.opportunity_grid()
    cur = grid[grid["season"] == season] if not grid.empty else grid
    prev_season = _prev_season(season, grid["season"].unique() if not grid.empty else [])

    form = _season_player_features(cur, None)
    out = base.merge(form, left_on="code", right_index=True, how="left")
    out["n_obs_season"] = out["n_obs_season"].fillna(0.0)

    # ---- prior season ---------------------------------------------------
    if prev_season is not None:
        prv = grid[grid["season"] == prev_season]
        prev_block = _rate_block(prv, "prev_tmp")
        prev_block.columns = ["prev_full_rate", "prev_cameo_rate", "prev_unavail_rate",
                              "prev_start_rate", "prev_mean_min"]
        prev_block["prev_n_obs"] = prv.groupby("code").size()
        out = out.merge(prev_block, left_on="code", right_index=True, how="left")
        prev_team = (
            view.dim_player[view.dim_player["season"] == prev_season]
            .set_index("code")["team_code"]
        )
        out["_prev_team"] = out["code"].map(prev_team)
    else:
        for c in ("prev_full_rate", "prev_cameo_rate", "prev_unavail_rate", "prev_start_rate",
                  "prev_mean_min", "prev_n_obs"):
            out[c] = np.nan
        out["_prev_team"] = np.nan
    out["prev_n_obs"] = out["prev_n_obs"].fillna(0.0)
    out["is_unseen"] = (out["prev_n_obs"] <= 0).astype(float)
    out["is_new_signing"] = np.where(
        out["_prev_team"].isna(), 1.0, (out["_prev_team"] != out["team_code"]).astype(float)
    )

    # ---- cold start -----------------------------------------------------
    team_played = (
        cur.groupby("team_code")["fixture_id"].nunique() if not cur.empty else pd.Series(dtype=int)
    )
    out["_team_played"] = out["team_code"].map(team_played).fillna(0.0)
    out["is_cold_start"] = (out["_team_played"] < 1).astype(float)

    # ---- club depth -----------------------------------------------------
    ref = np.where(
        out["n_obs_season"] >= 3, out["full_rate_season"], out["prev_full_rate"]
    )
    out["_ref_rate"] = pd.Series(ref, index=out.index).fillna(0.0)
    key = ["fixture_id", "team_code", "position"]
    out["depth_rank"] = out.groupby(key)["_ref_rate"].rank(ascending=False, method="first")
    out["squad_size_pos"] = out.groupby(key)["_ref_rate"].transform("size").astype(float)
    out["depth_surplus"] = out["depth_rank"] - out["position"].map(SLOTS).astype(float)

    style_season = season if not cur.empty else prev_season
    style = _team_style(grid[grid["season"] == style_season]) if style_season else pd.DataFrame()
    if style.empty:
        out["team_rotation_index"] = np.nan
        out["team_subs_per_game"] = np.nan
    else:
        out = out.merge(style.drop(columns=["season"]), on="team_code", how="left")

    # ---- congestion -----------------------------------------------------
    all_tf = view.team_fixture_rows()
    all_tf = all_tf[all_tf["season"] == season]
    prev_ko, next14 = [], []
    by_team = {
        int(t): np.sort(g["kickoff_utc"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy())
        for t, g in all_tf.groupby("team_code")
    }
    target_naive = out["target_ko"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy()
    window14 = np.timedelta64(14, "D")
    for team, ko in zip(out["team_code"].astype(int), target_naive):
        arr = by_team.get(int(team))
        if arr is None or len(arr) == 0:
            prev_ko.append(np.datetime64("NaT"))
            next14.append(np.nan)
            continue
        earlier = arr[arr < ko]
        prev_ko.append(earlier[-1] if len(earlier) else np.datetime64("NaT"))
        next14.append(float(((arr >= ko) & (arr <= ko + window14)).sum()))
    prev_ko = pd.Series(
        pd.to_datetime(np.array(prev_ko, dtype="datetime64[ns]"), utc=True), index=out.index
    )
    out["days_rest"] = (out["target_ko"] - prev_ko).dt.total_seconds() / 86400.0
    out["is_season_opener"] = prev_ko.isna().astype(float)
    out["team_fixtures_next_14d"] = next14
    out["is_midweek"] = out["target_ko"].dt.dayofweek.isin([1, 2, 3]).astype(float)
    out["days_since_last_appearance"] = (
        out["target_ko"] - pd.to_datetime(out.get("last_appearance_ko"), utc=True)
    ).dt.total_seconds() / 86400.0

    ranks = _league_rank(view.fixtures, prev_season) if prev_season else {}
    out["euro_club"] = out["team_code"].map(lambda t: float(ranks.get(int(t), 99) <= 5)) if ranks \
        else np.nan
    # days_rest is NaN for a club's first fixture of the season, which is not an
    # unknown - there is no previous league fixture, and the club is rested. Any
    # other NaN would mean an unknown schedule, which cannot happen: fixtures are
    # published in advance and read from the same snapshot.
    opener = out["is_season_opener"].to_numpy(dtype=float) > 0.5
    short_rest = np.where(opener, 0.0, np.where(out["days_rest"].to_numpy() <= 5.0, 1.0, 0.5))
    euro = out["euro_club"].to_numpy(dtype=float)
    out["euro_congestion"] = np.where(np.isnan(euro), np.nan, euro * short_rest)

    # ---- availability ---------------------------------------------------
    # An absent status is NOT "available". Reading a missing availability as fit
    # is the optimistic direction, and the optimistic direction is the one that
    # buys the injured player. Unknown propagates as NaN: the GBM branches on it
    # and the hierarchical model has its own gate bucket for it.
    status = out["status"].astype(object)
    status_known = pd.notna(status)
    def _flag(values: set[str]) -> np.ndarray:
        hit = np.array([v in values for v in status], dtype=float)
        return np.where(status_known, hit, np.nan)

    out["status_known"] = status_known.astype(float)
    out["status_flagged"] = np.where(
        status_known, 1.0 - np.array([v == "a" for v in status], dtype=float), np.nan
    )
    out["status_injured"] = _flag({"i", "u", "n"})
    out["status_doubtful"] = _flag({"d"})
    out["status_suspended"] = _flag({"s"})
    chance = pd.to_numeric(out["chance_of_playing_next_round"], errors="coerce")
    out["has_chance"] = chance.notna().astype(float)
    out["chance_next"] = chance
    # The API sends "" for "nothing to report", so an empty string is an
    # observation. A NULL is not, and stays NaN.
    news = out["news"].astype(object)
    news_known = pd.notna(news)
    text = pd.Series([v if isinstance(v, str) else "" for v in news], index=out.index)
    out["news_len"] = np.where(news_known, text.str.len().astype(float), np.nan)
    for col, rx in (
        ("news_injury", _INJURY_RE),
        ("news_suspension", _SUSPENSION_RE),
        ("news_doubt", _DOUBT_RE),
        ("news_return", _RETURN_RE),
    ):
        out[col] = np.where(news_known, text.str.contains(rx).astype(float), np.nan)

    tin = pd.to_numeric(out["transfers_in_event"], errors="coerce").fillna(0.0)
    tout = pd.to_numeric(out["transfers_out_event"], errors="coerce").fillna(0.0)
    out["transfers_net_frac"] = (tin - tout) / (tin + tout + 1.0)
    out["price_tenths"] = pd.to_numeric(out["price_tenths"], errors="coerce").astype(float)
    out["selected_by_pct"] = pd.to_numeric(out["selected_by_pct"], errors="coerce").astype(float)

    out["position"] = out["position"].astype(float)
    out["is_gk"] = (out["position"] == 1).astype(float)
    out["is_home"] = out["is_home"].astype(float)
    out["gw_idx"] = out["gw"].astype(float)
    out["season"] = season

    for col in FEATURE_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)

    keep: list[str] = []
    for col in (*ID_COLUMNS, "target_ko", *FEATURE_COLUMNS):
        if col not in keep:
            keep.append(col)
    return out[keep].sort_values(["gw", "fixture_id", "code"]).reset_index(drop=True)


def attach_labels(features: pd.DataFrame, label_snapshot: Snapshot, season: Season) -> pd.DataFrame:
    """Join realised buckets from a snapshot taken *after* the gameweek settled.

    Labels are the one thing legitimately read from the future: the training
    target. Feature rows are never rebuilt from this snapshot.
    """
    if features.empty:
        return features.assign(bucket=pd.Series(dtype="int64"))
    res = label_snapshot.table("fact_player_fixture", where="season = ?", params=[season])
    fx = label_snapshot.table("fact_fixture", where="season = ?", params=[season])
    settled = set(res["fixture_id"].astype(int)) if not res.empty else set()
    out = features[features["fixture_id"].astype(int).isin(settled)].copy()
    if out.empty:
        return out.assign(bucket=pd.Series(dtype="int64"))
    mins = res.set_index(["code", "fixture_id"])["minutes"]
    idx = pd.MultiIndex.from_arrays([out["code"].astype(int), out["fixture_id"].astype(int)])
    out["minutes"] = pd.to_numeric(pd.Series(mins.reindex(idx).to_numpy()), errors="coerce")
    out["minutes"] = out["minutes"].fillna(0.0).to_numpy()
    out["bucket"] = buckets_of_minutes(out["minutes"]).to_numpy()
    del fx
    return out.reset_index(drop=True)
