"""Effect sizes between cohorts of managers, with the multiplicity honestly paid for.

This module exists because the natural way to do this analysis produces garbage.
The natural way is: take eighteen strategy features, compare winners against
everybody else, report the ones where p < 0.05. With eighteen features and a
threshold of 0.05 you expect roughly one significant result from pure noise even
if winners are strategically identical to everyone else -- and that one result
will be the one that gets written up, because it is the only one there is to
write up.

So three things are enforced here rather than left to the caller's discipline:

**Effect size, not just significance.** A difference can be real and
irrelevant. Hedges' *g* (Cohen's *d* with the small-sample correction, which
matters a great deal when the "winners" cohort has fifteen members) says how big
the gap is in SDs. Cliff's delta says the same thing without assuming normality,
which several of these features badly violate -- hit counts are zero-inflated
and squad value is bounded.

**Confidence intervals.** A *g* of 0.9 with a CI of [-0.2, 2.0] is not a
finding, and reporting the point estimate alone hides that. The interval is
computed from the standard large-sample variance of *d*, which is itself
approximate at n=15, and that caveat is in the output.

**Multiplicity.** Benjamini-Hochberg across every comparison in a run, reported
as ``q_value`` next to the raw ``p_value``. A feature that survives at q<0.10 is
worth acting on; one that is significant at p<0.05 but q=0.6 is what the
eighteen-feature sweep was always going to produce and is labelled as such.

The output is deliberately a table with sample sizes in it, because the single
most useful thing a reader can do with this analysis is notice that a cohort has
eleven members.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from scipy import stats

#: Preferred reporting order, most-selected first, so a monotone trend across
#: the table is visible at a glance rather than needing to be reconstructed.
#: Cohorts NOT named here are still compared -- they are appended in sorted
#: order by :func:`compare_cohorts`. Treating this tuple as an allow-list was a
#: bug: it silently dropped every cohort the crawl invented (`elite_list`,
#: `snowball`, ...) from the analysis while the table still looked complete.
COHORTS = ("winner", "repeat_top10k", "elite_list", "expert", "snowball",
           "mini_league", "pool", "field")


@dataclass(frozen=True, slots=True)
class Comparison:
    """One feature, one pair of cohorts, one honest verdict."""

    feature: str
    group_a: str
    group_b: str
    n_a: int
    n_b: int
    mean_a: float
    mean_b: float
    median_a: float
    median_b: float
    hedges_g: float
    g_ci_low: float
    g_ci_high: float
    cliffs_delta: float
    p_value: float
    q_value: float = float("nan")

    @property
    def magnitude(self) -> str:
        """Cohen's conventional labels, with the interval doing the real work."""
        g = abs(self.hedges_g)
        if not np.isfinite(g):
            return "undefined"
        if self.g_ci_low <= 0 <= self.g_ci_high:
            return "indistinguishable (CI spans zero)"
        if g < 0.2:
            return "negligible"
        if g < 0.5:
            return "small"
        if g < 0.8:
            return "medium"
        return "large"


def hedges_g(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """Bias-corrected standardised mean difference with a 95% interval.

    Cohen's *d* is biased upward at small n by roughly ``3/(4*df-1)``, which at
    the cohort sizes available here (a dozen winners, if that) is a several
    percent inflation applied to exactly the comparison the reader cares most
    about. Hedges' correction removes it; the interval keeps them honest anyway.
    """
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan"), float("nan"), float("nan")
    va, vb = a.var(ddof=1), b.var(ddof=1)
    pooled = ((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)
    if pooled <= 0:
        return float("nan"), float("nan"), float("nan")
    d = (a.mean() - b.mean()) / np.sqrt(pooled)
    df = na + nb - 2
    correction = 1.0 - 3.0 / (4.0 * df - 1.0)
    g = d * correction
    se = np.sqrt((na + nb) / (na * nb) + d ** 2 / (2.0 * df))
    return float(g), float(g - 1.96 * se), float(g + 1.96 * se)


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """P(a > b) - P(a < b). Rank-based, so heavy tails and zero-inflation are fine.

    Reported alongside *g* because these features are not normal and where the
    two disagree, the disagreement is itself the finding: it means the mean gap
    is being driven by a couple of extreme managers rather than by the cohort.
    """
    if len(a) == 0 or len(b) == 0:
        return float("nan")
    diff = np.sign(a[:, None] - b[None, :])
    return float(diff.mean())


def benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    """FDR-adjusted q-values. Ties handled by the standard step-up procedure."""
    p = np.asarray(p, dtype=float)
    ok = np.isfinite(p)
    q = np.full_like(p, np.nan)
    if not ok.any():
        return q
    vals = p[ok]
    order = np.argsort(vals)
    ranked = vals[order]
    m = len(ranked)
    adj = ranked * m / (np.arange(1, m + 1))
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.clip(adj, 0, 1)
    q[ok] = out
    return q


def compare_cohorts(
    features: pd.DataFrame,
    cohort_col: str,
    feature_cols: list[str],
    *,
    pairs: list[tuple[str, str]] | None = None,
    min_n: int = 3,
) -> pd.DataFrame:
    """Effect-size table for every (feature, cohort pair) combination.

    Rows with too few observations in either cohort are still emitted, with
    their sample sizes and NaN effect sizes, rather than silently dropped.
    A reader who sees ``n_a = 2`` learns something; a reader who sees a table
    that mysteriously lacks a row learns nothing and may assume it was tested.
    """
    observed = set(features[cohort_col].dropna())
    cohorts = [c for c in COHORTS if c in observed]
    cohorts += sorted(observed - set(cohorts))
    if pairs is None:
        pairs = [(cohorts[i], cohorts[j])
                 for i in range(len(cohorts)) for j in range(i + 1, len(cohorts))]

    rows: list[Comparison] = []
    for feat in feature_cols:
        if feat not in features.columns:
            continue
        for ga, gb in pairs:
            a = features.loc[features[cohort_col] == ga, feat].dropna().to_numpy(float)
            b = features.loc[features[cohort_col] == gb, feat].dropna().to_numpy(float)
            if len(a) < min_n or len(b) < min_n:
                rows.append(Comparison(feat, ga, gb, len(a), len(b),
                                       float(a.mean()) if len(a) else np.nan,
                                       float(b.mean()) if len(b) else np.nan,
                                       float(np.median(a)) if len(a) else np.nan,
                                       float(np.median(b)) if len(b) else np.nan,
                                       np.nan, np.nan, np.nan, np.nan, np.nan))
                continue
            g, lo, hi = hedges_g(a, b)
            try:
                p = float(stats.mannwhitneyu(a, b, alternative="two-sided").pvalue)
            except ValueError:
                p = float("nan")
            rows.append(Comparison(
                feat, ga, gb, len(a), len(b), float(a.mean()), float(b.mean()),
                float(np.median(a)), float(np.median(b)), g, lo, hi,
                cliffs_delta(a, b), p,
            ))

    table = pd.DataFrame([asdict(r) for r in rows])
    if table.empty:
        return table
    table["q_value"] = benjamini_hochberg(table["p_value"].to_numpy())
    table["magnitude"] = [
        Comparison(**{k: v for k, v in r.items() if k != "magnitude"}).magnitude
        for r in table.to_dict("records")
    ]
    return table.sort_values(
        ["q_value", "hedges_g"], ascending=[True, False], na_position="last"
    ).reset_index(drop=True)


def surviving(table: pd.DataFrame, *, q_max: float = 0.10, min_g: float = 0.3) -> pd.DataFrame:
    """The findings worth turning into a policy: real direction, real size.

    Both filters are needed. ``q_max`` alone lets through effects that are
    statistically detectable and practically irrelevant, which is what large
    samples do. ``min_g`` alone lets through the largest of eighteen noise
    draws, which is what small samples do.
    """
    if table.empty:
        return table
    keep = (
        (table["q_value"] <= q_max)
        & (table["hedges_g"].abs() >= min_g)
        & ~((table["g_ci_low"] <= 0) & (table["g_ci_high"] >= 0))
    )
    return table[keep].reset_index(drop=True)


def power_note(n_a: int, n_b: int, *, alpha: float = 0.05, power: float = 0.8) -> float:
    """Smallest effect this comparison could reliably have detected.

    The number to quote when a comparison comes back null. "Winners do not take
    more hits" and "we could not have detected anything smaller than 1.2 SDs"
    are very different statements, and only the second one is defensible from a
    cohort of twelve.
    """
    if n_a < 2 or n_b < 2:
        return float("inf")
    z_a = stats.norm.ppf(1 - alpha / 2)
    z_b = stats.norm.ppf(power)
    return float((z_a + z_b) * np.sqrt(1.0 / n_a + 1.0 / n_b))
