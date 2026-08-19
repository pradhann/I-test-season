"""The field: a distribution over rival *squads*, not over rival *scores*.

This module is the reason the engine exists.

The naive way to estimate P(rank < k) is to model my score as a distribution,
model the field's score as another distribution, and compare them. That is
wrong, and wrong in a way that flips decisions rather than merely blurring
them. My score and the field's score share almost all of their randomness: if
70% of managers own Haaland and I own Haaland, then a Haaland blank moves me
and the field together and costs me nothing. Independent sampling prices that
blank as a disaster. The error is largest exactly where the decisions are
hardest -- captaincy and differentials.

The fix is structural rather than a correlation parameter bolted on afterwards.
We sample ``n_rivals`` explicit 15-man squads from the ownership forecast, then
score *my squad and every rival squad against the same Monte Carlo draw of
player points*. Cov(my score, field score) is then an emergent property of
squad overlap. Nothing needs to be estimated, and it is automatically right for
any squad, including ones no one has thought of yet.

Three further pieces of structure matter:

* **Fixed-size sampling.** A rival must own exactly 2 GKP / 5 DEF / 5 MID /
  3 FWD. Independent Bernoulli draws per player give squads of random size and
  understate the negative correlation between owning one premium and another
  (a budget you spend on Haaland is a budget you cannot spend on Salah).
  Madow systematic sampling gives exactly the target inclusion probabilities
  *and* a fixed sample size.
* **Persistent manager skill.** If every rival redrew an independent squad each
  gameweek, season totals would concentrate by the central limit theorem and
  the gap between the mean manager and the top 10k would collapse to a fraction
  of its real size. Each rival therefore carries a latent skill that persists
  all season and tilts their selection toward high-expected-points players.
* **Squad persistence.** Rivals reuse their uniform random draws across
  gameweeks, so a rival's squad changes only as fast as ownership does, with an
  explicit churn rate on top for transfers.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as _field

import numpy as np
import pandas as pd

from fpl_edge.sim.squad import (
    SQUAD_BY_POSITION,
    SQUAD_SIZE,
    VALID_FORMATIONS,
    XI_SIZE,
    PlayerUniverse,
    apply_autosubs,
)

#: The API's ``total_players`` at the GW1 deadline snapshot. Still growing; the
#: rules registry flags it as not-final, so it is a default, not a constant.
DEFAULT_FIELD_SIZE = 5_896_644

_POS_GROUPS: tuple[int, ...] = (1, 2, 3, 4)


@dataclass(frozen=True, slots=True)
class FieldConfig:
    """Knobs for the field model. Every one of them is a modelling claim."""

    n_rivals: int = 10_000
    field_size: int = DEFAULT_FIELD_SIZE
    #: SD of the latent per-manager skill. Fixed at 1; ``skill_tilt`` carries scale.
    skill_sd: float = 1.0
    #: Skewness shape of the latent skill. Negative gives the long *left* tail
    #: that real FPL has -- a large mass of managers who stop paying attention,
    #: and a top end that is compressed because everyone good reads the same
    #: information. A symmetric skill distribution makes the top 10k far too
    #: far above the mean.
    skill_skew: float = -4.0
    #: A minority of managers play a much higher-variance game (heavy
    #: differentials, aggressive chip timing). Without them the top 1k sits far
    #: too close to the top 10k, because a purely Gaussian skill distribution
    #: has no room above three sigma.
    heavy_fraction: float = 0.12
    heavy_scale: float = 2.2
    #: How strongly rival selection is *stratified* by expected points, in
    #: [0, 1]. This is the template-clustering knob and it turned out to be the
    #: single most important field parameter.
    #:
    #: Sampling squads independently at the correct marginal ownership produces
    #: a field that is far more diverse than the real one: the top 10k would sit
    #: ~360 points above the mean rather than the ~310 that is actually
    #: observed. Real managers do not pick independently -- they all read the
    #: same information and converge on the same core, so a manager who owns one
    #: premium tends to own the others too. Ordering the systematic sample by
    #: expected points stratifies each rival's squad across the quality
    #: distribution, which reproduces that clustering while leaving every
    #: player's marginal ownership exactly as forecast. 0 is independent
    #: selection, 1 is fully stratified.
    template_alignment: float = 0.60
    #: How hard skill tilts squad selection toward high-xP players. Calibrated
    #: against the published rank-vs-points ladder; see docs/models/simulator.md.
    #: It is small, and that is itself a finding: most of the observed spread
    #: between managers is squad-selection luck, not persistent skill, and a
    #: field model with a large skill term puts the top 10k hundreds of points
    #: further from the mean than it really is.
    skill_tilt: float = 0.03
    #: Same, for captaincy choice.
    captain_skill_tilt: float = 0.35
    #: Fraction of rivals who re-roll their selection randomness each gameweek.
    churn: float = 0.10
    #: How many rivals get the exact autosub engine run on them; their mean
    #: per-simulation credit is applied to the whole field.
    autosub_sample: int = 256
    #: Iterations of proportional fitting used to make realised inclusion
    #: probabilities match the ownership forecast after the skill tilt.
    ipf_iters: int = 6
    #: Rivals used to fit the proportional-fitting offsets (they do not depend on M).
    ipf_subsample: int = 1_500
    seed: int = 20_260_821
    rival_chunk: int = 2_000


@dataclass(frozen=True)
class FieldSquads:
    """One gameweek's worth of sampled rival squads."""

    slots: np.ndarray        # (M, 15) universe indices, 11 starters then bench
    slot_pos: np.ndarray     # (M, 15) element types
    captain_slot: np.ndarray  # (M,)
    vice_slot: np.ndarray     # (M,)

    @property
    def n_rivals(self) -> int:
        return int(self.slots.shape[0])

    def ownership_realised(self, n_players: int) -> np.ndarray:
        """Realised fraction of rivals owning each player."""
        counts = np.bincount(self.slots.ravel(), minlength=n_players)
        return counts / self.n_rivals

    def start_share(self, n_players: int) -> np.ndarray:
        counts = np.bincount(self.slots[:, :XI_SIZE].ravel(), minlength=n_players)
        return counts / self.n_rivals

    def captain_share(self, n_players: int) -> np.ndarray:
        picks = self.slots[np.arange(self.n_rivals), self.captain_slot]
        return np.bincount(picks, minlength=n_players) / self.n_rivals


def _skew_normal(rng: np.random.Generator, n: int, shape: float) -> np.ndarray:
    """Standardised skew-normal draws (mean 0, sd 1) with the given shape."""
    if abs(shape) < 1e-9:
        return rng.standard_normal(n)
    d = shape / np.sqrt(1.0 + shape ** 2)
    u0, v = rng.standard_normal(n), rng.standard_normal(n)
    z = d * np.abs(u0) + np.sqrt(1.0 - d ** 2) * v
    mean = d * np.sqrt(2.0 / np.pi)
    sd = np.sqrt(1.0 - 2.0 * d ** 2 / np.pi)
    return (z - mean) / sd


def _clip_to_unit(pi: np.ndarray, target_sum: float, iters: int = 6) -> np.ndarray:
    """Scale rows to sum to ``target_sum`` with every entry in [0, 1].

    Madow sampling requires inclusion probabilities that are simultaneously
    valid probabilities and a valid fixed sample size. Simple normalisation can
    push a heavily-owned premium above 1, so the excess is redistributed over
    the players that are not yet saturated.
    """
    pi = np.array(pi, dtype=np.float64, copy=True)
    pi = np.clip(pi, 1e-12, None)
    pi *= target_sum / pi.sum(axis=1, keepdims=True)
    for _ in range(iters):
        over = pi > 1.0
        if not over.any():
            break
        excess = (pi - 1.0)[over].sum() if pi.ndim == 1 else np.where(over, pi - 1.0, 0.0).sum(
            axis=1, keepdims=True
        )
        pi = np.where(over, 1.0, pi)
        free = np.where(over, 0.0, pi)
        denom = free.sum(axis=1, keepdims=True)
        denom = np.where(denom <= 0, 1.0, denom)
        pi = np.where(over, 1.0, pi + excess * free / denom)
    return np.clip(pi, 0.0, 1.0)


def _madow(pi: np.ndarray, u: np.ndarray, perm: np.ndarray) -> np.ndarray:
    """Madow systematic PPS sampling, vectorised over rivals.

    ``pi`` is ``(M, P)`` with each row summing to an integer ``n``. Returns
    ``(M, n)`` column indices. First-order inclusion probabilities are exactly
    ``pi``; the random permutation per row keeps the induced joint inclusion
    probabilities from depending on an arbitrary player ordering.
    """
    m = pi.shape[0]
    n = round(float(pi[0].sum()))
    rows = np.arange(m)[:, None]
    pi_perm = pi[rows, perm]
    csum = np.cumsum(pi_perm, axis=1)
    csum[:, -1] = np.maximum(csum[:, -1], float(n))     # guard float drift
    targets = u[:, None] + np.arange(n)[None, :]        # (M, n)
    pick = (csum[:, :, None] > targets[:, None, :]).argmax(axis=1)  # (M, n)
    return perm[rows, pick]


@dataclass
class FieldModel:
    """Samples rival squads and scores them against shared point draws."""

    universe: PlayerUniverse
    config: FieldConfig = _field(default_factory=FieldConfig)

    def __post_init__(self) -> None:
        rng = np.random.default_rng(self.config.seed)
        m = self.config.n_rivals
        skill = _skew_normal(rng, m, self.config.skill_skew)
        heavy = rng.random(m) < self.config.heavy_fraction
        skill = np.where(heavy, skill * self.config.heavy_scale, skill)
        self.skill = skill * self.config.skill_sd
        # Common random numbers: one uniform start and one column permutation
        # per rival per position group, reused every gameweek so that a rival's
        # squad drifts with ownership rather than being redrawn from scratch.
        self._u: dict[int, np.ndarray] = {}
        self._eps: dict[int, np.ndarray] = {}
        self._pos_index: dict[int, np.ndarray] = {}
        for p in _POS_GROUPS:
            cols = np.flatnonzero(self.universe.position == p)
            self._pos_index[p] = cols
            self._u[p] = rng.random(m)
            self._eps[p] = rng.standard_normal((m, len(cols)))
        self._u_cap = rng.random(m)
        self._churn_rng = np.random.default_rng(self.config.seed + 1)
        self._alpha_cache: dict = {}

    # -- squad sampling ------------------------------------------------------

    def target_ownership(self, ownership: np.ndarray) -> np.ndarray:
        """The inclusion probabilities the sampler will actually reproduce.

        The argument is **ownership** -- the share of managers who hold the
        player -- not effective ownership. EO counts a captain twice and drops
        benched owners, so it cannot be a squad-inclusion probability; passing
        it here silently clips every captaincy magnet to 1.0. See
        ``engine._align_ownership``.

        A squad holds exactly 2/5/5/3 by position, so ownership *must* sum to
        those counts within each position group. A forecast that does not is
        renormalised here rather than silently; :meth:`ownership_renormalisation`
        reports how far it had to move, and a large value means the ownership
        model and the squad rules disagree.
        """
        out = np.zeros_like(ownership, dtype=np.float64)
        for p in _POS_GROUPS:
            cols = self._pos_index[p]
            base = np.clip(ownership[cols], 1e-6, 0.999)
            out[cols] = _clip_to_unit(base[None, :], SQUAD_BY_POSITION[p])[0]
        return out

    def ownership_renormalisation(self, ownership: np.ndarray) -> dict[str, float]:
        target = self.target_ownership(ownership)
        return {
            "max_abs_shift": float(np.abs(target - ownership).max()),
            "mean_abs_shift": float(np.abs(target - ownership).mean()),
            "n_saturated": float(np.sum(np.asarray(ownership) >= 0.999)),
        }

    def _ordering(self, p: int, xp_z: np.ndarray) -> np.ndarray:
        """Per-rival column ordering for the systematic sample.

        A correlated blend of "sorted by expected points" and "shuffled". The
        expected-points component stratifies the sample, so every rival gets a
        spread across the quality distribution rather than, by chance, a squad
        of all-premiums or all-punts. That is what makes the simulated field as
        tightly clustered as the real one.
        """
        cols = self._pos_index[p]
        a = float(np.clip(self.config.template_alignment, 0.0, 1.0))
        key = a * xp_z[cols][None, :] + np.sqrt(max(1.0 - a * a, 0.0)) * self._eps[p]
        return np.argsort(-key, axis=1, kind="stable")

    def _fit_log_alpha(self, p: int, eo: np.ndarray, xp_z: np.ndarray) -> np.ndarray:
        """Proportional-fitting offsets that restore the ownership marginals.

        Fitted on a subsample of rivals (the offsets are a property of the skill
        *distribution*, not of the particular draws) and memoised on the inputs,
        because in a rest-of-season run the ownership forecast usually moves by
        very little from one gameweek to the next.
        """
        key = (p, eo.tobytes(), xp_z.tobytes())
        hit = self._alpha_cache.get(key)
        if hit is not None:
            return hit
        cols = self._pos_index[p]
        n = SQUAD_BY_POSITION[p]
        base = np.clip(eo[cols], 1e-6, 0.999)
        target = _clip_to_unit(base[None, :], n)[0]
        k = min(self.config.ipf_subsample, len(self.skill))
        skill = self.skill[:k]
        tilt = self.config.skill_tilt * np.outer(skill, xp_z[cols])
        log_alpha = np.log(base)[None, :]
        for _ in range(self.config.ipf_iters):
            pi = _clip_to_unit(np.exp(log_alpha + tilt), n)
            realised = pi.mean(axis=0)
            log_alpha = log_alpha + np.log(
                np.clip(target / np.clip(realised, 1e-9, None), 1e-3, 1e3)
            )
        self._alpha_cache[key] = log_alpha
        return log_alpha

    def _inclusion_probabilities(self, p: int, eo: np.ndarray, xp_z: np.ndarray) -> np.ndarray:
        """(M, P_p) per-rival inclusion probabilities for position ``p``.

        The skill tilt is applied in log space and then proportionally fitted so
        that the *average* inclusion probability across rivals reproduces the
        ownership forecast. Without that fit, tilting the field toward good
        players would silently inflate every good player's ownership and the
        simulation would no longer be sampling the field we were given.
        """
        cols = self._pos_index[p]
        n = SQUAD_BY_POSITION[p]
        log_alpha = self._fit_log_alpha(p, eo, xp_z)
        tilt = self.config.skill_tilt * np.outer(self.skill, xp_z[cols])  # (M, Pp)
        return _clip_to_unit(np.exp(log_alpha + tilt), n)

    def sample_squads(
        self,
        ownership: np.ndarray,
        captaincy: np.ndarray,
        expected_points: np.ndarray,
        *,
        gw_offset: int = 0,
    ) -> FieldSquads:
        """Draw ``n_rivals`` squads consistent with the ownership forecast.

        ``ownership``, ``captaincy`` and ``expected_points`` are
        ``(n_players,)`` arrays aligned to the universe. ``ownership`` is the
        squad-inclusion share, **not** effective ownership; ``captaincy`` is the
        share of the whole field captaining the player, so ``captaincy /
        ownership`` is P(captain | owned), which is what the armband draw below
        needs.
        """
        eo = ownership
        m = self.config.n_rivals
        xp_z = _standardise_within_position(expected_points, self.universe.position)
        if gw_offset > 0 and self.config.churn > 0:
            reroll = self._churn_rng.random(m) < self.config.churn
            if reroll.any():
                k = int(reroll.sum())
                for p in _POS_GROUPS:
                    self._u[p][reroll] = self._churn_rng.random(k)
                    self._eps[p][reroll] = self._churn_rng.standard_normal(
                        (k, len(self._pos_index[p]))
                    )
                self._u_cap[reroll] = self._churn_rng.random(k)

        picked: dict[int, np.ndarray] = {}
        for p in _POS_GROUPS:
            pi = self._inclusion_probabilities(p, eo, xp_z)
            local = _madow(pi, self._u[p], self._ordering(p, xp_z))
            picked[p] = self._pos_index[p][local]          # (M, n_p)

        # Fixed column layout: [GK x2, DEF x5, MID x5, FWD x3], each group sorted
        # by expected points descending so "top d defenders" is a slice.
        groups = []
        for p in _POS_GROUPS:
            g = picked[p]
            order = np.argsort(-expected_points[g], axis=1, kind="stable")
            groups.append(np.take_along_axis(g, order, axis=1))
        grid = np.concatenate(groups, axis=1)              # (M, 15)
        xp_grid = expected_points[grid]

        # Best legal formation per rival, from group cumulative sums.
        cs_def = np.cumsum(xp_grid[:, 2:7], axis=1)
        cs_mid = np.cumsum(xp_grid[:, 7:12], axis=1)
        cs_fwd = np.cumsum(xp_grid[:, 12:15], axis=1)
        totals = np.stack(
            [cs_def[:, d - 1] + cs_mid[:, mm - 1] + cs_fwd[:, f - 1] for d, mm, f in VALID_FORMATIONS],
            axis=1,
        )
        best = np.argmax(totals, axis=1)
        form = np.array(VALID_FORMATIONS)[best]            # (M, 3)

        rank_in_group = np.concatenate(
            [np.arange(2), np.arange(5), np.arange(5), np.arange(3)]
        )[None, :]
        group_id = np.concatenate([np.full(2, 1), np.full(5, 2), np.full(5, 3), np.full(3, 4)])
        starts = np.zeros((m, SQUAD_SIZE), dtype=bool)
        starts[:, 0] = True
        starts[:, 2:7] = rank_in_group[:, 2:7] < form[:, 0:1]
        starts[:, 7:12] = rank_in_group[:, 7:12] < form[:, 1:2]
        starts[:, 12:15] = rank_in_group[:, 12:15] < form[:, 2:3]

        # Canonical slot order: starters (group order), bench GK, bench outfield
        # by expected points descending -- the order autosubs consume.
        key = np.where(
            starts,
            -1e9 + np.arange(SQUAD_SIZE)[None, :],
            np.where(group_id[None, :] == 1, -1e8, -xp_grid),
        )
        order = np.argsort(key, axis=1, kind="stable")
        slots = np.take_along_axis(grid, order, axis=1)
        slot_pos = self.universe.position[slots]

        # Captaincy: sample among the XI with weights captaincy_share / ownership,
        # tilted by skill so stronger managers captain the higher-xP option.
        w = captaincy[slots[:, :XI_SIZE]] / np.clip(eo[slots[:, :XI_SIZE]], 1e-4, None)
        w = np.clip(w, 1e-9, None) * np.exp(
            self.config.captain_skill_tilt * self.skill[:, None] * xp_z[slots[:, :XI_SIZE]]
        )
        cw = np.cumsum(w, axis=1)
        captain_slot = (cw >= (self._u_cap * cw[:, -1])[:, None]).argmax(axis=1)
        xi_xp = expected_points[slots[:, :XI_SIZE]].copy()
        xi_xp[np.arange(m), captain_slot] = -np.inf
        vice_slot = np.argmax(xi_xp, axis=1)
        return FieldSquads(slots=slots, slot_pos=slot_pos,
                           captain_slot=captain_slot, vice_slot=vice_slot)

    # -- scoring -------------------------------------------------------------

    def score(
        self,
        squads: FieldSquads,
        points: np.ndarray,
        minutes: np.ndarray | None,
        *,
        captain_multiplier: float = 2.0,
    ) -> np.ndarray:
        """Score every rival against a shared ``(n_players, n_sims)`` draw.

        Returns ``(n_rivals, n_sims)`` float32. This is the function whose reuse
        of ``points`` across me and the field is the entire correlation model.
        """
        m = squads.n_rivals
        s = points.shape[1]
        out = np.empty((m, s), dtype=np.float32)
        rows_all = np.arange(m)
        for lo in range(0, m, self.config.rival_chunk):
            hi = min(lo + self.config.rival_chunk, m)
            sl = squads.slots[lo:hi, :XI_SIZE]
            pts = points[sl]                                     # (c, 11, S)
            total = pts.sum(axis=1, dtype=np.float32)
            cap = squads.slots[rows_all[lo:hi], squads.captain_slot[lo:hi]]
            vc = squads.slots[rows_all[lo:hi], squads.vice_slot[lo:hi]]
            cap_pts = points[cap]
            if minutes is None:
                extra = cap_pts
            else:
                cap_played = minutes[cap] > 0
                extra = np.where(cap_played, cap_pts, points[vc])
            out[lo:hi] = total + (captain_multiplier - 1.0) * extra
        if minutes is not None and self.config.autosub_sample > 0:
            out += self.autosub_credit(squads, points, minutes)[None, :]
        return out

    def autosub_credit(
        self, squads: FieldSquads, points: np.ndarray, minutes: np.ndarray
    ) -> np.ndarray:
        """Mean per-simulation points the field gains from autosubs.

        Estimated exactly, on a subsample of rivals, and then applied to the
        whole field. Running the full autosub engine on 10,000 rivals is a
        ~40x cost increase for second-order cross-rival variance; running it on
        nobody biases my squad's rank upward by roughly a point a gameweek,
        which over a season is several hundred thousand rank places.
        """
        n = min(self.config.autosub_sample, squads.n_rivals)
        rng = np.random.default_rng(self.config.seed + 7)
        pick = rng.choice(squads.n_rivals, size=n, replace=False)
        sl = squads.slots[pick]
        pts = points[sl]
        mins = minutes[sl]
        _, with_subs = apply_autosubs(pts, mins, squads.slot_pos[pick])
        without = pts[:, :XI_SIZE, :].sum(axis=1)
        return (with_subs - without).mean(axis=0).astype(np.float32)


def _standardise_within_position(x: np.ndarray, position: np.ndarray) -> np.ndarray:
    """z-score expected points inside each position so the tilt is comparable."""
    out = np.zeros_like(x, dtype=np.float64)
    for p in _POS_GROUPS:
        mask = position == p
        v = x[mask]
        sd = v.std()
        out[mask] = (v - v.mean()) / (sd if sd > 1e-9 else 1.0)
    return out


def ownership_from_frame(
    df: pd.DataFrame, universe: PlayerUniverse, *, column: str = "eo_overall"
) -> np.ndarray:
    """Align an OWNERSHIP_COLUMNS frame to the universe ordering."""
    s = pd.Series(df[column].to_numpy(), index=df["code"].to_numpy())
    return s.reindex(universe.codes).fillna(0.0).to_numpy(dtype=np.float64)
