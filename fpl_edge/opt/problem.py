"""The optimiser's input: a fully-specified multi-gameweek decision problem.

Nothing in here reads the warehouse directly and nothing invents a forecast.
:func:`build_problem` assembles a problem from a point-in-time
:class:`~fpl_edge.store.Snapshot` plus injected forecasts; the tests build one
from committed fixtures instead. Both paths produce the same object, so a
constraint bug cannot hide behind "well, the real data is different".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from fpl_edge.rules.loader import RuleRegistry, rules
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Money, PlayerCode, Position, Season, TeamCode

CHIP_NAMES = ("wildcard", "freehit", "bboost", "3xc")


@dataclass(frozen=True, slots=True)
class Ruleset:
    """The constraint values, pulled once from the verified registry.

    Every number here comes from ``registry.yaml`` via ``RuleRegistry.get``,
    which raises on unverified rules. Nothing in the optimiser hardcodes a
    squad size, a budget or a hit cost.
    """

    squad_size: int
    starting_xi: int
    budget_tenths: int
    max_per_club: int
    select_by_position: Mapping[Position, int]
    min_play_by_position: Mapping[Position, int]
    max_play_by_position: Mapping[Position, int]
    free_per_gw: int
    max_banked_ft: int
    hit_cost: int
    transfer_cap_per_gw: int
    unlimited_before_first_deadline: bool
    chips_retain_banked_ft: bool
    captain_multiplier: int
    triple_captain_multiplier: int
    chip_windows: Mapping[str, tuple[tuple[int, int], ...]]
    chip_count_each: int
    chip_one_per_gw: bool
    freehit_not_consecutive: bool
    sell_on_fee_fraction: float

    @classmethod
    def from_registry(cls, reg: RuleRegistry | None = None) -> Ruleset:
        r = reg if reg is not None else rules()

        def by_pos(path: str) -> dict[Position, int]:
            raw = r.get(path)
            return {Position[k]: int(v) for k, v in raw.items()}

        windows = {
            name: tuple((int(a), int(b)) for a, b in spans)
            for name, spans in r.get("chips.windows").items()
        }
        return cls(
            squad_size=int(r.get("squad.size")),
            starting_xi=int(r.get("squad.starting_xi")),
            budget_tenths=int(r.get("squad.budget_tenths")),
            max_per_club=int(r.get("squad.max_per_club")),
            select_by_position=by_pos("squad.select_by_position"),
            min_play_by_position=by_pos("squad.min_play_by_position"),
            max_play_by_position=by_pos("squad.max_play_by_position"),
            free_per_gw=int(r.get("transfers.free_per_gw")),
            max_banked_ft=int(r.get("transfers.max_banked")),
            hit_cost=int(r.get("transfers.hit_cost")),
            transfer_cap_per_gw=int(r.get("transfers.cap_per_gw")),
            unlimited_before_first_deadline=bool(
                r.get("transfers.unlimited_before_first_deadline")
            ),
            chips_retain_banked_ft=bool(r.get("transfers.chips_retain_banked_ft")),
            captain_multiplier=int(r.get("captaincy.captain_multiplier")),
            triple_captain_multiplier=int(r.get("captaincy.triple_captain_multiplier")),
            chip_windows=windows,
            chip_count_each=int(r.get("chips.count_each")),
            chip_one_per_gw=bool(r.get("chips.one_per_gw")),
            freehit_not_consecutive=bool(r.get("chips.freehit_not_consecutive")),
            sell_on_fee_fraction=float(r.get("prices.sell_on_fee_fraction")),
        )

    def chip_available_in_gw(self, chip: str, gw: int) -> bool:
        return any(lo <= gw <= hi for lo, hi in self.chip_windows.get(chip, ()))

    def chip_half_of(self, chip: str, gw: int) -> int | None:
        """Index of the availability window (season half) containing ``gw``."""
        for idx, (lo, hi) in enumerate(self.chip_windows.get(chip, ())):
            if lo <= gw <= hi:
                return idx
        return None


@dataclass(frozen=True, slots=True)
class PlayerRow:
    code: PlayerCode
    name: str
    position: Position
    team_code: TeamCode


@dataclass(frozen=True, slots=True)
class ChipState:
    """Chips already spent, per chip name, as the gameweeks they were played in."""

    played: Mapping[str, tuple[GwId, ...]] = field(default_factory=dict)

    def remaining_in_half(self, chip: str, ruleset: Ruleset, half: int) -> int:
        used = sum(
            1 for gw in self.played.get(chip, ()) if ruleset.chip_half_of(chip, int(gw)) == half
        )
        # One of each chip per half of the season.
        return max(0, 1 - used)

    def last_gw(self, chip: str) -> int | None:
        got = self.played.get(chip, ())
        return max(int(g) for g in got) if got else None


@dataclass(frozen=True, slots=True)
class SquadState:
    """What you own entering the first gameweek of the horizon.

    ``holdings`` maps player code to the price you *bought at*, not the current
    price. The distinction is the whole sell-on fee: a player bought at 7.5 and
    now worth 7.8 sells for 7.6, and using 7.8 there overstates your budget by
    a tenth per player.

    Empty holdings mean the pre-GW1 case: no squad yet, unlimited transfers.
    """

    holdings: Mapping[PlayerCode, Money] = field(default_factory=dict)
    bank: Money = field(default_factory=lambda: Money(0))
    free_transfers: int = 1
    chips: ChipState = field(default_factory=ChipState)

    @property
    def is_preseason(self) -> bool:
        return len(self.holdings) == 0


@dataclass(frozen=True)
class HorizonProblem:
    """A rolling-horizon squad decision problem.

    All the per-(player, gameweek) data is column-aligned to ``players`` and
    ``gws``, in that order. Row i of every array is ``players[i]``.
    """

    season: Season
    gws: tuple[GwId, ...]
    players: tuple[PlayerRow, ...]
    #: (n_players, n_gws) integer tenths. From a PriceForecast.
    price_tenths: np.ndarray
    #: (n_players, n_gws) expected points. From a PointsForecast.
    xpts: np.ndarray
    #: (n_players, n_gws) P(at least one minute). Used by the vice-captain term.
    p_play: np.ndarray
    #: (n_players, n_gws) may this player be *in the squad* this gameweek.
    #: Injured players are ownable; the XI constraints are what keep them benched.
    ownable: np.ndarray
    state: SquadState = field(default_factory=SquadState)
    ruleset: Ruleset = field(default_factory=Ruleset.from_registry)

    def __post_init__(self) -> None:
        n, t = len(self.players), len(self.gws)
        for name in ("price_tenths", "xpts", "p_play", "ownable"):
            arr = getattr(self, name)
            if arr.shape != (n, t):
                raise ValueError(f"{name} has shape {arr.shape}, expected {(n, t)}")
        if self.price_tenths.dtype.kind not in "iu":
            raise TypeError(
                "price_tenths must be an integer array of tenths. Float prices "
                "silently break sell-on-fee arithmetic."
            )
        codes = [p.code for p in self.players]
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate player codes in the universe")
        unknown = set(self.state.holdings) - set(codes)
        if unknown:
            raise ValueError(f"held players missing from the universe: {sorted(unknown)}")
        if len(self.gws) != len(set(self.gws)):
            raise ValueError("duplicate gameweeks")
        if list(self.gws) != sorted(self.gws):
            raise ValueError("gws must be ascending")

    @property
    def initial_bank(self) -> Money:
        """Money available before the first gameweek's transfers.

        Pre-season there is no squad to sell, so the starting bank is the
        squad budget from the rule registry. An explicit non-zero
        ``state.bank`` always wins, so a partially-built squad still works.
        """
        if self.state.is_preseason and self.state.bank.tenths == 0:
            return Money(self.ruleset.budget_tenths)
        return self.state.bank

    @property
    def initial_free_transfers(self) -> int:
        """Free transfers entering the first gameweek of the horizon.

        Pre-season this is 0, not 1: transfers before the first deadline are
        unlimited and free, and the single free transfer for GW2 is generated
        by the normal carryover rule rather than banked from GW1.
        """
        if self.state.is_preseason and self.ruleset.unlimited_before_first_deadline:
            return 0
        return self.state.free_transfers

    @property
    def n_players(self) -> int:
        return len(self.players)

    @property
    def n_gws(self) -> int:
        return len(self.gws)

    @property
    def index_of(self) -> dict[PlayerCode, int]:
        return {p.code: i for i, p in enumerate(self.players)}

    def codes(self) -> np.ndarray:
        return np.array([int(p.code) for p in self.players], dtype=np.int64)

    def prune(self, max_per_position: int | None) -> HorizonProblem:
        """Keep the top-N per position by horizon-total xPts, plus everything owned.

        Reduces the MILP, and changes the answer. Only ever called explicitly.
        """
        if max_per_position is None:
            return self
        total = self.xpts.sum(axis=1)
        keep: set[int] = {i for i, p in enumerate(self.players) if p.code in self.state.holdings}
        for pos in Position:
            idx = [i for i, p in enumerate(self.players) if p.position is pos]
            idx.sort(key=lambda i: (-total[i], int(self.players[i].code)))
            keep.update(idx[:max_per_position])
        order = sorted(keep)
        return HorizonProblem(
            season=self.season,
            gws=self.gws,
            players=tuple(self.players[i] for i in order),
            price_tenths=self.price_tenths[order],
            xpts=self.xpts[order],
            p_play=self.p_play[order],
            ownable=self.ownable[order],
            state=self.state,
            ruleset=self.ruleset,
        )


def build_problem(
    snapshot: Snapshot,
    season: Season,
    gws: Sequence[GwId],
    *,
    price_forecast: object,
    points_forecast: object,
    state: SquadState | None = None,
    ruleset: Ruleset | None = None,
) -> HorizonProblem:
    """Assemble a problem from a point-in-time snapshot and injected forecasts.

    The snapshot supplies identity, position, club and availability. Prices and
    points come from the forecast objects. The optimiser never reads either
    from the warehouse itself, because "current price" and "current form" are
    exactly the columns that leak.
    """
    gw_list = [GwId(int(g)) for g in gws]
    # The purchasable universe is what the game will actually let you pick --
    # selectable() filters on FPL's own can_select flag. Using the full player
    # table here let the optimiser recommend players who left the league in
    # the summer (32 of 592 at the GW1 snapshot). One exception: players the
    # manager already HOLDS stay in the universe even if no longer selectable,
    # because a held player can be kept or sold, just not bought.
    frame = snapshot.selectable(str(season))
    if state is not None and state.holdings:
        held = set(int(c) for c in state.holdings)
        missing = held - set(frame["code"].astype(int))
        if missing:
            everyone = snapshot.players(str(season))
            frame = pd.concat(
                [frame, everyone[everyone["code"].astype(int).isin(missing)]],
                ignore_index=True,
            )
    if frame.empty:
        raise ValueError(f"no players visible at {snapshot.as_of} for {season}")
    frame = frame.sort_values("code").reset_index(drop=True)

    prices = price_forecast.forecast(snapshot, season, gw_list)  # type: ignore[attr-defined]
    points = points_forecast.forecast(snapshot, season, gw_list)  # type: ignore[attr-defined]

    players = tuple(
        PlayerRow(
            code=PlayerCode(int(r.code)),
            name=str(r.web_name),
            position=Position(int(r.position)),
            team_code=TeamCode(int(r.team_code)),
        )
        for r in frame.itertuples()
    )
    return HorizonProblem(
        season=season,
        gws=tuple(gw_list),
        players=players,
        price_tenths=_pivot(prices, players, gw_list, "price_tenths", int).astype(np.int64),
        xpts=_pivot(points, players, gw_list, "xpts", float),
        p_play=_pivot(points, players, gw_list, "p_play", float),
        ownable=_ownable(frame, players, gw_list, state),
        state=state or SquadState(),
        ruleset=ruleset or Ruleset.from_registry(),
    )


def _pivot(
    frame: pd.DataFrame,
    players: Sequence[PlayerRow],
    gws: Sequence[GwId],
    column: str,
    kind: type,
) -> np.ndarray:
    if column not in frame.columns:
        raise ValueError(f"forecast is missing required column {column!r}")
    wide = frame.pivot_table(index="code", columns="gw", values=column, aggfunc="first")
    out = np.zeros((len(players), len(gws)), dtype=np.int64 if kind is int else np.float64)
    for i, p in enumerate(players):
        for j, gw in enumerate(gws):
            try:
                val = wide.at[int(p.code), int(gw)]
            except KeyError:
                raise ValueError(f"forecast has no {column} for code={p.code} gw={gw}") from None
            if pd.isna(val):
                raise ValueError(f"forecast has NaN {column} for code={p.code} gw={gw}")
            out[i, j] = kind(val)
    return out


def _ownable(
    frame: pd.DataFrame,
    players: Sequence[PlayerRow],
    gws: Sequence[GwId],
    state: SquadState | None,
) -> np.ndarray:
    from fpl_edge.types import Availability

    held = set(state.holdings) if state else set()
    status = dict(zip(frame["code"].astype(int), frame["status"].astype(str)))
    out = np.ones((len(players), len(gws)), dtype=bool)
    for i, p in enumerate(players):
        if p.code in held:
            continue
        try:
            selectable = Availability(status[int(p.code)]).is_selectable
        except ValueError:
            selectable = False
        if not selectable:
            out[i, :] = False
    return out
