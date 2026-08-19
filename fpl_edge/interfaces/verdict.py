"""The model seam: what the engine says about an idea, the moment it is had.

There are three providers here and the split is the point of the module.

:class:`SimulationVerdict` is the real one. It takes the
:class:`~fpl_edge.models.contracts.PointsModel` and
:class:`~fpl_edge.models.contracts.OwnershipModel` protocols and answers the
thesis the only way it can be honestly answered -- by counting the fraction of
correlated simulation draws in which the subject actually beats the comparator.
It is written against the published contracts, not against any implementation, so
it starts working the day a model is registered and needs no change here.

:class:`PriorVerdict` is what runs today, because on 2026-08-18 no gameweek of
2026-27 has been played and no points model has landed. It is deliberately weak
and deliberately legible: a monotone map from the subject's price rank inside its
own comparator set, adjusted for availability. It exists so the inbox is never
blocked on another team, and it is labelled ``degraded``/``low`` everywhere it
surfaces so that a 62% from it is never mistaken for a 62% from a simulation.

:class:`TimeBounded` composes them. The user's requirement is a verdict inside a
minute; a Monte Carlo over a double gameweek is not guaranteed to respect that,
and an inbox that hangs is an inbox that stops being used. So the primary
provider runs against a wall-clock budget and the prior answers if it overruns.
The idea row is already committed by then either way.

What none of these do is invent a number and present it as measured. Every
verdict carries its provider, its version and its confidence into the database,
so ``fpl idea review`` can separate the engine's calibration from the user's.
"""

from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import math
import time
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from fpl_edge.interfaces.features import comparator_set, player_history
from fpl_edge.interfaces.ideas import Comparator, Idea, IdeaKind, Stance, Verdict
from fpl_edge.models.contracts import ModelCard
from fpl_edge.store import Snapshot

UTC = dt.timezone.utc

#: Below/above these, the engine is taking a side. Between them it is admitting
#: the idea is a coin flip, which for most FPL decisions is the true answer and
#: is more useful to hear than a confident fabrication.
AGREE_AT = 0.55
DISAGREE_AT = 0.45

#: The prior is not allowed to express certainty. Nothing it knows -- a price and
#: an availability flag -- justifies more than this.
PRIOR_FLOOR, PRIOR_CEIL = 0.20, 0.80


@runtime_checkable
class VerdictProvider(Protocol):
    """Answers "will this thesis resolve correct?" for one idea.

    Implementations must be pure with respect to the snapshot: same idea, same
    snapshot, same answer. The registry stores the number as evidence, and
    evidence that changes when you look at it again is not evidence.
    """

    name: str
    version: str
    card: ModelCard

    def assess(self, idea: Idea, snapshot: Snapshot) -> tuple[float, str]:
        """Return (P(thesis resolves correct), a rationale a human can audit)."""
        ...


def _stance(p: float) -> Stance:
    if p >= AGREE_AT:
        return Stance.AGREE
    if p <= DISAGREE_AT:
        return Stance.DISAGREE
    return Stance.NEUTRAL


def _availability_multiplier(row: pd.Series | None) -> tuple[float, str]:
    """How much of the thesis survives the injury news.

    A player who does not play scores zero and loses to any median, so
    availability dominates every other signal on this path. Handled explicitly
    rather than folded into a score because "he's suspended" is the one reason
    the user most needs said back to them in plain words.
    """
    if row is None:
        return 1.0, ""
    status = str(row.get("status", "a") or "a")
    if status in ("i", "s", "u", "n"):
        label = {"i": "injured", "s": "suspended", "u": "unavailable", "n": "not in squad"}[status]
        return 0.15, f"flagged {label}"
    chance = row.get("chance_of_playing_next_round")
    if status == "d" and chance is not None and not pd.isna(chance):
        pct = float(chance) / 100.0
        return max(0.25, pct), f"doubtful, {int(float(chance))}% chance of playing"
    if status == "d":
        return 0.7, "doubtful"
    return 1.0, ""


class PriorVerdict:
    """A weak, legible prior. The provider of record until a points model lands.

    The only genuinely informative thing observable about an unplayed season is
    price: FPL prices are set from the previous season's returns and adjusted by
    the market, so within a position-and-price-matched comparator set, price rank
    is a real if noisy ordering. This maps that rank linearly into
    [``PRIOR_FLOOR``, ``PRIOR_CEIL``] and stops.

    It does not fit coefficients, because there is nothing to fit them on yet,
    and a fitted-looking number with no fit behind it is worse than an obvious
    prior. Once results exist it blends in points-per-game, still shrunk hard
    toward the set median.
    """

    name = "prior"
    version = "v1"

    card = ModelCard(
        name="idea-prior-v1",
        approach=(
            "Monotone map from the subject's price rank within its own frozen "
            "comparator set, blended with shrunk points-per-game once results "
            "exist, multiplied by an availability factor."
        ),
        baseline="0.5 for every idea (a coin flip)",
        metric="Brier score against realised idea outcomes",
        score=None,
        baseline_score=None,
        notes=(
            "UNSCORED. No gameweek of 2026-27 had finalised when this was written, "
            "so there are no resolved ideas to score it on. `fpl idea review` "
            "reports its Brier score as soon as there are, and it should be "
            "replaced by SimulationVerdict the moment a PointsModel is registered.",
            "Deliberately capped at 0.20/0.80: a price and a status flag do not "
            "support a stronger claim than that.",
        ),
    )

    def assess(self, idea: Idea, snapshot: Snapshot) -> tuple[float, str]:
        players = snapshot.players(str(idea.season))
        if players.empty or idea.subject_code is None:
            return 0.5, "no player universe at this instant; returning a coin flip"

        subj_rows = players[players["code"] == int(idea.subject_code)]
        subject = subj_rows.iloc[0] if not subj_rows.empty else None
        if subject is None:
            return 0.5, f"{idea.subject_name} not in the {idea.season} player list at this instant"

        codes, _ = comparator_set(
            players, subject, idea.comparator, comparator_code=idea.comparator_code
        )
        peers = players[players["code"].isin(codes)]

        parts: list[str] = []
        p_outscore = 0.5

        if not peers.empty and "price_tenths" in peers.columns:
            prices = peers["price_tenths"].astype(float).to_numpy()
            mine = float(subject["price_tenths"])
            rank = float((prices < mine).sum() + 0.5 * (prices == mine).sum()) / len(prices)
            p_outscore = PRIOR_FLOOR + (PRIOR_CEIL - PRIOR_FLOOR) * rank
            parts.append(
                f"£{mine / 10:.1f}m ranks at the {rank:.0%} mark of the "
                f"{len(prices)}-player comparator set"
            )

        history = player_history(snapshot, str(idea.season))
        if not history.empty:
            blended, note = self._blend_form(history, int(idea.subject_code), codes, p_outscore)
            if note:
                p_outscore, extra = blended, note
                parts.append(extra)

        mult, avail_note = _availability_multiplier(subject)
        if mult < 1.0:
            p_outscore *= mult
            parts.append(avail_note)

        p_outscore = float(min(max(p_outscore, 0.02), 0.95))
        p_true = 1.0 - p_outscore if idea.kind is IdeaKind.FADE else p_outscore
        direction = "under" if idea.kind is IdeaKind.FADE else "out"
        rationale = (
            f"prior only (no points model registered): "
            f"P({idea.subject_name} {direction}scores {idea.comparator_label}) "
            f"= {p_outscore:.0%}. " + "; ".join(parts) if parts else "prior only, no signal available"
        )
        return p_true, rationale

    @staticmethod
    def _blend_form(
        history: pd.DataFrame, code: int, peer_codes: list[int], prior: float
    ) -> tuple[float, str]:
        """Shrink toward the prior by games played. Returns (p, note)."""
        mine = history[history["code"] == code]
        peers = history[history["code"].isin(peer_codes)]
        if mine.empty or peers.empty:
            return prior, ""
        games = int(mine.iloc[0]["games"])
        if games == 0:
            return prior, ""
        my_ppg = float(mine.iloc[0]["season_ppg"])
        peer_ppg = peers["season_ppg"].astype(float).to_numpy()
        rank = float((peer_ppg < my_ppg).sum() + 0.5 * (peer_ppg == my_ppg).sum()) / len(peer_ppg)
        signal = PRIOR_FLOOR + (PRIOR_CEIL - PRIOR_FLOOR) * rank
        # Half weight on form only once ~5 games have been played; before that the
        # sample is smaller than the noise in a single haul.
        w = games / (games + 5.0)
        return (1 - w) * prior + w * signal, (
            f"{my_ppg:.1f} ppg over {games} games ranks at the {rank:.0%} mark of the set "
            f"(weighted {w:.0%} against price)"
        )


class SimulationVerdict:
    """The real provider: counts simulation draws in which the thesis holds.

    This is the only structurally correct way to answer the question. "Does X
    beat the median captain" is a statement about a *joint* distribution: X and
    the comparator set share fixtures, opponents and clean sheets, and a method
    that compares marginal expectations independently will systematically
    misprice exactly the differential calls the user is asking about. So the
    comparison is done inside the sample, draw by draw, using the correlated
    :class:`~fpl_edge.models.contracts.PointsSample` the contract already
    guarantees.

    Constructed with a points model and optionally an ownership model; with the
    latter the captaincy comparator uses real captaincy share instead of the
    ownership proxy. Not registered by default -- :func:`default_provider` wires
    it only when a model is passed in, so no report can silently show prior
    numbers under a simulation label.
    """

    name = "simulation"

    def __init__(self, points_model, ownership_model=None, *, n_sims: int = 10_000, seed: int = 0):
        self.points = points_model
        self.ownership = ownership_model
        self.n_sims = n_sims
        self.seed = seed
        self.version = getattr(getattr(points_model, "card", None), "name", "unknown")
        self.card = getattr(points_model, "card", None) or ModelCard(
            name="simulation-verdict",
            approach="P(thesis) counted over correlated point samples",
            baseline="prior-v1",
            metric="Brier score against realised idea outcomes",
        )

    def assess(self, idea: Idea, snapshot: Snapshot) -> tuple[float, str]:
        season = str(idea.season)
        players = snapshot.players(season)
        if idea.subject_code is None or players.empty:
            raise ValueError("simulation verdict needs a resolved subject and a player universe")
        subj_rows = players[players["code"] == int(idea.subject_code)]
        subject = subj_rows.iloc[0] if not subj_rows.empty else None

        share = None
        if self.ownership is not None and idea.comparator is Comparator.MEDIAN_CAPTAIN:
            share = self.ownership.forecast(snapshot, idea.season, idea.gw)
        codes, _ = comparator_set(
            players, subject, idea.comparator,
            comparator_code=idea.comparator_code, captaincy_share=share,
        )
        if not codes:
            raise ValueError(f"comparator {idea.comparator} resolved to an empty set")

        subject_total: np.ndarray | None = None
        peer_total: np.ndarray | None = None
        for gw in idea.gw_range:
            sample = self.points.simulate(
                snapshot, idea.season, gw, n_sims=self.n_sims, seed=self.seed + int(gw)
            )
            index = {int(c): i for i, c in enumerate(sample.codes)}
            if int(idea.subject_code) not in index:
                raise ValueError(f"{idea.subject_name} absent from the GW{gw} sample")
            s = sample.points[index[int(idea.subject_code)], :].astype(float)
            rows = [index[c] for c in codes if c in index]
            if not rows:
                raise ValueError(f"comparator set absent from the GW{gw} sample")
            # Median WITHIN each draw, not the median of the marginals: the
            # comparator is itself a random variable and its spread is part of
            # the answer.
            m = np.median(sample.points[rows, :].astype(float), axis=0)
            subject_total = s if subject_total is None else subject_total + s
            peer_total = m if peer_total is None else peer_total + m

        assert subject_total is not None and peer_total is not None
        wins = float((subject_total > peer_total).mean())
        pushes = float((subject_total == peer_total).mean())
        p_true = (1.0 - wins - pushes) if idea.kind is IdeaKind.FADE else wins
        direction = "under" if idea.kind is IdeaKind.FADE else "out"
        rationale = (
            f"{self.n_sims:,} correlated draws over {idea.window_label}: "
            f"{idea.subject_name} {direction}scores {idea.comparator_label} in "
            f"{p_true:.1%} of them (mean {subject_total.mean():.1f} vs "
            f"{peer_total.mean():.1f}, {pushes:.1%} ties). Provider {self.version}."
        )
        return float(p_true), rationale


class TimeBounded:
    """Run a provider against a wall-clock budget, falling back if it overruns.

    A hung verdict is indistinguishable to the user from a broken inbox, and the
    inbox only works if it is used one-handed during a match. The fallback is
    recorded as ``degraded=True`` on the row, so a slow week is visible in the
    data rather than silently changing what the numbers mean.
    """

    def __init__(self, primary: VerdictProvider, fallback: VerdictProvider, *, budget_s: float = 8.0):
        self.primary = primary
        self.fallback = fallback
        self.budget_s = budget_s
        self.name = primary.name
        self.version = primary.version
        self.card = primary.card

    def assess_with_fallback(self, idea: Idea, snapshot: Snapshot) -> tuple[float, str, bool, str, str]:
        """Returns (p, rationale, degraded, provider_name, provider_version)."""
        try:
            with cf.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(self.primary.assess, idea, snapshot)
                p, rationale = fut.result(timeout=self.budget_s)
            return p, rationale, False, self.primary.name, self.primary.version
        except cf.TimeoutError:
            reason = f"{self.primary.name} exceeded its {self.budget_s:g}s budget"
        except Exception as exc:  # noqa: BLE001 - any model failure must not lose the idea
            reason = f"{self.primary.name} failed: {type(exc).__name__}: {exc}"
        p, rationale = self.fallback.assess(idea, snapshot)
        return p, f"DEGRADED ({reason}). {rationale}", True, self.fallback.name, self.fallback.version


def confidence_of(provider_name: str, degraded: bool, p: float) -> str:
    """Confidence is a property of the provider, not of how extreme p is.

    Stated explicitly because the opposite convention is the usual one and is
    backwards: a prior that returns 0.80 is not more trustworthy than one that
    returns 0.55, it is just further from the middle.
    """
    if degraded or provider_name == "prior":
        return "low"
    return "high" if abs(p - 0.5) > 0.15 else "medium"


def issue(
    idea: Idea,
    snapshot: Snapshot,
    provider: VerdictProvider | TimeBounded,
    *,
    now: dt.datetime | None = None,
) -> Verdict:
    """Compute and time a verdict. Never raises: a failed model still yields a row."""
    started = time.perf_counter()
    issued = now or dt.datetime.now(UTC)
    if isinstance(provider, TimeBounded):
        p, rationale, degraded, name, version = provider.assess_with_fallback(idea, snapshot)
    else:
        try:
            p, rationale = provider.assess(idea, snapshot)
            degraded = False
        except Exception as exc:  # noqa: BLE001
            p, rationale, degraded = 0.5, f"verdict unavailable: {type(exc).__name__}: {exc}", True
        name, version = provider.name, provider.version
    if math.isnan(p):
        p, degraded, rationale = 0.5, True, f"provider returned NaN. {rationale}"
    p = float(min(max(p, 0.0), 1.0))
    return Verdict(
        idea_id=idea.idea_id,
        issued_utc=issued,
        provider=name,
        provider_version=version,
        stance=_stance(p),
        p_thesis_true=p,
        confidence=confidence_of(name, degraded, p),
        rationale=rationale,
        degraded=degraded,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


def default_provider(points_model=None, ownership_model=None, *, budget_s: float = 8.0):
    """The provider the inbox uses unless told otherwise.

    With no points model this is the prior alone. With one it is the simulation,
    time-bounded, with the prior underneath. That single call site is the whole
    integration surface for the models team.
    """
    prior = PriorVerdict()
    if points_model is None:
        return prior
    return TimeBounded(SimulationVerdict(points_model, ownership_model), prior, budget_s=budget_s)
