"""The optimiser's output.

A plan carries only decisions -- who is owned, who starts, who is captain, what
was bought and sold, which chip was played. It deliberately does NOT carry any
of the MILP's internal variables. That is what makes
:func:`fpl_edge.opt.scoring.score_plan` an independent check: it has to
recompute the objective from the decisions alone, so a model whose constraints
disagree with its own scorer shows up as a number mismatch rather than as a
quietly wrong squad.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fpl_edge.opt.config import ObjectiveMode
from fpl_edge.types import GwId, Money, PlayerCode


@dataclass(frozen=True, slots=True)
class GwDecision:
    """One gameweek of a plan."""

    gw: GwId
    #: The 15 players owned persistently after this gameweek's transfers.
    squad: tuple[PlayerCode, ...]
    #: The 15 that actually score this gameweek. Differs from ``squad`` only
    #: under Free Hit, where the persistent squad is untouched.
    fielded: tuple[PlayerCode, ...]
    starting_xi: tuple[PlayerCode, ...]
    #: Bench in priority order: keeper first, then outfield slots 1, 2, 3.
    bench: tuple[PlayerCode, ...]
    captain: PlayerCode
    vice_captain: PlayerCode
    chip: str | None
    transfers_in: tuple[PlayerCode, ...]
    transfers_out: tuple[PlayerCode, ...]
    free_transfers_available: int
    hits: int
    bank_after: Money
    squad_value: Money

    @property
    def n_transfers(self) -> int:
        return len(self.transfers_in)

    @property
    def hit_points(self) -> int:
        return self.hits * 4


@dataclass(frozen=True, slots=True)
class HorizonPlan:
    """A whole rolling-horizon decision, plus how it was produced."""

    season: str
    mode: ObjectiveMode
    decisions: tuple[GwDecision, ...]
    #: The objective as reported by the solver, in the units of ``mode``.
    objective: float
    solver: str
    status: str
    solve_seconds: float
    mip_gap: float | None = None
    notes: tuple[str, ...] = ()

    @property
    def gws(self) -> tuple[GwId, ...]:
        return tuple(d.gw for d in self.decisions)

    def at(self, gw: GwId | int) -> GwDecision:
        for d in self.decisions:
            if int(d.gw) == int(gw):
                return d
        raise KeyError(f"GW{gw} is not in this plan ({[int(g) for g in self.gws]})")

    @property
    def total_hits(self) -> int:
        return sum(d.hits for d in self.decisions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "mode": str(self.mode),
            "objective": self.objective,
            "solver": self.solver,
            "status": self.status,
            "solve_seconds": self.solve_seconds,
            "mip_gap": self.mip_gap,
            "notes": list(self.notes),
            "gameweeks": [
                {
                    "gw": int(d.gw),
                    "chip": d.chip,
                    "squad": [int(c) for c in d.squad],
                    "fielded": [int(c) for c in d.fielded],
                    "xi": [int(c) for c in d.starting_xi],
                    "bench": [int(c) for c in d.bench],
                    "captain": int(d.captain),
                    "vice": int(d.vice_captain),
                    "in": [int(c) for c in d.transfers_in],
                    "out": [int(c) for c in d.transfers_out],
                    "ft": d.free_transfers_available,
                    "hits": d.hits,
                    "bank_after": d.bank_after.tenths,
                    "squad_value": d.squad_value.tenths,
                }
                for d in self.decisions
            ],
        }
