"""Per-player objective coefficients for ``ObjectiveMode.RANK_MV``.

This is the seam between the closed forms in :mod:`fpl_edge.rank.policy` and the
MILP. F2's certainty-equivalent score is

    ``score = m_1 + theta * s_1^2``

and the MILP needs it **separated across players**, because a MILP objective is
a sum of per-variable coefficients. Two things make that separation legitimate,
and one thing it costs:

**The pace increment drops out.** ``m_1`` is expected points *minus the pace
increment*, but the pace increment is a property of the field in that gameweek,
not of my choices: it is the same constant whichever fifteen I pick. Subtracting
a constant from every coefficient shifts the objective and never the argmax, so
the coefficients below carry ``mu_p`` (expected points) rather than
``mu_p - pace``. The reported objective value is therefore in "points above
nothing", not "points above the bar", and :meth:`RankCoefficients.pace_note`
says so on every plan.

**The ownership term is the ``(1 - 2 * share)`` factor.** Owning player ``p`` at
cohort share ``share_p`` changes my variance *against the bar* by
``(1 - 2 share_p) sigma_p^2`` to first order -- the same algebra as §4's
captaincy rule, applied to squad membership instead of the armband. Above 50%
cohort share, adding the player *reduces* relative variance: you are buying the
bar, not betting against it.

**What it costs: cross-player covariance is not priced per pair.** The exact
relative variance of a squad is ``(w - e)^T Sigma_x (w - e)``, quadratic in the
ownership vector, and putting that in a MILP makes it an MIQP over ~600 binaries.
The F2 approximation (§2) is to price the *diagonal* per player and let
**covariance enter through ``Sigma`` inside ``theta``** -- ``theta`` is computed
from the squad-level effective ``s``, which :func:`fpl_edge.rank.state.deficit_moments`
measures from paired draws *with* every covariance already inside it. So the
covariance channel is present, at squad resolution, in the price of variance;
it is absent at pair resolution, in the allocation of variance. §2 records the
consequence: the approximation is local -- valid for candidates near the
incumbent -- and §8.2 is why :mod:`fpl_edge.rank.validate` exists, since F1 "is
the only layer that prices squad-level covariance exactly".

That trade is deliberate and it is the study's own recommendation: F2 is "the
only formulation that is simultaneously cheap enough to run in the argmax loop
and state-aware enough to capture the 9-19pp adaptivity prize" (§8.1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from fpl_edge.opt.interfaces import RankInputsUnavailableError as _RankInputsUnavailableError
from fpl_edge.rank.policy import theta
from fpl_edge.rank.state import RankState

#: Columns a share table must carry, one row per (code, gw).
SHARE_COLUMNS = ("code", "gw", "own_share", "captain_share")

#: Provenance for shares taken from ownership marginals in ``fact_player_state``
#: rather than from observed rival squads. Mirrors
#: ``fpl_edge.models.field.contracts.PROVENANCE_MARGINALS_PRIOR``: before GW1 no
#: picks are public, so this is the only thing available and the label says so.
PROVENANCE_OWNERSHIP_MARGINALS = "ownership_marginals:prior"


#: Re-exported so callers can catch it without importing the optimiser. Defined
#: in :mod:`fpl_edge.opt.interfaces` next to its sibling
#: :class:`~fpl_edge.opt.interfaces.RankUtilityUnavailableError`, because the
#: optimiser is what raises it.
RankInputsUnavailableError = _RankInputsUnavailableError


@dataclass(frozen=True)
class RankCoefficients:
    """``mu``, ``sigma^2`` and cohort shares, keyed by player code.

    Keyed by *code*, not by row index, so that
    :meth:`~fpl_edge.opt.problem.HorizonProblem.prune` reordering or shrinking
    the universe cannot silently misalign a coefficient with a player. Alignment
    happens once, in :meth:`align`, against whatever problem is actually solved.
    """

    #: The state these coefficients were priced at. Carried, not recomputed:
    #: a plan must be able to report the posture it was solved under.
    state: RankState
    #: ``theta(D, tau)``, already capped if a cap was requested.
    theta: float
    codes: np.ndarray            # (n,) int64
    gws: tuple[int, ...]
    mu: np.ndarray               # (n, T) expected points
    variance: np.ndarray         # (n, T) per-player points variance
    own_share: np.ndarray        # (n, T) cohort squad-inclusion share
    captain_share: np.ndarray    # (n, T) cohort captaincy share
    provenance: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        n, t = len(self.codes), len(self.gws)
        for name in ("mu", "variance", "own_share", "captain_share"):
            arr = getattr(self, name)
            if arr.shape != (n, t):
                raise ValueError(f"{name} has shape {arr.shape}, expected {(n, t)}")
        if len(set(self.codes.tolist())) != n:
            raise ValueError("duplicate player codes in the coefficient table")
        if (self.variance < 0.0).any():
            raise ValueError("negative variance in the coefficient table")
        for name in ("own_share", "captain_share"):
            arr = getattr(self, name)
            if (arr < 0.0).any() or (arr > 1.0).any():
                raise ValueError(f"{name} must be a probability in [0, 1]")
        if not self.provenance:
            raise ValueError("rank coefficients must declare where their shares came from")

    # -- the two coefficient matrices the MILP consumes ----------------------

    def lineup_coefficients(self) -> np.ndarray:
        """``mu_p + theta (1 - 2 own_share_p) sigma_p^2`` -- points that count.

        Applied to the starting XI, the autosub-weighted bench and the
        Bench Boost uplift: every variable whose meaning is "this player's
        points enter my total once".
        """
        return self.mu + self.theta * (1.0 - 2.0 * self.own_share) * self.variance

    def captain_coefficients(self) -> np.ndarray:
        """``mu_p + theta (1 - 2 captain_share_p) sigma_p^2`` -- the armband (§4).

        The **flipped-variance** term. It uses ``captain_share`` and not
        ``own_share`` because the armband is compared against the cohort's
        *armbands*: the relevant relative gain is ``x_i - sum_j c_j x_j``, so
        the crossover sits at 50% of the cohort's captaincy, which is a much
        rarer and much more concentrated distribution than ownership.

        Carried by the captaincy variable specifically, so that a player can be
        worth owning and not worth captaining (or the reverse) without the
        objective having to express that as a constraint.
        """
        return self.mu + self.theta * (1.0 - 2.0 * self.captain_share) * self.variance

    # -- alignment -----------------------------------------------------------

    def align(self, problem: object) -> tuple[np.ndarray, np.ndarray]:
        """``(lineup, captain)`` coefficient matrices in ``problem``'s row order.

        Raises rather than filling a default for any player or gameweek the
        table does not cover. A missing coefficient is a missing forecast, and
        the correct response to a missing forecast is to stop.
        """
        want_codes = [int(p.code) for p in problem.players]  # type: ignore[attr-defined]
        want_gws = [int(g) for g in problem.gws]             # type: ignore[attr-defined]
        row_of = {int(c): i for i, c in enumerate(self.codes)}
        col_of = {int(g): j for j, g in enumerate(self.gws)}

        missing_p = [c for c in want_codes if c not in row_of]
        if missing_p:
            raise ValueError(
                f"rank coefficients cover no player code {missing_p[:5]} "
                f"({len(missing_p)} missing). Build them from the same universe "
                "the problem was built from."
            )
        missing_g = [g for g in want_gws if g not in col_of]
        if missing_g:
            raise ValueError(f"rank coefficients cover no gameweek {missing_g}")

        rows = np.array([row_of[c] for c in want_codes], dtype=np.int64)
        cols = np.array([col_of[g] for g in want_gws], dtype=np.int64)
        take = np.ix_(rows, cols)
        return self.lineup_coefficients()[take], self.captain_coefficients()[take]

    def pace_note(self) -> str:
        """The sentence a plan carries so its objective value is not misread."""
        return (
            "RANK_MV objective is in points, not points-above-the-bar: the pace "
            "increment is player-independent and drops out of the argmax, so it "
            "is not subtracted from the coefficients. Differences between plans "
            "are comparable; the level is not a margin over the top-10k bar."
        )

    def covariance_note(self) -> str:
        """The F2 approximation, stated on every plan that uses it."""
        return (
            "F2 approximation: cross-player covariance enters through Sigma "
            "inside theta (squad-level effective s, measured from paired draws), "
            "NOT as per-pair terms in the objective. Pair-resolution covariance "
            "is priced only by the F1 paired-CRN validator."
        )

    def describe(self) -> str:
        return (
            f"theta={self.theta:+.5f}/pt^2 at {self.state.describe()}; "
            f"shares from {self.provenance}"
        )


def build_rank_coefficients(
    problem: object,
    state: RankState,
    *,
    variance: np.ndarray,
    own_share: np.ndarray,
    captain_share: np.ndarray,
    provenance: str,
    mu: np.ndarray | None = None,
    theta_cap: float | None = None,
    notes: Sequence[str] = (),
) -> RankCoefficients:
    """Price a problem's universe at ``state``.

    ``variance`` is the per-(player, gameweek) variance of that player's points.
    It is an *own* variance -- the ``(1 - 2 share)`` factor is what converts it
    into a contribution to variance against the bar -- and it comes from the
    simulator's draws or from a points model that emits second moments. There is
    no default: a variance the caller did not supply is a variance nobody
    measured.

    ``mu`` defaults to ``problem.xpts``, which is the same expected-points
    forecast ``EXPECTED_POINTS`` consumes. That is deliberate: running both modes
    on one problem then differs *only* in the rank terms, so the diff between the
    two squads is exactly the value the rank machinery adds and nothing else.
    """
    codes = np.array([int(p.code) for p in problem.players], dtype=np.int64)  # type: ignore[attr-defined]
    gws = tuple(int(g) for g in problem.gws)                                   # type: ignore[attr-defined]
    base_mu = problem.xpts if mu is None else mu                               # type: ignore[attr-defined]
    return RankCoefficients(
        state=state,
        theta=theta(state, cap=theta_cap),
        codes=codes,
        gws=gws,
        mu=np.asarray(base_mu, dtype=np.float64),
        variance=np.asarray(variance, dtype=np.float64),
        own_share=np.asarray(own_share, dtype=np.float64),
        captain_share=np.asarray(captain_share, dtype=np.float64),
        provenance=provenance,
        notes=tuple(notes),
    )


def shares_from_frame(
    problem: object,
    frame: pd.DataFrame,
    *,
    provenance: str,
) -> tuple[np.ndarray, np.ndarray]:
    """``(own_share, captain_share)`` aligned to ``problem``, from a long table.

    The table must carry :data:`SHARE_COLUMNS`. Players present in the problem
    and absent from the table get share 0 -- which is the honest reading for an
    ownership marginal (nobody in the cohort holds him), unlike a missing
    *forecast*, and it keeps a 600-player universe usable against a table that
    only lists owned players.
    """
    missing = set(SHARE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"share table missing columns {sorted(missing)}")
    if not provenance:
        raise ValueError("shares must declare their provenance")

    codes = [int(p.code) for p in problem.players]  # type: ignore[attr-defined]
    gws = [int(g) for g in problem.gws]             # type: ignore[attr-defined]
    own = np.zeros((len(codes), len(gws)), dtype=np.float64)
    cap = np.zeros((len(codes), len(gws)), dtype=np.float64)
    row_of = {c: i for i, c in enumerate(codes)}
    col_of = {g: j for j, g in enumerate(gws)}
    for r in frame.itertuples():
        i = row_of.get(int(r.code))
        j = col_of.get(int(r.gw))
        if i is None or j is None:
            continue
        own[i, j] = float(r.own_share)
        cap[i, j] = float(r.captain_share)
    if (own < 0).any() or (own > 1).any() or (cap < 0).any() or (cap > 1).any():
        raise ValueError("shares must be probabilities in [0, 1]")
    return own, cap


def shares_from_field_samples(
    problem: object,
    samples: Mapping[int, object],
    universe_codes: Sequence[int],
) -> tuple[np.ndarray, np.ndarray, str]:
    """``(own_share, captain_share, provenance)`` from per-gameweek field samples.

    The swap-in seam for :mod:`fpl_edge.models.field`. ``samples`` maps gameweek
    to a :class:`~fpl_edge.models.field.contracts.FieldSample`, whose sampled
    rival squads carry the *joint* structure that ownership marginals throw
    away, and whose ``provenance`` records how much of it was actually observed.

    The returned provenance is the **weakest** label across the sampled
    gameweeks, because a set of coefficients is only as measured as its least
    measured week.
    """
    codes = [int(p.code) for p in problem.players]  # type: ignore[attr-defined]
    gws = [int(g) for g in problem.gws]             # type: ignore[attr-defined]
    uni = [int(c) for c in universe_codes]
    pos_in_uni = {c: i for i, c in enumerate(uni)}

    own = np.zeros((len(codes), len(gws)), dtype=np.float64)
    cap = np.zeros((len(codes), len(gws)), dtype=np.float64)
    labels: list[str] = []
    for j, gw in enumerate(gws):
        sample = samples.get(gw)
        if sample is None:
            raise ValueError(f"no field sample for GW{gw}; refusing to invent one")
        squads = sample.squads                                    # type: ignore[attr-defined]
        own_full = np.asarray(squads.ownership_realised(len(uni)), dtype=np.float64)
        cap_full = np.asarray(squads.captain_share(len(uni)), dtype=np.float64)
        labels.append(str(sample.provenance))                     # type: ignore[attr-defined]
        for i, c in enumerate(codes):
            k = pos_in_uni.get(c)
            if k is None:
                continue
            own[i, j] = own_full[k]
            cap[i, j] = cap_full[k]

    order = [
        PROVENANCE_OWNERSHIP_MARGINALS,
        "hybrid(empirical+marginals)",
        "hybrid(empirical+marginals)+flow_drift",
        "empirical_picks",
        "empirical_picks+flow_drift",
    ]

    def rank_of(label: str) -> int:
        return order.index(label) if label in order else -1

    weakest = min(labels, key=rank_of) if labels else PROVENANCE_OWNERSHIP_MARGINALS
    return own, cap, weakest
