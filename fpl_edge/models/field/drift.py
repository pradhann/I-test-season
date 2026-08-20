"""Transfer-flow drift: carrying GW k-1 squads forward to the GW k deadline.

THE TIMING PROBLEM this module exists for: picks become public only when a
deadline passes. So at the moment the GW-k decision is made, the freshest
observed squads are the ones that locked at GW k-1 -- roughly a week stale --
while the *transfer counters* (``transfers_in_event`` / ``transfers_out_event``
on ``fact_player_state``) update continuously all week and are visible right up
to the deadline. The stale sample plus the live flow is strictly more
information than either alone, and the drift step is how they are combined:
each resampled squad is mutated by the transfers the flow says its manager
would plausibly have made since locking.

The mechanics are per-owner, which is the part that matters. The API's flow
counters are whole-field totals; dividing the outflow by the player's owner
count turns "400,000 managers sold Haaland" into "an owner of Haaland sold him
with probability 0.10 this window", which is the event a sampled squad
experiences. Replacements are drawn from the *inflow* distribution restricted
to legal moves (same position, squad stays under the price of the player sold
plus a tolerance, 3-per-club holds, no duplicates) so a drifted squad is still
a legal squad -- the property that makes the empirical construction worth
having in the first place.

What this deliberately does NOT model, stated so nobody discovers it later:
multi-transfer strategies (moves are drawn independently up to a cap), hits,
price changes between the observation and the deadline, and cohort-specific
flow (the counters aggregate the whole field; elite flow leads it -- see the
lead parameter in :mod:`fpl_edge.models.ownership.elite`). Each is a measured
candidate improvement once a season of crawled ``fact_manager_transfer`` rows
exists to fit against.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from fpl_edge.sim.field import FieldSquads
from fpl_edge.sim.squad import SQUAD_SIZE, XI_SIZE, PlayerUniverse
from fpl_edge.store import Snapshot

#: Ceiling on the per-owner sell probability over one drift horizon. A rate
#: above this means the flow window and the ownership denominator disagree
#: (e.g. a player whose ownership collapsed mid-window); capping keeps one bad
#: denominator from evacuating a player from every sampled squad.
MAX_SELL_PROB = 0.6

#: How many transfers a drifted squad may absorb. Real managers average about
#: one transfer per gameweek; two covers the bulk of the distribution without
#: letting a squad be rebuilt by drift into something nobody locked.
DEFAULT_MAX_SWAPS = 2

#: Price headroom for a replacement, in price tenths. A manager who sells a
#: 9.0 has 9.0 plus whatever was in the bank; the bank is unobservable per
#: squad pre-deadline, so a small fixed allowance stands in for it.
DEFAULT_PRICE_TOLERANCE_TENTHS = 3


@dataclass(frozen=True)
class FlowVelocity:
    """Whole-field transfer flow per day, aligned to a PlayerUniverse.

    ``provenance`` says how the rate was obtained; ``window_days`` is how long
    the measurement window was. Both are carried because a velocity measured
    over 20 minutes extrapolated across three days is quantisation noise
    dressed as signal, and the caller must be able to see that.
    """

    in_per_day: np.ndarray     # (P,) transfers in per day, whole field
    out_per_day: np.ndarray    # (P,) transfers out per day, whole field
    window_days: float
    as_of: dt.datetime
    provenance: str

    def __post_init__(self) -> None:
        if self.in_per_day.shape != self.out_per_day.shape:
            raise ValueError("flow arrays must align")
        if (self.in_per_day < 0).any() or (self.out_per_day < 0).any():
            raise ValueError("flow velocities are counts per day; negatives are a bug")


@dataclass(frozen=True)
class DriftRates:
    """Per-owner sell probabilities and the replacement distribution."""

    sell_prob: np.ndarray      # (P,) P(an owner of p sells p over the horizon)
    buy_weight: np.ndarray     # (P,) unnormalised replacement mass
    horizon_days: float
    provenance: str


def drift_rates(
    velocity: FlowVelocity,
    ownership: np.ndarray,
    field_size: float,
    horizon_days: float,
    *,
    max_sell_prob: float = MAX_SELL_PROB,
) -> DriftRates:
    """Turn whole-field flow into the per-owner rates a sampled squad obeys.

    ``ownership`` is the squad-inclusion share (sums to ~15) used to convert
    field totals into per-owner probabilities. A player with no owners cannot
    be sold, and the guard below stops a 0.1%-owned player's tiny denominator
    from manufacturing a certainty.
    """
    if horizon_days < 0:
        raise ValueError("horizon_days must be non-negative")
    own = np.asarray(ownership, dtype=float)
    owners = np.maximum(own * float(field_size), 1.0)
    sell = np.clip(velocity.out_per_day * horizon_days / owners, 0.0, max_sell_prob)
    sell = np.where(own <= 0, 0.0, sell)
    buy = np.clip(velocity.in_per_day, 0.0, None) * horizon_days
    return DriftRates(
        sell_prob=sell, buy_weight=buy, horizon_days=float(horizon_days),
        provenance=f"{velocity.provenance}; horizon={horizon_days:.2f}d",
    )


def measure_flow_velocity(
    snapshot: Snapshot,
    season: str,
    universe: PlayerUniverse,
    *,
    lookback: dt.timedelta = dt.timedelta(hours=24),
    min_window_days: float = 0.10,
) -> FlowVelocity | None:
    """Flow per day from two polls of ``fact_player_state``, or None.

    The event counters reset when a deadline passes, so a negative difference
    means the window straddled a reset; those players fall back to the level
    divided by the time since the last deadline. Returns ``None`` when there
    is genuinely nothing to measure -- one poll, a sub-``min_window_days``
    window, or flow that is identically zero (the pre-GW1 state, where the
    counters have never moved). None means "drift disabled", never "drift of
    zero measured".
    """
    players = snapshot.players(season)
    if players.empty:
        return None
    players = players.sort_values("code").reset_index(drop=True)
    now_in = _aligned(players, "transfers_in_event", universe)
    now_out = _aligned(players, "transfers_out_event", universe)
    if not np.any(now_in) and not np.any(now_out):
        return None

    wh = snapshot.escape_hatch_unfiltered(
        "constructing a strictly earlier Snapshot to measure transfer-flow "
        "velocity; an earlier as_of is a subset of the current one and nothing "
        "is read outside point-in-time filtering"
    )
    earlier = wh.snapshot_at(snapshot.as_of - lookback)
    try:
        prior = earlier.players(season)
    except Exception:
        prior = pd.DataFrame()
    if prior.empty:
        return None
    prior = prior.sort_values("code").reset_index(drop=True)
    gap_days = (
        snapshot.as_of - pd.to_datetime(prior["as_of"], utc=True).max()
    ).total_seconds() / 86400.0
    if not np.isfinite(gap_days) or gap_days < min_window_days:
        return None
    prev_in = _aligned(prior, "transfers_in_event", universe)
    prev_out = _aligned(prior, "transfers_out_event", universe)

    d_in, d_out = now_in - prev_in, now_out - prev_out
    reset = (d_in < 0) | (d_out < 0)
    if reset.any():
        # Counter reset mid-window: use the level over the time since the
        # deadline that reset it, when that time is knowable; else drop to 0.
        since = _days_since_last_deadline(snapshot, season)
        if since is not None and since > min_window_days:
            d_in = np.where(reset, now_in / since * gap_days, d_in)
            d_out = np.where(reset, now_out / since * gap_days, d_out)
        else:
            d_in = np.where(reset, 0.0, d_in)
            d_out = np.where(reset, 0.0, d_out)
    return FlowVelocity(
        in_per_day=np.clip(d_in, 0.0, None) / gap_days,
        out_per_day=np.clip(d_out, 0.0, None) / gap_days,
        window_days=float(gap_days),
        as_of=snapshot.as_of,
        provenance=f"fact_player_state two-poll diff over {gap_days:.2f}d",
    )


def _aligned(players: pd.DataFrame, column: str, universe: PlayerUniverse) -> np.ndarray:
    s = pd.Series(
        players[column].fillna(0).to_numpy(dtype=float),
        index=players["code"].to_numpy(),
    )
    return s.reindex(universe.codes).fillna(0.0).to_numpy(dtype=float)


def _days_since_last_deadline(snapshot: Snapshot, season: str) -> float | None:
    events = snapshot.table("dim_event", where="season = ?", params=[season])
    if events.empty:
        return None
    past = events[pd.to_datetime(events["deadline_utc"], utc=True) <= snapshot.as_of]
    if past.empty:
        return None
    latest = pd.to_datetime(past["deadline_utc"], utc=True).max()
    return (snapshot.as_of - latest).total_seconds() / 86400.0


# ---------------------------------------------------------------------------
# the drift step itself
# ---------------------------------------------------------------------------


def apply_transfer_drift(
    squads: FieldSquads,
    universe: PlayerUniverse,
    rates: DriftRates,
    rng: np.random.Generator,
    *,
    expected_points: np.ndarray | None = None,
    max_swaps: int = DEFAULT_MAX_SWAPS,
    price_tolerance_tenths: int = DEFAULT_PRICE_TOLERANCE_TENTHS,
) -> tuple[FieldSquads, dict[str, int]]:
    """Mutate sampled squads by the transfers the flow implies. Pure; returns new arrays.

    For every (squad, slot), the held player is sold with ``sell_prob``. Sales
    beyond ``max_swaps`` per squad are dropped, keeping the ones the flow was
    most insistent about. Each sale is replaced by a same-position player drawn
    with probability proportional to ``buy_weight``, restricted to legal moves;
    a sale with no legal replacement is cancelled, which slightly understates
    churn and is reported in the receipt rather than papered over.

    Captaincy: a manager who sells their captain re-arms. With
    ``expected_points`` the new armband goes to the XI's highest-xP player
    (the same rule the sim's field already assumes); without it, to the vice.

    Returns the drifted squads and an itemised receipt:
    ``{attempted, applied, cancelled_no_replacement, capped}``.
    """
    slots = squads.slots.copy()
    cap_slot = squads.captain_slot.copy()
    vice_slot = squads.vice_slot.copy()
    m = slots.shape[0]

    sell_draw = rng.random(slots.shape) < rates.sell_prob[slots]
    receipt = {"attempted": int(sell_draw.sum()), "applied": 0,
               "cancelled_no_replacement": 0, "capped": 0}

    # Enforce the per-squad cap, keeping the highest-pressure sales.
    n_sales = sell_draw.sum(axis=1)
    for r in np.flatnonzero(n_sales > max_swaps):
        cols = np.flatnonzero(sell_draw[r])
        keep = cols[np.argsort(-rates.sell_prob[slots[r, cols]], kind="stable")[:max_swaps]]
        dropped = set(cols) - set(keep)
        sell_draw[r, list(dropped)] = False
        receipt["capped"] += len(dropped)

    buy = np.clip(rates.buy_weight, 0.0, None)
    price = universe.price_tenths
    pos = universe.position
    for r in np.flatnonzero(sell_draw.any(axis=1)):
        for c in np.flatnonzero(sell_draw[r]):
            out_idx = slots[r, c]
            in_squad = np.zeros(universe.n_players, dtype=bool)
            in_squad[slots[r]] = True
            legal = (
                (pos == pos[out_idx])
                & ~in_squad
                & (price <= price[out_idx] + price_tolerance_tenths)
            )
            # 3-per-club, counted over the 14 players who remain after the sale.
            remaining = np.delete(slots[r], c)
            clubs, cnt = np.unique(universe.team_code[remaining], return_counts=True)
            full_clubs = clubs[cnt >= 3]
            if len(full_clubs):
                legal &= ~np.isin(universe.team_code, full_clubs)
            w = buy * legal
            total = w.sum()
            if total <= 0:
                receipt["cancelled_no_replacement"] += 1
                continue
            new_idx = rng.choice(universe.n_players, p=w / total)
            slots[r, c] = new_idx
            receipt["applied"] += 1
        # Re-arm if the armband slot was transferred out. (Armbands are
        # slot-indexed, so a swap in the captain's slot changes the player
        # the armband is on -- which is a decision, not an accident.)
        if sell_draw[r, cap_slot[r]]:
            cap_slot[r] = _rearm(slots[r], vice_slot[r], expected_points)
            if cap_slot[r] == vice_slot[r]:
                vice_slot[r] = _rearm(slots[r], cap_slot[r], expected_points)
        elif sell_draw[r, vice_slot[r]]:
            vice_slot[r] = _rearm(slots[r], cap_slot[r], expected_points)

    out = FieldSquads(
        slots=slots,
        slot_pos=universe.position[slots],
        captain_slot=cap_slot,
        vice_slot=vice_slot,
    )
    _assert_legal(out, universe)
    return out, receipt


def _rearm(squad_slots: np.ndarray, exclude_slot: int,
           expected_points: np.ndarray | None) -> int:
    """New armband slot within the XI, excluding one slot."""
    xi = np.arange(XI_SIZE)
    xi = xi[xi != exclude_slot]
    if expected_points is None:
        return int(xi[0])
    return int(xi[np.argmax(expected_points[squad_slots[xi]])])


def _assert_legal(squads: FieldSquads, universe: PlayerUniverse) -> None:
    """Invariants the drift step must preserve; cheap enough to always run."""
    slots = squads.slots
    if slots.shape[1] != SQUAD_SIZE:
        raise AssertionError("drift changed the squad size")
    s = np.sort(slots, axis=1)
    if (s[:, 1:] == s[:, :-1]).any():
        raise AssertionError("drift introduced a duplicate player into a squad")
    if (universe.position[slots] != squads.slot_pos).any():
        raise AssertionError("slot_pos is stale after drift")
