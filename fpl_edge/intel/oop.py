"""Out-of-position detection: FPL's classification against the player's profile.

This is one of the largest standing edges in FPL and it exists for a structural
reason. FPL assigns every player one ``element_type`` and never changes it
mid-season, but a manager can play a nominal defender as a wing-back or a winger,
and a nominal midfielder as a third centre-back. When that happens the player is
priced, and scored, in the wrong bucket:

* A **defender who plays as a wing-back** collects 6 points per goal and 3 per
  assist while still being eligible for the 4-point clean sheet and the 10-action
  defensive-contribution threshold. He is competing for selection against £4.5m
  centre-backs on attacking returns.
* A **midfielder who plays as a holding centre-back** is the reverse trap: he
  looks cheap for a midfielder, but he has the attacking output of a defender
  and only the 12-action DC threshold rather than the defender's 10.

Detection, without magic numbers
--------------------------------
FPL publishes no role, so the classification is inferred from what the player
actually did, using the per-90 rates from
:mod:`fpl_edge.models.points.shares` -- the same shrunk, season-decayed
estimates the points model itself consumes, so this cannot disagree with the
projection about what a player's rates are.

Two features carry the signal and they pull in opposite directions:

``attack90``  ``xg90 + xa90``. Rises monotonically with how far up the pitch
              a player operates.
``dc_rate``   share of appearances clearing the defensive-contribution
              threshold. Falls monotonically with the same thing.

For each candidate position the *empirical* distribution of both features is
built from the players FPL assigns to it, and a player's fit for that position is
how typical they are within it: ``1 - 2 * |percentile - 0.5|``, averaged over the
two features. A player at the median of a position scores 1.0 there; one at its
extreme scores 0.0. The position with the best fit is what the player "plays
like", and the reported ``score`` is the margin over their FPL position.

Everything is a percentile within the season's own population, so nothing here
is a threshold copied from a blog post, and the detector recalibrates itself
when the league's overall attacking rates move.

The honest limits, stated rather than buried
--------------------------------------------
* This measures **output**, not position. A defender with wing-back output might
  be a centre-back on a team that attacks constantly. The evidence string always
  quotes the percentiles so the reader can judge.
* It needs minutes. Below :data:`MIN_MINUTES` the shrinkage in ``shares.py``
  pulls a player onto their positional prior by construction, which would make
  every low-minutes player look perfectly typical and produce a confident "no
  mismatch" from no evidence at all. Those players are excluded and reported as
  excluded, never scored as normal.
* A player new to the league has no rates. They are excluded for the same
  reason, and the dossier says so rather than implying the check passed.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from fpl_edge.intel.items import IntelItem, IntelKind, OopSignal, content_id
from fpl_edge.types import Position

UTC = dt.timezone.utc

#: Minutes of history below which the empirical-Bayes shrinkage in shares.py has
#: pulled a player most of the way onto their positional prior, so their rates
#: describe the prior rather than the player. Ten full matches.
MIN_MINUTES = 900.0

#: Margin in fit at which a mismatch is worth reporting. Calibrated against the
#: 2026-27 population: at 0.15, the flagged defenders are the ones whose
#: attacking output sits above the *lower quartile of midfielders*, and the
#: flagged midfielders are the ones whose defensive-action rate sits above the
#: median defender. Below it the signal is dominated by ordinary variation in
#: how attacking a full-back's team is.
REPORT_MARGIN = 0.15

FEATURES = ("attack90", "dc_rate")

#: Goalkeepers are excluded as both subject and candidate. FPL never plays one
#: out of position, and including them lets a keeper's zero attacking rate make
#: every defensive midfielder "look like a goalkeeper".
CANDIDATES = (Position.DEF, Position.MID, Position.FWD)

#: How far up the pitch each position nominally operates. Used only for the
#: direction guard below, which is what stops the detector reporting a bit-part
#: centre-back as a wing-back.
ADVANCEMENT = {int(Position.DEF): 0, int(Position.MID): 1, int(Position.FWD): 2}

POS_NAME = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

#: What the mismatch means in football terms, for the headline.
ROLE_HINT: dict[tuple[int, int], str] = {
    (2, 3): "attacking full-back or wing-back — scoring as a defender, producing as a midfielder",
    (2, 4): "a defender producing like a forward; check for a converted striker or a set-piece target",
    (3, 2): "holding midfielder or auxiliary centre-back — midfielder points, defender workload",
    (3, 4): "advanced midfielder playing as a striker — forward output at midfielder scoring rates",
    (4, 3): "forward dropping deep; forward scoring rates on midfielder-shaped output",
    (4, 2): "forward with defender-shaped output; almost certainly a minutes artefact",
}


def _percentile(values: np.ndarray, x: float) -> float:
    """Share of ``values`` at or below ``x``. Empirical CDF, no distributional assumption."""
    if values.size == 0:
        return 0.5
    return float(np.mean(values <= x))


def _typicality(values: np.ndarray, x: float) -> float:
    """How central ``x`` is within ``values``: 1.0 at the median, 0.0 at either extreme."""
    return 1.0 - 2.0 * abs(_percentile(values, x) - 0.5)


def build_frame(rates: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Join per-90 rates to the current player list and derive the two features.

    ``rates`` is :attr:`fpl_edge.models.points.shares.PlayerRates.frame`, indexed
    by stable code; ``players`` is a snapshot player list. The join is on ``code``
    because it is the only identifier stable across the seasons the rates were
    estimated from -- joining on ``element_id`` would mix up players between
    seasons, which is the single most common way an FPL backtest becomes junk.
    """
    frame = rates.join(
        players.set_index("code")[["web_name", "position", "team_code"]], how="inner"
    )
    frame["attack90"] = frame["xg90"].astype(float) + frame["xa90"].astype(float)
    frame["dc_rate"] = frame["dc_rate"].astype(float)
    return frame


def detect(
    frame: pd.DataFrame,
    *,
    season: str,
    as_of: dt.datetime,
    min_minutes: float = MIN_MINUTES,
    report_margin: float = REPORT_MARGIN,
) -> tuple[list[OopSignal], dict[str, int]]:
    """Score every eligible player, returning mismatches and an exclusion count.

    The counter is returned rather than logged because the dossier has to be able
    to say "this player was not assessed and here is why", which is a different
    statement from "this player was assessed and looks normal".
    """
    counts = {"assessed": 0, "excluded_minutes": 0, "excluded_gkp": 0, "flagged": 0}
    eligible = frame[frame["minutes"].astype(float) >= float(min_minutes)]
    counts["excluded_minutes"] = int(len(frame) - len(eligible))

    pools: dict[int, dict[str, np.ndarray]] = {}
    for pos in CANDIDATES:
        sub = eligible[eligible["position"].astype(int) == int(pos)]
        pools[int(pos)] = {f: sub[f].to_numpy(dtype=float) for f in FEATURES}

    signals: list[OopSignal] = []
    for code, row in eligible.iterrows():
        fpl_pos = int(row["position"])
        if fpl_pos == int(Position.GKP):
            counts["excluded_gkp"] += 1
            continue
        counts["assessed"] += 1

        fits = {
            int(p): float(
                np.mean([_typicality(pools[int(p)][f], float(row[f])) for f in FEATURES])
            )
            for p in CANDIDATES
        }
        plays_like = max(fits, key=lambda p: fits[p])
        margin = fits[plays_like] - fits[fpl_pos]
        if plays_like == fpl_pos or margin < report_margin:
            continue

        pcts = {
            f: (
                _percentile(pools[fpl_pos][f], float(row[f])),
                _percentile(pools[plays_like][f], float(row[f])),
            )
            for f in FEATURES
        }
        # Direction guard. "Atypical for a defender" is not the same claim as
        # "plays further forward than a defender", and without this the two
        # collapse: a bit-part centre-back with almost no attacking output AND
        # almost no defensive actions is atypical in every direction, so the
        # typicality argmax lands on midfielder and the detector reports a
        # wing-back who does not exist. Measured case: Lindelof, attacking
        # output at the 4th percentile among defenders and the 1st among
        # midfielders, was flagged DEF -> MID at margin 0.34 before this test
        # existed. To claim a player operates further forward than his badge, he
        # must be above the median of his OWN position on attacking output; to
        # claim he operates deeper, above the median of his own position on
        # defensive actions.
        moving_forward = ADVANCEMENT[plays_like] > ADVANCEMENT[fpl_pos]
        driver = "attack90" if moving_forward else "dc_rate"
        if pcts[driver][0] < 0.5:
            continue

        counts["flagged"] += 1
        evidence = (
            f"attacking output {row['attack90']:.3f}/90 sits at the "
            f"{pcts['attack90'][0]:.0%} percentile among {POS_NAME[fpl_pos]}s and the "
            f"{pcts['attack90'][1]:.0%} percentile among {POS_NAME[plays_like]}s; "
            f"defensive-contribution rate {row['dc_rate']:.3f} at the "
            f"{pcts['dc_rate'][0]:.0%} and {pcts['dc_rate'][1]:.0%} percentiles "
            f"respectively. {int(row['minutes']):,} weighted minutes of history."
        )
        signals.append(
            OopSignal(
                season=season, code=int(code), fpl_position=fpl_pos,
                plays_like=plays_like, score=round(margin, 4),
                evidence=evidence, as_of=as_of,
            )
        )
    signals.sort(key=lambda s: -s.score)
    return signals, counts


def to_items(signals: list[OopSignal], names: dict[int, str]) -> list[IntelItem]:
    """Turn mismatches into dated news items."""
    items = []
    for s in signals:
        hint = ROLE_HINT.get((s.fpl_position, s.plays_like), "role mismatch")
        who = names.get(s.code, f"player {s.code}")
        items.append(
            IntelItem(
                item_id=content_id("oop", s.season, s.code, s.as_of.isoformat()),
                published_at=s.as_of,
                observed_at=s.as_of,
                kind=IntelKind.OUT_OF_POSITION,
                headline=(
                    f"{who} is classified {POS_NAME[s.fpl_position]} but performs like a "
                    f"{POS_NAME[s.plays_like]} ({hint})"
                ),
                body=s.evidence,
                source="fpl_edge.intel.oop",
                season=s.season,
                player_code=s.code,
                # Inferred from output, not observed as a role. Says so.
                confidence=min(0.9, 0.4 + s.score),
            )
        )
    return items


def explain(signal: OopSignal | None, *, position: int, name: str) -> str:
    """The dossier's out-of-position paragraph, including the negative case."""
    if signal is None:
        return (
            f"No out-of-position signal. {name} performs within the normal range for a "
            f"{POS_NAME.get(int(position), '?')} on attacking output and defensive-action "
            "rate, or has too little history to assess (see the minutes note above)."
        )
    hint = ROLE_HINT.get((signal.fpl_position, signal.plays_like), "role mismatch")
    return (
        f"FPL classifies {name} as {POS_NAME[signal.fpl_position]}; the rates say "
        f"{POS_NAME[signal.plays_like]} (margin {signal.score:.2f}). Reading: {hint}.\n"
        f"  {signal.evidence}\n"
        "  Inferred from output, not from observed positional data — FPL publishes no role."
    )
