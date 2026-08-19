"""Deduplication and the consensus map -- with its own limitations attached.

Deduplication is not optional bookkeeping here, it is what stops the numbers
being wrong. A creator publishes the same opinion three times: on the podcast
feed, on the YouTube channel, and in the show notes attached to both. Counting
those as three votes triples the apparent agreement for whoever is most
prolific, which means the consensus map would measure *publication volume*
rather than agreement. So claims collapse to one per
``(creator, player, action, gameweek)``, keeping the earliest publication -- the
earliest, because that is when the opinion actually became available to act on,
and a later repeat adds no information a manager did not already have.

Concentration is reported because agreement without concentration is noise.
Fifteen creators each naming a different captain is not a signal that the
fifteenth is right; it is fifteen coin flips. The map therefore carries:

* ``share`` -- weighted agreement for this player/action as a fraction of all
  weighted claims for that action in that gameweek;
* ``hhi`` -- the Herfindahl index over that action's whole distribution, which
  is high when opinion is concentrated on one or two names and low when it is
  scattered;
* ``distinct_creators`` -- the raw count, kept alongside the weighted number so
  the difference between "many creators" and "many *good* creators" is legible.

And the weighting. Every aggregate here is available in two forms: raw counts
and track-record-weighted counts. The raw form exists for reporting and for the
docs, and is explicitly not for model input, for the reason set out at length in
:mod:`fpl_edge.ingest.content.scoring`. An unweighted creator consensus is a
noisy reconstruction of the template, and the template is already a feature this
engine has. Only ``weighted_*`` columns should ever reach a model, and when
every creator's earned weight is 0.0 -- which is the correct state before any
claim has been scored -- the weighted columns are all zero and the consensus
contributes nothing. That is the intended behaviour, not a bug to be patched
with a default prior.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

UTC = dt.UTC


def deduplicate(claims: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """One claim per (creator, player, action, gameweek); earliest wins."""
    if claims.empty:
        return claims, 0
    frame = claims.copy()
    frame["published_at"] = pd.to_datetime(frame["published_at"], utc=True)
    before = len(frame)
    frame = (
        frame.sort_values(["published_at", "claim_id"])
        .drop_duplicates(subset=["creator", "player_code", "action", "season", "gameweek"],
                         keep="first")
        .reset_index(drop=True)
    )
    return frame, before - len(frame)


def consensus_map(
    claims: pd.DataFrame,
    weights: dict[str, float] | None = None,
    *,
    season: str | None = None,
    gameweek: int | None = None,
) -> pd.DataFrame:
    """Who is saying what about whom, and how concentrated the agreement is."""
    columns = [
        "season", "gameweek", "action", "player_code", "player_name",
        "distinct_creators", "claims", "mean_confidence",
        "weighted_creators", "weighted_share", "share", "hhi", "creators",
    ]
    if claims.empty:
        return pd.DataFrame(columns=columns)

    weights = weights or {}
    frame, _ = deduplicate(claims)
    if season is not None:
        frame = frame[frame["season"] == season]
    if gameweek is not None:
        frame = frame[frame["gameweek"] == int(gameweek)]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    frame = frame.assign(
        weight=frame["creator"].map(lambda c: float(weights.get(str(c), 0.0)))
    )

    grouped = (
        frame.groupby(["season", "gameweek", "action", "player_code"])
        .agg(
            distinct_creators=("creator", "nunique"),
            claims=("claim_id", "count"),
            mean_confidence=("confidence", "mean"),
            weighted_creators=("weight", "sum"),
            player_name=("player_name", "first"),
            creators=("creator", lambda s: ", ".join(sorted(set(s)))),
        )
        .reset_index()
    )

    # Shares are computed within (season, gameweek, action): "who is the
    # captain pick" is a different question from "who should you buy", and
    # pooling them would let a crowded buy list dilute a clear captain signal.
    totals = grouped.groupby(["season", "gameweek", "action"]).agg(
        total_creators=("distinct_creators", "sum"),
        total_weight=("weighted_creators", "sum"),
    )
    grouped = grouped.join(totals, on=["season", "gameweek", "action"])
    grouped["share"] = grouped["distinct_creators"] / grouped["total_creators"].replace(0, pd.NA)
    grouped["weighted_share"] = (
        grouped["weighted_creators"] / grouped["total_weight"].replace(0, pd.NA)
    ).fillna(0.0)

    hhi = (
        grouped.assign(sq=grouped["share"].fillna(0.0) ** 2)
        .groupby(["season", "gameweek", "action"])["sq"].sum()
        .rename("hhi")
    )
    grouped = grouped.join(hhi, on=["season", "gameweek", "action"])

    return (
        grouped[columns]
        .sort_values(["season", "gameweek", "action", "weighted_creators",
                      "distinct_creators"], ascending=[True, True, True, False, False])
        .reset_index(drop=True)
    )


def render_consensus(consensus: pd.DataFrame, *, top: int = 8) -> str:
    if consensus.empty:
        return "(no claims)"
    lines: list[str] = []
    for (season, gw, action), group in consensus.groupby(["season", "gameweek", "action"]):
        hhi = float(group["hhi"].iloc[0]) if not group["hhi"].isna().all() else 0.0
        lines.append(f"\n{season} GW{gw} :: {action}  (concentration HHI={hhi:.3f})")
        for row in group.head(top).itertuples(index=False):
            lines.append(
                f"  {row.distinct_creators:>2} creators "
                f"(w={row.weighted_creators:.2f}, share={float(row.share or 0):.0%}) "
                f"{row.player_name or row.player_code} "
                f"[conf {row.mean_confidence:.2f}] -- {row.creators[:70]}"
            )
    return "\n".join(lines)
