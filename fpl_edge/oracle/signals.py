"""The oracle's evidence layer.

The engine's edge does not come from one model. It comes from combining many
weak, partly independent signals — our own projection, bookmaker prices,
external projections, content creators, what demonstrably skilled managers own,
injury and tactical intel — into one verdict per player.

Three rules govern this module, and they exist because the naive version of this
idea is worse than useless:

1. **Every signal carries provenance.** Source, timestamp, and where it came
   from. A verdict you cannot trace is a verdict you cannot debug or defend.

2. **Every signal carries an `as_of`.** A creator's tip published after a
   deadline, or an injury update that landed on Friday night, must be invisible
   to a Friday-morning decision. Aggregation filters on this, so a backtest of
   the oracle is not quietly reading the future.

3. **Weights are earned, never assumed.** A source's influence is a function of
   its measured historical accuracy. Averaging pundit opinion at equal weight
   reproduces the template with extra steps and more confidence. A source with
   no track record yet gets a weight near zero and says so out loud.
"""

from __future__ import annotations

import datetime as dt
import enum
import math
from dataclasses import dataclass, field

import numpy as np


class Direction(enum.IntEnum):
    """Which way a signal points. Deliberately coarse."""

    STRONG_AGAINST = -2
    AGAINST = -1
    NEUTRAL = 0
    FOR = 1
    STRONG_FOR = 2


class SourceKind(enum.StrEnum):
    """Signal families. Kept explicit so correlated sources can be discounted.

    Sources within a family are heavily correlated: five creators repeating the
    same take are close to one observation, not five. Aggregation applies a
    within-family discount for exactly this reason.
    """

    OWN_MODEL = "own_model"
    MARKET = "market"
    EXTERNAL_PROJECTION = "external_projection"
    CREATOR = "creator"
    ELITE_MANAGER = "elite_manager"
    NEWS = "news"
    OWNERSHIP = "ownership"
    USER_IDEA = "user_idea"


@dataclass(frozen=True, slots=True)
class Signal:
    """One piece of evidence about one player."""

    player_code: int
    kind: SourceKind
    source: str                  # e.g. "dixon_coles", "paddypower", "FPL Harry"
    direction: Direction
    strength: float              # 0..1, how emphatic this source is
    rationale: str
    as_of: dt.datetime           # when this became knowable
    url: str | None = None
    numeric: float | None = None  # the underlying quantity, if there is one

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError(f"signal from {self.source!r} has a naive as_of")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"strength must be in [0, 1], got {self.strength}")

    @property
    def signed_strength(self) -> float:
        return float(self.direction) / 2.0 * self.strength


@dataclass(frozen=True, slots=True)
class SourceWeight:
    """A source's influence, earned from measured performance.

    ``hit_rate`` is the share of that source's resolved past claims that proved
    correct. ``sample`` is how many claims that is measured over. A source with
    a great hit rate over four claims should not move a decision, so the weight
    shrinks toward the prior by sample size.
    """

    source: str
    kind: SourceKind
    hit_rate: float | None
    sample: int
    prior_hit_rate: float = 0.5
    #: Claims needed before a source's own record dominates the prior.
    shrinkage: int = 25

    @property
    def is_measured(self) -> bool:
        return self.hit_rate is not None and self.sample > 0

    @property
    def weight(self) -> float:
        """Shrunk edge over chance, floored at zero.

        A source no better than a coin flip gets weight 0 and is reported as
        carrying no evidential value, rather than quietly contributing noise.
        """
        if not self.is_measured:
            return 0.0
        shrunk = (
            (self.hit_rate * self.sample + self.prior_hit_rate * self.shrinkage)
            / (self.sample + self.shrinkage)
        )
        return max(0.0, 2.0 * (shrunk - 0.5))

    @classmethod
    def from_skill_score(
        cls, source: str, kind: SourceKind, *, skill: float, sample: int
    ) -> "SourceWeight":
        """Build a weight for a MODEL, whose record is a skill score not a hit rate.

        A model is not judged on discrete correct/incorrect claims but on
        out-of-sample calibration against a baseline (see
        fpl_edge.eval.calibration.skill_score). We map that onto the same
        0.5-is-worthless scale so models and pundits are weighed on one ruler::

            effective hit rate = 0.5 + clamp(skill, 0, 1) / 2

        A model that merely matches its baseline maps to 0.5 and therefore
        contributes nothing, which is the correct treatment.
        """
        clamped = max(0.0, min(1.0, skill))
        return cls(source=source, kind=kind, hit_rate=0.5 + clamped / 2.0, sample=sample)

    def explain(self) -> str:
        if not self.is_measured:
            return f"{self.source}: no track record yet, weight 0 (carries no evidence)"
        return (
            f"{self.source}: {self.hit_rate:.0%} over {self.sample} resolved claims "
            f"-> weight {self.weight:.2f}"
        )


@dataclass
class Verdict:
    """The oracle's aggregated view of one player, with its working shown."""

    player_code: int
    score: float
    contributions: list[tuple[Signal, float]] = field(default_factory=list)
    unweighted_sources: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """How much independent, weighted evidence is behind the score.

        Distinct from the score itself: a strong score from one unproven source
        is not the same as a modest score from six independent measured ones.
        """
        by_kind: dict[SourceKind, float] = {}
        for sig, w in self.contributions:
            by_kind[sig.kind] = by_kind.get(sig.kind, 0.0) + abs(w)
        if not by_kind:
            return 0.0
        # Effective number of independent families, via a participation ratio.
        vals = np.array(list(by_kind.values()), dtype=float)
        if vals.sum() <= 0:
            return 0.0
        p = vals / vals.sum()
        effective_families = float(1.0 / np.sum(p ** 2))
        return min(1.0, effective_families / len(SourceKind) * math.sqrt(vals.sum()))

    def top_reasons(self, n: int = 5) -> list[tuple[Signal, float]]:
        return sorted(self.contributions, key=lambda cw: -abs(cw[1]))[:n]

    def explain(self, name: str | None = None) -> str:
        label = name or f"code {self.player_code}"
        lines = [f"{label}: oracle score {self.score:+.2f} "
                 f"(confidence {self.confidence:.0%})"]
        for sig, w in self.top_reasons():
            lines.append(
                f"  {w:+.3f}  [{sig.kind.value}/{sig.source}] {sig.rationale}"
            )
        if self.unweighted_sources:
            lines.append(
                "  ignored (no measured track record): "
                + ", ".join(sorted(set(self.unweighted_sources)))
            )
        return "\n".join(lines)


#: How much a family's total influence is damped. Sources inside a family repeat
#: each other, so their combined weight grows sub-linearly with count.
FAMILY_CAP: dict[SourceKind, float] = {
    SourceKind.OWN_MODEL: 1.0,
    SourceKind.MARKET: 1.0,
    SourceKind.EXTERNAL_PROJECTION: 0.8,
    SourceKind.CREATOR: 0.5,
    SourceKind.ELITE_MANAGER: 0.7,
    SourceKind.NEWS: 1.0,
    SourceKind.OWNERSHIP: 0.6,
    SourceKind.USER_IDEA: 0.3,
}


def aggregate(
    signals: list[Signal],
    weights: dict[str, SourceWeight],
    *,
    as_of: dt.datetime,
) -> dict[int, Verdict]:
    """Combine signals into one verdict per player.

    Signals published after ``as_of`` are dropped, so the same function can be
    used live and in a backtest without the backtest reading the future.

    Within a family, contributions are damped by ``sqrt(n)`` and capped: five
    creators echoing one take is close to one observation, not five.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    visible = [s for s in signals if s.as_of <= as_of]
    verdicts: dict[int, Verdict] = {}

    by_player: dict[int, list[Signal]] = {}
    for sig in visible:
        by_player.setdefault(sig.player_code, []).append(sig)

    for code, sigs in by_player.items():
        verdict = Verdict(player_code=code, score=0.0)
        families: dict[SourceKind, list[tuple[Signal, float]]] = {}
        for sig in sigs:
            sw = weights.get(sig.source)
            if sw is None or sw.weight <= 0.0:
                verdict.unweighted_sources.append(sig.source)
                continue
            families.setdefault(sig.kind, []).append(
                (sig, sig.signed_strength * sw.weight)
            )

        for kind, items in families.items():
            damping = FAMILY_CAP[kind] / math.sqrt(len(items))
            for sig, raw in items:
                contribution = raw * damping
                verdict.contributions.append((sig, contribution))
                verdict.score += contribution

        verdicts[code] = verdict

    return verdicts
