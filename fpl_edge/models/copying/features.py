"""Strategy features: what a manager *did*, measured per season, never asserted.

The point of this module is to turn folk wisdom into columns. "Winners take
early hits", "the elite go differential in the run-in", "good managers wildcard
around the fixture swing" are all testable claims, and each becomes testable
only once it is a number attached to a manager and a season.

Data availability governs everything here, so it is stated up front
------------------------------------------------------------------
The public FPL API exposes **completed seasons as four fields**: season label,
total points, final rank, rounded percentile. It exposes **the current season**
in full: per-gameweek points, transfers, hits, squad value, bench points, plus
the actual squad at every past deadline and every transfer made.

That asymmetry is absolute and it is worth being blunt about, because it decides
what "winner archaeology" can and cannot mean. Verified against the live API on
2026-08-18::

    entry/4490171/event/38/picks/   ->  404 {"detail": "Not found."}
    entry/200/transfers/            ->  200 []
    entry/200/history/  ->  'past' has 13 seasons; every one of them has
                            exactly {season_name, total_points, rank, rank_percentage}

There is no endpoint, parameter or archive that returns a past season's squad,
transfers or chip usage for an entry. So reconstructing "what the 2019/20 winner
did in GW14" is not something a rate limit or a bigger budget can buy. Features
are therefore tiered:

``TIER_SEASON``
    Computable for every completed season, from rank and points alone.
``TIER_GAMEWEEK``
    Needs ``fact_manager_gw``: transfers, hits, squad value, bench points.
    Available for the season in progress, from GW1 onward.
``TIER_SQUAD``
    Needs ``fact_manager_pick``: template overlap, differential counts,
    captaincy, position spend, breakout onboarding. Available per gameweek from
    the moment that gameweek's deadline passes.

:func:`available_tiers` reports which of the three the warehouse can currently
support, and every computation function returns an empty frame with the right
columns rather than a partially-invented one when its inputs are absent. A
feature table with fabricated rows is worse than no feature table, because the
effect sizes computed from it look exactly as legitimate as real ones.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TIER_SEASON = "season"
TIER_GAMEWEEK = "gameweek"
TIER_SQUAD = "squad"

#: Ownership at or below which a pick counts as a differential. 5% is the
#: conventional line and is used because it is conventional -- the analysis
#: reports sensitivity at 2% and 10% rather than defending 5% as special.
DIFFERENTIAL_PCT = 5.0


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """One measurable strategy feature, and what it needs to exist."""

    name: str
    tier: str
    description: str
    #: Direction folk wisdom claims. Recorded so that a measured effect with the
    #: opposite sign is visibly a refutation rather than quietly reinterpreted.
    folk_claim: str


FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec("total_transfers", TIER_GAMEWEEK,
                "Transfers made across the season, excluding wildcard/free-hit weeks.",
                "elite managers make fewer, better-timed transfers"),
    FeatureSpec("total_hits", TIER_GAMEWEEK,
                "Points spent on extra transfers across the season.",
                "winners take more hits than the average manager, and earlier"),
    FeatureSpec("hit_weeks", TIER_GAMEWEEK,
                "Number of gameweeks in which any hit was taken.",
                "hits are concentrated in a few decisive weeks"),
    FeatureSpec("value_gain_tenths", TIER_GAMEWEEK,
                "Squad value at the last observed gameweek minus at the first.",
                "elite managers build value early and spend it late"),
    FeatureSpec("bench_points", TIER_GAMEWEEK,
                "Points left on the bench across the season.",
                "good managers leave fewer points on the bench"),
    FeatureSpec("rank_volatility", TIER_GAMEWEEK,
                "SD of week-on-week change in log overall rank.",
                "consistency beats spikiness"),
    FeatureSpec("template_overlap", TIER_SQUAD,
                "Mean share of the 15 most-selected players present in the squad.",
                "the elite are closer to the template than people think"),
    FeatureSpec("mean_squad_ownership", TIER_SQUAD,
                "Mean field ownership of the 15 players held, averaged over weeks.",
                "elite squads are more concentrated on high-ownership assets"),
    FeatureSpec("differential_count", TIER_SQUAD,
                f"Mean count of held players owned by <={DIFFERENTIAL_PCT}% of the field.",
                "winners carry two to four differentials at a time"),
    FeatureSpec("captain_deviation_rate", TIER_SQUAD,
                "Share of gameweeks captaining someone other than the field's most-captained.",
                "winners captain off-template more often"),
    FeatureSpec("bench_value_tenths", TIER_SQUAD,
                "Mean price of the four benched players.",
                "elite managers run a cheap bench and spend it on the XI"),
    FeatureSpec("spend_share_def", TIER_SQUAD, "Share of squad value in defenders.", "n/a"),
    FeatureSpec("spend_share_mid", TIER_SQUAD, "Share of squad value in midfielders.", "n/a"),
    FeatureSpec("spend_share_fwd", TIER_SQUAD, "Share of squad value in forwards.", "n/a"),
    FeatureSpec("breakout_capture", TIER_SQUAD,
                "Share of the season's breakout players' points captured while owned.",
                "the edge is being early on breakouts, not picking premiums"),
    FeatureSpec("chip_gw_wildcard1", TIER_GAMEWEEK, "Gameweek the first wildcard was played.",
                "hold the first wildcard past the early international break"),
    FeatureSpec("chip_gain_bboost", TIER_SQUAD,
                "Bench points gained in the Bench Boost week.", "n/a"),
    FeatureSpec("chip_gain_3xc", TIER_SQUAD,
                "Extra points from the Triple Captain over a normal captaincy.", "n/a"),
)


def available_tiers(
    seasons: pd.DataFrame | None,
    gws: pd.DataFrame | None,
    picks: pd.DataFrame | None,
) -> dict[str, bool]:
    """Which feature tiers the warehouse can currently support, and why not."""
    return {
        TIER_SEASON: seasons is not None and not seasons.empty,
        TIER_GAMEWEEK: gws is not None and not gws.empty,
        TIER_SQUAD: picks is not None and not picks.empty,
    }


# ---------------------------------------------------------------------------
# Tier: gameweek
# ---------------------------------------------------------------------------

GAMEWEEK_COLUMNS = [
    "entry_id", "season", "gws_observed", "total_transfers", "total_hits",
    "hit_weeks", "mean_transfers_per_gw", "value_start_tenths", "value_end_tenths",
    "value_gain_tenths", "bench_points", "rank_volatility", "final_rank",
]


def gameweek_features(gws: pd.DataFrame, chips: pd.DataFrame | None = None) -> pd.DataFrame:
    """Transfer, hit, value and volatility behaviour from the per-gameweek record.

    ``event_transfers`` counts transfers made in a wildcard or free-hit week too,
    where they are free and unbounded. Leaving them in makes any manager who
    wildcarded look hyperactive, so chip weeks are excluded from the transfer
    counts (but not from value or bench points, which are unaffected).
    """
    if gws is None or gws.empty:
        return pd.DataFrame(columns=GAMEWEEK_COLUMNS)

    df = gws.sort_values(["entry_id", "season", "gw"]).copy()
    chip_weeks: set[tuple[int, str, int]] = set()
    if chips is not None and not chips.empty:
        chip_weeks = {
            (int(r.entry_id), str(r.season), int(r.gw))
            for r in chips.itertuples()
            if r.chip in ("wildcard", "freehit")
        }
    df["_chipfree"] = [
        (int(e), str(s), int(g)) not in chip_weeks
        for e, s, g in zip(df["entry_id"], df["season"], df["gw"])
    ]

    rows = []
    for (eid, season), grp in df.groupby(["entry_id", "season"]):
        free = grp[grp["_chipfree"]]
        ranks = grp["overall_rank"].dropna()
        # Log-rank differences, because a move from 2,000,000 to 1,000,000 and a
        # move from 20,000 to 10,000 are the same achievement in the only unit
        # that matters, and the raw difference says otherwise by two orders of
        # magnitude.
        vol = float(np.diff(np.log(ranks[ranks > 0])).std(ddof=1)) if len(ranks) > 2 else np.nan
        rows.append({
            "entry_id": int(eid), "season": str(season), "gws_observed": int(len(grp)),
            "total_transfers": int(free["event_transfers"].fillna(0).sum()),
            "total_hits": int(grp["event_transfers_cost"].fillna(0).sum()),
            "hit_weeks": int((grp["event_transfers_cost"].fillna(0) > 0).sum()),
            "mean_transfers_per_gw": float(free["event_transfers"].fillna(0).mean())
            if len(free) else np.nan,
            "value_start_tenths": float(grp["value_tenths"].dropna().iloc[0])
            if grp["value_tenths"].notna().any() else np.nan,
            "value_end_tenths": float(grp["value_tenths"].dropna().iloc[-1])
            if grp["value_tenths"].notna().any() else np.nan,
            "value_gain_tenths": (
                float(grp["value_tenths"].dropna().iloc[-1] - grp["value_tenths"].dropna().iloc[0])
                if grp["value_tenths"].notna().sum() > 1 else np.nan
            ),
            "bench_points": int(grp["points_on_bench"].fillna(0).sum()),
            "rank_volatility": vol,
            "final_rank": float(ranks.iloc[-1]) if len(ranks) else np.nan,
        })
    return pd.DataFrame(rows, columns=GAMEWEEK_COLUMNS)


CHIP_COLUMNS = ["entry_id", "season", "chip", "gw", "half", "instance"]


def chip_timing(chips: pd.DataFrame) -> pd.DataFrame:
    """When each chip was played, tagged by season half and instance.

    The 2026-27 rules give **two of each** of wildcard, free hit, bench boost and
    triple captain, one per half, with wildcard and free hit locked out of GW1
    (``docs/rules.md``: ``chips.windows``, ``chips.count_each``). Earlier seasons
    did not work this way -- for several seasons there was a single bench boost
    and a single triple captain for the whole year, and the free hit did not
    exist at all before 2016/17 -- so a "chip played in GW8" from an old season
    and one from this season are not the same decision and must not be pooled.

    This function therefore tags each usage with the half it falls in under the
    *current* rules and leaves cross-season comparison to the caller, who has to
    decide explicitly whether the seasons are commensurable.
    """
    if chips is None or chips.empty:
        return pd.DataFrame(columns=CHIP_COLUMNS)
    df = chips.dropna(subset=["gw"]).copy()
    df["gw"] = df["gw"].astype(int)
    df["half"] = np.where(df["gw"] <= 19, 1, 2)
    df = df.sort_values(["entry_id", "season", "chip", "gw"])
    df["instance"] = df.groupby(["entry_id", "season", "chip"]).cumcount() + 1
    return df[CHIP_COLUMNS].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Tier: squad
# ---------------------------------------------------------------------------

SQUAD_COLUMNS = [
    "entry_id", "season", "gw", "template_overlap", "mean_squad_ownership",
    "differential_count", "differential_count_2pct", "differential_count_10pct",
    "captain_element", "captained_field_favourite", "bench_value_tenths",
    "squad_value_tenths", "spend_share_gkp", "spend_share_def",
    "spend_share_mid", "spend_share_fwd",
]


def squad_features(
    picks: pd.DataFrame,
    players: pd.DataFrame,
    *,
    most_captained: dict[int, int] | None = None,
    template_size: int = 15,
) -> pd.DataFrame:
    """Per manager, per gameweek: how template, how differential, how aggressive.

    ``players`` must be the player state **as it was at that gameweek's
    deadline** -- ownership after the fact is a different number, and using it
    would make every manager look like they bought the bandwagon a week early.
    It needs columns ``element_id``, ``gw``, ``selected_by_pct``,
    ``price_tenths`` and ``position``.
    """
    if picks is None or picks.empty or players is None or players.empty:
        return pd.DataFrame(columns=SQUAD_COLUMNS)

    need = {"element_id", "gw", "selected_by_pct", "price_tenths", "position"}
    missing = need - set(players.columns)
    if missing:
        raise KeyError(f"players frame is missing {sorted(missing)}")

    rows = []
    for gw, pgrp in players.groupby("gw"):
        own = dict(zip(pgrp["element_id"], pgrp["selected_by_pct"]))
        price = dict(zip(pgrp["element_id"], pgrp["price_tenths"]))
        pos = dict(zip(pgrp["element_id"], pgrp["position"]))
        template = set(
            pgrp.sort_values("selected_by_pct", ascending=False)
            .head(template_size)["element_id"]
        )
        favourite = (most_captained or {}).get(int(gw))

        week = picks[picks["gw"] == gw]
        for (eid, season), grp in week.groupby(["entry_id", "season"]):
            elements = list(grp["element_id"])
            owns = np.array([own.get(e, np.nan) for e in elements], dtype=float)
            prices = np.array([price.get(e, np.nan) for e in elements], dtype=float)
            positions = [pos.get(e) for e in elements]
            bench = grp[grp["slot"] > 11]["element_id"] if "slot" in grp else []
            captain = grp[grp["is_captain"].fillna(False)]["element_id"]
            cap = int(captain.iloc[0]) if len(captain) else None

            by_pos = {}
            total_price = float(np.nansum(prices))
            for label, code in (("gkp", 1), ("def", 2), ("mid", 3), ("fwd", 4)):
                sel = [p for p, q in zip(prices, positions) if q == code]
                by_pos[f"spend_share_{label}"] = (
                    float(np.nansum(sel)) / total_price if total_price > 0 else np.nan
                )

            rows.append({
                "entry_id": int(eid), "season": str(season), "gw": int(gw),
                "template_overlap": len(set(elements) & template) / float(template_size),
                "mean_squad_ownership": float(np.nanmean(owns)) if owns.size else np.nan,
                "differential_count": int(np.nansum(owns <= DIFFERENTIAL_PCT)),
                "differential_count_2pct": int(np.nansum(owns <= 2.0)),
                "differential_count_10pct": int(np.nansum(owns <= 10.0)),
                "captain_element": cap,
                "captained_field_favourite": (
                    None if favourite is None or cap is None else bool(cap == favourite)
                ),
                "bench_value_tenths": float(
                    np.nansum([price.get(e, np.nan) for e in bench])
                ) if len(bench) else np.nan,
                "squad_value_tenths": total_price,
                **by_pos,
            })
    out = pd.DataFrame(rows, columns=SQUAD_COLUMNS)
    return out.sort_values(["entry_id", "gw"]).reset_index(drop=True) if not out.empty else out


BREAKOUT_COLUMNS = ["entry_id", "season", "element_id", "first_gw_owned",
                    "gws_owned", "points_while_owned", "player_season_points",
                    "capture_share"]


def identify_breakouts(
    player_gw_points: pd.DataFrame,
    start_ownership: dict[int, float],
    *,
    max_start_ownership: float = 10.0,
    top_n: int = 12,
) -> list[int]:
    """This season's breakout players, defined without peeking at ownership later.

    A breakout is a player who was **not** widely owned at the start of the
    season and finished among the top scorers. Both halves are necessary: the
    top scorers alone are mostly the premiums everybody owned from GW1, and
    finding them early is not an edge because there was nothing to find.

    This is unavoidably a hindsight definition -- it uses the final points total
    -- and that is fine, because it is used to score *how early* managers got
    there, not to make a forward-looking pick. The distinction matters and is the
    reason this function is not exported to anything that produces a
    recommendation.
    """
    totals = player_gw_points.groupby("element_id")["points"].sum()
    eligible = [
        e for e in totals.index
        if start_ownership.get(int(e), 100.0) <= max_start_ownership
    ]
    return list(totals.loc[eligible].sort_values(ascending=False).head(top_n).index.astype(int))


def breakout_capture(
    picks: pd.DataFrame,
    player_gw_points: pd.DataFrame,
    breakouts: list[int],
) -> pd.DataFrame:
    """How much of each breakout player's season a manager actually banked.

    The measure is ``points scored while in the manager's squad / points scored
    all season``. It is strictly better than "the gameweek they bought him",
    because buying a breakout in GW6 and selling in GW9 is not the same as
    holding from GW6, and a first-purchase date scores both identically.
    """
    if picks is None or picks.empty or not breakouts:
        return pd.DataFrame(columns=BREAKOUT_COLUMNS)

    pts = player_gw_points[player_gw_points["element_id"].isin(breakouts)]
    season_total = pts.groupby("element_id")["points"].sum().to_dict()
    per_gw = {
        (int(r.element_id), int(r.gw)): float(r.points) for r in pts.itertuples()
    }

    held = picks[picks["element_id"].isin(breakouts)]
    rows = []
    for (eid, season, element), grp in held.groupby(["entry_id", "season", "element_id"]):
        gws = sorted(int(g) for g in grp["gw"])
        earned = sum(per_gw.get((int(element), g), 0.0) for g in gws)
        total = season_total.get(int(element), 0.0)
        rows.append({
            "entry_id": int(eid), "season": str(season), "element_id": int(element),
            "first_gw_owned": gws[0], "gws_owned": len(gws),
            "points_while_owned": earned, "player_season_points": total,
            "capture_share": earned / total if total else np.nan,
        })
    # Managers who never owned a given breakout captured zero of it, and
    # dropping them would compute the mean over owners only -- which measures
    # "how well did buyers time it", not "how much of the upside did you get".
    owners = {(r["entry_id"], r["element_id"]) for r in rows}
    for eid in picks["entry_id"].unique():
        for element in breakouts:
            if (int(eid), int(element)) in owners:
                continue
            rows.append({
                "entry_id": int(eid), "season": str(picks["season"].iloc[0]),
                "element_id": int(element), "first_gw_owned": np.nan, "gws_owned": 0,
                "points_while_owned": 0.0,
                "player_season_points": season_total.get(int(element), 0.0),
                "capture_share": 0.0,
            })
    return pd.DataFrame(rows, columns=BREAKOUT_COLUMNS)
