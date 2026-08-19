"""Turn each data source into :class:`Signal` objects.

Adapters are deliberately thin and independent. A source that is not yet wired
contributes nothing and is reported as missing, rather than being silently
approximated by a source that happens to be available.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from fpl_edge.models.contracts import PointsSample
from fpl_edge.oracle.signals import Direction, Signal, SourceKind

#: Bands for turning a continuous z-score into a coarse direction. Coarse on
#: purpose: the precision of "1.7 standard deviations above positional mean" is
#: not real, and pretending otherwise invites overfitting to it.
_STRONG_Z = 1.25
_WEAK_Z = 0.4


def _direction_from_z(z: float) -> Direction:
    if z >= _STRONG_Z:
        return Direction.STRONG_FOR
    if z >= _WEAK_Z:
        return Direction.FOR
    if z <= -_STRONG_Z:
        return Direction.STRONG_AGAINST
    if z <= -_WEAK_Z:
        return Direction.AGAINST
    return Direction.NEUTRAL


def _z_by_position(values: pd.Series, positions: pd.Series) -> pd.Series:
    """Standardise within position.

    Comparing a defender's projected points against a forward's would make the
    oracle a position-preference machine rather than a player-selection one.
    """
    out = pd.Series(0.0, index=values.index)
    for pos, idx in positions.groupby(positions).groups.items():
        block = values.loc[idx]
        sd = block.std()
        out.loc[idx] = 0.0 if sd == 0 or np.isnan(sd) else (block - block.mean()) / sd
    return out.fillna(0.0)


def from_points_model(
    sample: PointsSample, players: pd.DataFrame, *, as_of: dt.datetime,
    source: str = "decomposed_points",
) -> list[Signal]:
    """Our own projection, standardised within position."""
    xpts = pd.Series(sample.mean(), index=sample.codes)
    pos = players.set_index("code")["position"].reindex(sample.codes)
    z = _z_by_position(xpts, pos)
    haul = pd.Series(sample.p_at_least(10), index=sample.codes)

    out = []
    for code in sample.codes:
        out.append(Signal(
            player_code=int(code), kind=SourceKind.OWN_MODEL, source=source,
            direction=_direction_from_z(float(z[code])),
            strength=float(min(1.0, abs(z[code]) / 2.0)),
            rationale=(f"projected {xpts[code]:.2f} pts, "
                       f"{haul[code]:.0%} chance of a 10+ haul "
                       f"({z[code]:+.1f} sd for the position)"),
            as_of=as_of, numeric=float(xpts[code]),
        ))
    return out


def from_anytime_scorer_odds(
    odds: pd.DataFrame, players: pd.DataFrame, *, as_of: dt.datetime,
) -> list[Signal]:
    """Bookmaker anytime-scorer prices.

    The market is a strong, well-calibrated aggregator and deserves its own
    family: agreeing with it is weak evidence, disagreeing with it is a claim
    that needs justifying.

    ``odds`` must carry ``code`` and ``prob`` (de-vigged) columns.
    """
    if odds.empty:
        return []
    pos = players.set_index("code")["position"]
    probs = odds.set_index("code")["prob"]
    z = _z_by_position(probs, pos.reindex(probs.index).fillna(3))

    out = []
    for code, p in probs.items():
        out.append(Signal(
            player_code=int(code), kind=SourceKind.MARKET, source="bookmaker_consensus",
            direction=_direction_from_z(float(z[code])),
            strength=float(min(1.0, abs(z[code]) / 2.0)),
            rationale=f"market prices {p:.1%} to score anytime ({z[code]:+.1f} sd)",
            as_of=as_of, numeric=float(p),
        ))
    return out


def from_ownership_differential(
    players: pd.DataFrame, sample: PointsSample, *, as_of: dt.datetime,
) -> list[Signal]:
    """Differential value: strong projection the field has not noticed.

    This is the only adapter that encodes the rank objective rather than points.
    A highly owned player is not a bad pick; they are simply a smaller source of
    RANK edge, because owning what everyone owns cannot move you up the table.
    Signals here are therefore modest and are damped further by the family cap.
    """
    xpts = pd.Series(sample.mean(), index=sample.codes)
    own = players.set_index("code")["selected_by_pct"].reindex(sample.codes)
    pos = players.set_index("code")["position"].reindex(sample.codes)
    z_pts = _z_by_position(xpts, pos)

    out = []
    for code in sample.codes:
        o = own.get(code)
        if o is None or np.isnan(o):
            continue
        # Edge is projection strength the field has not priced into ownership.
        edge = float(z_pts[code]) * (1.0 - min(float(o), 60.0) / 60.0)
        out.append(Signal(
            player_code=int(code), kind=SourceKind.OWNERSHIP,
            source="differential_value",
            direction=_direction_from_z(edge),
            strength=float(min(1.0, abs(edge) / 2.0)),
            rationale=(f"{o:.1f}% owned against a {z_pts[code]:+.1f} sd projection "
                       f"-> rank edge {edge:+.2f}"),
            as_of=as_of, numeric=edge,
        ))
    return out
