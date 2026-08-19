"""Measuring the user's biases from their own idea history.

Every number in here is computed from the registry. Nothing is asserted. That is
a deliberate constraint and it costs something: with fifteen ideas the honest
answer to "do I chase form?" is usually "there is not enough evidence yet", and
this module says so rather than producing a confident paragraph. A bias report
that always finds a bias is a horoscope.

Each probe is a hypothesis test with a null drawn from the same instant the idea
was had -- ``idea_context`` stores the population base rate alongside the
subject's feature precisely so the comparison is against the world the user was
looking at, not against a convenient constant. The three the user asked for:

* **Form chasing.** Under the null that attention is uncorrelated with recent
  scoring, the form percentile of a chosen player is uniform on [0, 1] and
  averages 0.5. Higher means the highlights are picking the players.
* **Home bias for players they watched.** The probe is whether the subject's
  *most recent match* was at home more often than the population's was. The
  mechanism it is looking for is specific: you watch a home performance, the
  player looks unplayable, you text about them. Testing against a hardcoded 0.5
  would find "bias" in the fixture calendar, so the null is the measured
  population rate.
* **Recency.** Whether the subject hauled in the last couple of gameweeks more
  often than the population did.
* **Club affinity.** The user supports Man Utd, and "he plays for my club" has no
  predictive content whatsoever. This is the one probe that works from GW1: it
  needs only a squad list, not results, so it starts returning an answer while
  the other three are still waiting for a gameweek to finalise.

Four tests on one small dataset is four chances to find something, so p-values
are Holm-corrected and both the raw and adjusted values are reported. The
correction is applied across the probes that actually had data, not across all
four unconditionally, because a probe that could not run is not a test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from fpl_edge.config import USER
from fpl_edge.interfaces.features import RECENT_HAUL_WINDOW
from fpl_edge.interfaces.registry import IdeaRegistry
from fpl_edge.store import Warehouse

try:  # scipy is a hard dependency of the project, but the review must not be
    from scipy.stats import binomtest, norm  # the thing that breaks if it moves.
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _HAVE_SCIPY = False

#: Below this many usable observations, a probe reports its numbers but refuses
#: to call a bias. Twelve is where a 20-percentage-point swing starts being
#: distinguishable from noise at all; it is a floor on honesty, not on power.
MIN_OBSERVATIONS = 12


def _normal_p(z: float) -> float:
    if _HAVE_SCIPY:
        return float(2 * (1 - norm.cdf(abs(z))))
    return float(math.erfc(abs(z) / math.sqrt(2)))


@dataclass(frozen=True, slots=True)
class BiasFinding:
    """One hypothesis test against the user's history."""

    name: str
    question: str
    n: int
    observed: float | None
    expected: float | None
    units: str
    p_value: float | None = None
    p_adjusted: float | None = None
    detail: str = ""

    @property
    def effect(self) -> float | None:
        if self.observed is None or self.expected is None:
            return None
        return self.observed - self.expected

    @property
    def has_evidence(self) -> bool:
        return self.n >= MIN_OBSERVATIONS and self.p_adjusted is not None

    @property
    def significant(self) -> bool:
        return self.has_evidence and (self.p_adjusted or 1.0) < 0.05

    def verdict(self) -> str:
        if self.observed is None or self.n == 0:
            return f"no data yet ({self.detail or 'no ideas carry the needed context'})"
        obs = f"{self.observed:.1%}" if self.units == "rate" else f"{self.observed:.3f}"
        exp = f"{self.expected:.1%}" if self.units == "rate" else f"{self.expected:.3f}"
        head = f"{obs} vs {exp} expected, n={self.n}"
        if self.n < MIN_OBSERVATIONS:
            return (
                f"{head} — NOT ENOUGH EVIDENCE. "
                f"{MIN_OBSERVATIONS - self.n} more usable ideas needed before this "
                "is worth reading."
            )
        if self.p_adjusted is None:
            # Distinct from "too few ideas": here there are plenty, but the base
            # rate is degenerate (nobody in the population has the trait, or
            # everybody does), so there is no test to run rather than a weak one.
            return (
                f"{head} — NO TEST POSSIBLE: the population base rate is "
                f"{exp}, which leaves nothing to be biased relative to."
            )
        direction = "above" if (self.effect or 0) > 0 else "below"
        sig = "SIGNIFICANT" if self.significant else "not significant"
        return (
            f"{head}, {abs(self.effect or 0):.1%} {direction} the base rate "
            f"(p={self.p_value:.3f}, Holm-adjusted p={self.p_adjusted:.3f}) — {sig}"
        )


@dataclass(frozen=True, slots=True)
class Scoreboard:
    """How the ideas actually did, split by whether the user acted."""

    n_total: int = 0
    n_resolved: int = 0
    n_open: int = 0
    n_void: int = 0
    hit_rate: float | None = None
    mean_margin: float | None = None
    acted_n: int = 0
    acted_hit_rate: float | None = None
    unacted_n: int = 0
    unacted_hit_rate: float | None = None
    brier: float | None = None
    baseline_brier: float | None = None
    engine_agreed_hit_rate: float | None = None
    engine_disagreed_hit_rate: float | None = None


@dataclass(frozen=True, slots=True)
class Review:
    """The whole picture: what was thought, what happened, what it says."""

    season: str
    scoreboard: Scoreboard
    findings: tuple[BiasFinding, ...] = ()
    by_kind: pd.DataFrame = field(default_factory=pd.DataFrame)
    ideas: pd.DataFrame = field(default_factory=pd.DataFrame)
    caveats: tuple[str, ...] = ()


def _holm(findings: list[BiasFinding]) -> list[BiasFinding]:
    """Holm-Bonferroni across the probes that actually ran.

    Step-down rather than plain Bonferroni because it is uniformly more powerful
    at the same family-wise error rate, and with this few tests there is no reason
    to give away power. Probes with no p-value are excluded from the family: not
    running a test is not the same as running one and failing.
    """
    live = [(i, f) for i, f in enumerate(findings) if f.p_value is not None]
    if not live:
        return findings
    live.sort(key=lambda pair: pair[1].p_value or 1.0)
    m = len(live)
    out = list(findings)
    running = 0.0
    for rank, (idx, f) in enumerate(live):
        adj = min(1.0, (m - rank) * (f.p_value or 1.0))
        running = max(running, adj)  # enforce monotonicity down the ladder
        out[idx] = BiasFinding(
            name=f.name, question=f.question, n=f.n, observed=f.observed,
            expected=f.expected, units=f.units, p_value=f.p_value,
            p_adjusted=running, detail=f.detail,
        )
    return out


def _form_chasing(df: pd.DataFrame) -> BiasFinding:
    usable = df[df["form_percentile"].notna()]
    n = len(usable)
    q = "Do I pick players because they just scored?"
    if n == 0:
        return BiasFinding(
            "form_chasing", q, 0, None, 0.5, "score",
            detail="no idea has a form percentile yet; needs finalised gameweeks before the idea",
        )
    obs = float(usable["form_percentile"].mean())
    # Under H0 the percentile is Uniform(0,1): mean 1/2, variance 1/12.
    se = math.sqrt((1.0 / 12.0) / n)
    z = (obs - 0.5) / se if se > 0 else 0.0
    return BiasFinding(
        "form_chasing", q, n, obs, 0.5, "score", p_value=_normal_p(z),
        detail=(
            "mean form percentile of the players you picked, against the uniform "
            "0.500 you would get if recent scoring had no pull on your attention"
        ),
    )


def _home_bias(df: pd.DataFrame) -> BiasFinding:
    usable = df[df["last_match_was_home"].notna() & df["pop_home_rate"].notna()]
    n = len(usable)
    q = "Do I pick players I just watched play at home?"
    if n == 0:
        return BiasFinding(
            "home_bias", q, 0, None, None, "rate",
            detail="no idea has a last-match venue recorded yet",
        )
    hits = int(usable["last_match_was_home"].astype(bool).sum())
    base = float(usable["pop_home_rate"].astype(float).mean())
    obs = hits / n
    p = None
    if _HAVE_SCIPY and 0.0 < base < 1.0:
        p = float(binomtest(hits, n, base, alternative="two-sided").pvalue)
    return BiasFinding(
        "home_bias", q, n, obs, base, "rate", p_value=p,
        detail=(
            f"{hits} of {n} of your subjects had last played at home, against the "
            f"{base:.1%} of the eligible player pool that had, measured at the "
            "same instants"
        ),
    )


def _recency(df: pd.DataFrame) -> BiasFinding:
    usable = df[df["pop_recent_haul_rate"].notna()]
    n = len(usable)
    q = f"Do I pick players who hauled in the last {RECENT_HAUL_WINDOW} gameweeks?"
    if n == 0:
        return BiasFinding(
            "recency", q, 0, None, None, "rate",
            detail="no idea has a population haul rate recorded yet",
        )
    recent = usable["gws_since_haul"].notna() & (
        usable["gws_since_haul"].fillna(99) < RECENT_HAUL_WINDOW
    )
    hits = int(recent.sum())
    base = float(usable["pop_recent_haul_rate"].astype(float).mean())
    obs = hits / n
    p = None
    if _HAVE_SCIPY and 0.0 < base < 1.0:
        p = float(binomtest(hits, n, base, alternative="two-sided").pvalue)
    return BiasFinding(
        "recency", q, n, obs, base, "rate", p_value=p,
        detail=(
            f"{hits} of {n} of your subjects had hauled within {RECENT_HAUL_WINDOW} "
            f"gameweeks, against {base:.1%} of the eligible pool"
        ),
    )


def _club_affinity(df: pd.DataFrame) -> BiasFinding:
    usable = df[df["is_supported_club"].notna() & df["pop_supported_club_rate"].notna()]
    n = len(usable)
    q = f"Do I pick {USER.supported_club} players because I support them?"
    if n == 0:
        return BiasFinding(
            "club_affinity", q, 0, None, None, "rate",
            detail="no idea has a club recorded yet",
        )
    hits = int(usable["is_supported_club"].astype(bool).sum())
    base = float(usable["pop_supported_club_rate"].astype(float).mean())
    obs = hits / n
    p = None
    if _HAVE_SCIPY and 0.0 < base < 1.0:
        p = float(binomtest(hits, n, base, alternative="two-sided").pvalue)
    return BiasFinding(
        "club_affinity", q, n, obs, base, "rate", p_value=p,
        detail=(
            f"{hits} of {n} of your subjects play for {USER.supported_club}, against "
            f"the {base:.1%} of the selectable squad list that does. Supporting a club "
            "tells you nothing about whether its players outscore their price peers."
        ),
    )


def _scoreboard(df: pd.DataFrame) -> Scoreboard:
    resolved = df[(df["status"] == "resolved") & df["outcome"].notna()]
    decided = resolved[resolved["outcome"] != "push"]

    def rate(frame: pd.DataFrame) -> float | None:
        if frame.empty:
            return None
        return float((frame["outcome"] == "correct").mean())

    margin = None
    if not resolved.empty and resolved["subject_points"].notna().any():
        signed = resolved["subject_points"] - resolved["comparator_points"]
        signed = signed.where(resolved["kind"] != "fade", -signed)
        margin = float(signed.mean())

    brier = baseline = None
    scored = decided[decided["p_thesis_true"].notna()]
    if not scored.empty:
        y = (scored["outcome"] == "correct").astype(float).to_numpy()
        p = scored["p_thesis_true"].astype(float).to_numpy()
        brier = float(np.mean((p - y) ** 2))
        # The only baseline worth beating: 0.5 on everything.
        baseline = float(np.mean((0.5 - y) ** 2))

    agreed = disagreed = None
    if not decided.empty and decided["stance"].notna().any():
        # "The engine agreed" means it put the thesis above even money.
        a = decided[decided["stance"] == "agree"]
        d = decided[decided["stance"] == "disagree"]
        agreed, disagreed = rate(a), rate(d)

    return Scoreboard(
        n_total=len(df),
        n_resolved=len(resolved),
        n_open=int((df["status"] == "open").sum()),
        n_void=int((df["status"] == "void").sum()),
        hit_rate=rate(decided),
        mean_margin=margin,
        acted_n=int(decided["acted"].astype(bool).sum()) if not decided.empty else 0,
        acted_hit_rate=rate(decided[decided["acted"].astype(bool)]) if not decided.empty else None,
        unacted_n=int((~decided["acted"].astype(bool)).sum()) if not decided.empty else 0,
        unacted_hit_rate=(
            rate(decided[~decided["acted"].astype(bool)]) if not decided.empty else None
        ),
        brier=brier,
        baseline_brier=baseline,
        engine_agreed_hit_rate=agreed,
        engine_disagreed_hit_rate=disagreed,
    )


def _by_kind(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["kind", "n", "resolved", "hit_rate"])
    rows = []
    for kind, grp in df.groupby("kind"):
        decided = grp[(grp["status"] == "resolved") & (grp["outcome"].isin(["correct", "incorrect"]))]
        rows.append(
            {
                "kind": kind,
                "n": len(grp),
                "resolved": len(decided),
                "hit_rate": (
                    float((decided["outcome"] == "correct").mean()) if not decided.empty else None
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("n", ascending=False).reset_index(drop=True)


def review(
    warehouse: Warehouse,
    *,
    season: str = "2026-27",
    registry: IdeaRegistry | None = None,
) -> Review:
    """Compute the full review: outcomes, splits and bias tests."""
    reg = registry or IdeaRegistry(warehouse)
    df = reg.context_frame(season)
    if df.empty:
        return Review(
            season=season,
            scoreboard=Scoreboard(),
            findings=(),
            caveats=("No ideas recorded yet. Text the bot something and this fills in.",),
        )

    findings = _holm([_form_chasing(df), _home_bias(df), _recency(df), _club_affinity(df)])
    board = _scoreboard(df)

    caveats: list[str] = []
    if board.n_resolved == 0:
        caveats.append(
            "No idea has resolved yet, so every hit rate below is empty. Bias probes "
            "read the state at submission and do work before results exist; outcome "
            "numbers do not."
        )
    if 0 < board.n_resolved < MIN_OBSERVATIONS:
        caveats.append(
            f"Only {board.n_resolved} ideas have resolved. Hit rates on that many are "
            "dominated by variance; treat them as description, not evidence."
        )
    if df["provider"].notna().any() and set(df["provider"].dropna().unique()) == {"prior"}:
        caveats.append(
            "Every verdict came from the price-rank prior, not a points model. The "
            "Brier score below measures that prior, and it is not a claim about the "
            "engine's eventual accuracy."
        )
    if int(df["degraded"].fillna(False).astype(bool).sum()):
        n = int(df["degraded"].fillna(False).astype(bool).sum())
        caveats.append(f"{n} verdicts were degraded fallbacks after the primary provider failed.")
    unique_pop = df["pop_n"].dropna().unique()
    if len(unique_pop) and float(np.max(unique_pop)) == 0:
        caveats.append(
            "No gameweek had finalised when these ideas were submitted, so form, venue "
            "and haul context are all empty. The bias probes will start working after "
            "GW1 finalises."
        )

    return Review(
        season=season,
        scoreboard=board,
        findings=tuple(findings),
        by_kind=_by_kind(df),
        ideas=df,
        caveats=tuple(caveats),
    )
