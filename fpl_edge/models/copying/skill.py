"""Separating skill from luck in a manager's finishing record.

The problem, stated honestly
----------------------------
Six million entries play FPL. If finishing position were pure noise, roughly six
hundred of them would finish inside the top 10,000 in any given season, and
about one would do it three seasons running. So "finished 4,000th last year" is
close to worthless as evidence, and "has finished top-10k twice in eight
seasons" is only slightly better. Any shortlist built by sorting on best-ever
rank is a shortlist of lucky people.

What the API gives us to work with is also thinner than it looks.
``/api/entry/{id}/history/`` exposes, per completed season, exactly four fields:
season label, total points, final overall rank, and a coarsely rounded rank
percentage. There is no archive of past picks, transfers or chips for any entry,
for any season, at any endpoint. So a multi-season skill estimate has to be
built from a sequence of final ranks and nothing else. This module does that,
and refuses to pretend the sequence says more than it does.

The measure
-----------
Ranks are not comparable across seasons: the field grew from roughly 4.5 million
in 2016/17 to over 11 million by 2022/23, so 100,000th means quite different
things at either end. Percentile is comparable; a percentile is also a terrible
scale to average, because the difference between the 50th and 40th percentile is
a fraction of the skill difference between the 1st and 0.1st.

So each season is converted to a **normal score**::

    z = Phi^-1(1 - rank / field_size)

which is the manager's position in standard deviations of the field's ability
distribution. Season totals across a large field are approximately normal, so
this is close to a linear ability scale, it is comparable across seasons of any
size, and it makes "how far above average" a meaningful quantity to average.

Separating the two variances
----------------------------
Model each observed season as ability plus noise::

    z[m,s] = theta[m] + e[m,s],    e ~ N(0, sigma^2)
    theta[m] ~ N(mu, tau^2)

``sigma^2`` is within-manager season-to-season variance: how much one manager's
finish bounces around their own true level. ``tau^2`` is between-manager
variance: how much managers genuinely differ. Both are estimated from the pool
by moments, and the posterior mean of a manager's ability is the shrinkage
estimator::

    theta_hat[m] = mu + r[m] * (zbar[m] - mu),   r[m] = tau^2 / (tau^2 + sigma^2/n[m])

``r`` is the reliability, and it is the whole argument. A manager with one
spectacular season has small ``n``, so ``r`` is small, so their estimate is
pulled hard back toward the pool mean -- which is exactly the correct treatment
of one lucky year. A manager with eight seasons of consistently high finishes
keeps most of their observed average. The shortlist this produces is a shortlist
of people whose record is unlikely to be luck, which is a different and much
smaller set than the people with the best single season.

The number that decides whether any of this is worth doing is the intraclass
correlation ``tau^2 / (tau^2 + sigma^2)``: the share of a single season's
variation that is ability rather than noise. :func:`persistence` measures it,
along with a walk-forward test of whether a score fitted on early seasons
actually predicts later ones. If those numbers come back near zero then copying
"skilled" managers has no basis, and this module is built to be able to say so.

What this cannot correct for
----------------------------
*Selection.* The pool is snowballed from known names and the leagues they share
(see :mod:`fpl_edge.ingest.rivals.roster`), so it over-represents managers who
are visible, and visibility correlates with past success. ``mu`` is therefore the
mean of a selected group, not of FPL, and it is reported as such.

*Survivorship.* Managers who had a bad season and stopped playing are absent.
Their absence inflates the apparent persistence of skill, because the observed
record of everyone still here is conditioned on them having stayed.

*Range restriction.* A pool of mostly-good managers has less spread in true
ability than FPL does, which biases ``tau^2`` downward and therefore over-shrinks.
The direction is conservative -- it makes this module *understate* skill
differences -- which is the right way for the bias to point.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats

#: Clamp on the normal score. A manager who finished 1st in a field of eleven
#: million has a percentile of 9e-8 and a z of 5.2; without a clamp, a rank of 0
#: or a field-size estimate that is slightly too small produces an infinity that
#: silently poisons every pooled variance downstream.
Z_CLAMP = 5.5


def _season_sort_key(label: str) -> tuple[int, int]:
    """Order FPL's '2018/19' labels chronologically."""
    m = re.match(r"^(\d{4})/(\d{2})$", str(label).strip())
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)))


# ---------------------------------------------------------------------------
# Field sizes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FieldSize:
    """An estimate of how many entries a season had, with its provenance."""

    season: str
    estimate: float
    low: float
    high: float
    method: str
    observations: int
    #: Fraction of the pool whose bracket contains the estimate. A season where
    #: only half the managers agree is weak evidence and must not be read with
    #: the same confidence as one where 99% do.
    coverage: float = 0.0


def _pct_bracket(pct: float, text: str | None = None) -> tuple[float, float]:
    """The interval a rounded percentage could have come from.

    FPL prints ``rank_percentage`` with whatever precision it feels like:
    '46', '1', '0.2', '1.0'. The number of decimals in the printed string is the
    rounding unit, so '0.2' means the true value lies in [0.15, 0.25) and '46'
    means [45.5, 46.5). Treating '46' as exactly 46 would give a field-size
    estimate with a spurious six significant figures.

    ``text`` is FPL's original string and is used when available. Without it the
    precision has to be inferred from the float, and ``1.0`` and ``1`` are then
    indistinguishable -- so the wider of the two brackets is assumed. That is
    the safe direction: an over-wide bracket costs precision but can never
    exclude the true field size, whereas an over-narrow one produces an empty
    intersection and forces the whole season onto the fallback estimator.
    """
    if text is not None and str(text).strip():
        printed = str(text).strip()
        decimals = len(printed.split(".")[1]) if "." in printed else 0
    else:
        decimals = 0 if float(pct).is_integer() else len(f"{pct}".split(".")[1])
    half = 0.5 * (10 ** -decimals)
    return max(pct - half, 1e-9), pct + half


def _max_coverage(
    brackets: list[tuple[float, float]], floor: float
) -> tuple[float, float, int]:
    """The interval consistent with the most brackets.

    Coverage as a function of field size is piecewise constant and changes only
    at a bracket endpoint, so its maximum is attained at some bracket's lower
    bound. Evaluating there is exact rather than approximate, and two
    ``searchsorted`` calls make it O(n log n) instead of the O(n^2) the obvious
    nested loop would cost on a six-hundred-manager pool.

    The returned interval is the intersection of the brackets that cover the
    winning point -- the tightest range those witnesses jointly support -- not
    the plateau of equal coverage, which would be wider and would overstate the
    uncertainty.
    """
    clipped = [(max(lo, floor), hi) for lo, hi in brackets if hi >= max(lo, floor)]
    if not clipped:
        return floor, floor, 0

    los = np.array([lo for lo, _ in clipped], dtype=float)
    his = np.array([hi for _, hi in clipped], dtype=float)
    los_sorted = np.sort(los)
    his_sorted = np.sort(his)

    counts = (
        np.searchsorted(los_sorted, los, side="right")
        - np.searchsorted(his_sorted, los, side="left")
    )
    best_at = float(los[int(np.argmax(counts))])
    covering = (los <= best_at) & (his >= best_at)
    return float(los[covering].max()), float(his[covering].min()), int(covering.sum())


def estimate_field_sizes(seasons: pd.DataFrame) -> pd.DataFrame:
    """How many entries each season had, bracketed rather than guessed.

    FPL never states a season's entry count in any endpoint, but every manager's
    history row states their rank *and* their rounded percentile, and those two
    together bracket the field size::

        field_size in [ rank / (pct_high/100),  rank / (pct_low/100) ]

    Each manager's row gives one such bracket, and the true field size lies in
    all of them -- in theory. In practice a strict intersection across several
    hundred managers is empty for every season, and it is worth being precise
    about why rather than shrugging and taking a median: FPL's percentage is not
    a clean round of ``rank/N``. Tied ranks, mid-season entries and whatever
    rounding rule the site actually applies each produce rows whose bracket is
    slightly wrong, and one such row anywhere in a pool of six hundred destroys
    the intersection. Requiring unanimity from six hundred noisy witnesses is a
    bad estimator, not a rigorous one.

    So the estimate is the **maximum-coverage interval**: the range of field
    sizes consistent with the largest number of managers' brackets. It degrades
    gracefully -- a handful of bad rows shift the coverage count by a handful --
    and it reports ``coverage``, the fraction of the pool that agrees, so a
    season held up by 55% of its witnesses is visibly weaker evidence than one
    held up by 99%.
    """
    rows: list[FieldSize] = []
    usable = seasons[
        seasons["overall_rank"].notna()
        & seasons["rank_percentage"].notna()
        & (seasons["rank_percentage"] > 0)
    ]
    for season, grp in usable.groupby("season"):
        brackets: list[tuple[float, float]] = []
        points = []
        has_text = "rank_percentage_text" in grp.columns
        for _, r in grp.iterrows():
            pct_low, pct_high = _pct_bracket(
                float(r["rank_percentage"]),
                r["rank_percentage_text"] if has_text else None,
            )
            rank = float(r["overall_rank"])
            brackets.append((rank / (pct_high / 100.0), rank / (pct_low / 100.0)))
            points.append(rank / (float(r["rank_percentage"]) / 100.0))
        # A field cannot be smaller than the worst rank anybody achieved in it.
        floor = float(grp["overall_rank"].max())
        lo, hi, covered = _max_coverage(brackets, floor)
        if covered == 0:
            med = float(np.median(points))
            rows.append(FieldSize(str(season), max(med, floor), floor, float("inf"),
                                  "median-fallback(no-coverage)", len(grp), 0.0))
            continue
        rows.append(FieldSize(
            str(season), 0.5 * (lo + hi), lo, hi,
            "bracket-consensus", len(grp), covered / len(brackets),
        ))
    # asdict, not vars: these are slots dataclasses and have no __dict__.
    out = pd.DataFrame([asdict(r) for r in rows])
    if out.empty:
        return pd.DataFrame(columns=["season", "estimate", "low", "high",
                                     "method", "observations"])
    return out.sort_values("season", key=lambda s: s.map(_season_sort_key)).reset_index(drop=True)


def to_normal_scores(seasons: pd.DataFrame, field_sizes: pd.DataFrame) -> pd.DataFrame:
    """Attach percentile and normal score ``z`` to each manager-season row."""
    sizes = dict(zip(field_sizes["season"], field_sizes["estimate"]))
    df = seasons[seasons["overall_rank"].notna()].copy()
    df["field_size"] = df["season"].map(sizes)
    df = df[df["field_size"].notna() & (df["field_size"] > 0)].copy()
    # (rank - 0.5) / N is the standard continuity correction; without it the
    # single best manager in the world sits exactly at percentile 1/N rather
    # than halfway through the top slot, which matters only at the very top --
    # which is precisely where this pool lives.
    df["percentile"] = (df["overall_rank"] - 0.5) / df["field_size"]
    df["percentile"] = df["percentile"].clip(1e-9, 1 - 1e-9)
    df["z"] = np.clip(stats.norm.ppf(1.0 - df["percentile"]), -Z_CLAMP, Z_CLAMP)
    return df


# ---------------------------------------------------------------------------
# Empirical-Bayes skill model
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SkillModel:
    """Fitted variance components for one pool of managers."""

    mu: float                  # pool mean ability, in field SDs
    sigma2_within: float       # season-to-season noise within a manager
    tau2_between: float        # genuine ability spread across managers
    n_managers: int
    n_seasons_total: int
    n_managers_multi: int      # managers contributing to the within estimate

    @property
    def icc(self) -> float:
        """Share of a single season's variance that is ability, not luck.

        This is the headline number. At 0.5, half of what you see in one season
        is real. At 0.1, nine tenths of a manager's finish is noise and copying
        last season's top finishers is close to copying a lottery.
        """
        total = self.tau2_between + self.sigma2_within
        return float(self.tau2_between / total) if total > 0 else 0.0

    def seasons_for_reliability(self, target: float = 0.8) -> float:
        """How many seasons of record are needed before a score is ``target`` reliable.

        Spearman-Brown, rearranged. Answers the practical question directly: if
        this comes back as 14 seasons, then nobody in FPL has enough record for
        a confident individual judgement and the shortlist must be read as a
        weak prior rather than a ranking.
        """
        if self.tau2_between <= 0:
            return float("inf")
        return float(target * self.sigma2_within / ((1 - target) * self.tau2_between))


def fit_skill(z_panel: pd.DataFrame, *, min_seasons_for_within: int = 2) -> SkillModel:
    """Estimate ``mu``, ``sigma^2`` and ``tau^2`` by moments.

    ``sigma^2`` comes from pooled within-manager scatter, which needs at least
    two seasons per manager. ``tau^2`` is then recovered from the spread of
    manager means after subtracting the part of that spread which the sampling
    noise alone explains -- ``Var(zbar_m) = tau^2 + sigma^2 / n_m``. Skipping
    that subtraction is the classic error and inflates apparent skill by exactly
    the amount of luck in the data.
    """
    grouped = z_panel.groupby("entry_id")["z"]
    counts = grouped.count()
    means = grouped.mean()

    multi = counts[counts >= min_seasons_for_within].index
    if len(multi) == 0:
        return SkillModel(float(means.mean()) if len(means) else 0.0, 1.0, 0.0,
                          len(counts), int(counts.sum()), 0)

    sub = z_panel[z_panel["entry_id"].isin(multi)]
    dev = sub["z"] - sub["entry_id"].map(means)
    dof = int(counts[multi].sum() - len(multi))
    sigma2 = float((dev ** 2).sum() / dof) if dof > 0 else 1.0

    mu = float(means.mean())
    if len(means) > 1:
        observed = float(((means - mu) ** 2).sum() / (len(means) - 1))
        expected_noise = float((sigma2 / counts).mean())
        tau2 = max(0.0, observed - expected_noise)
    else:
        tau2 = 0.0

    return SkillModel(mu, sigma2, tau2, len(counts), int(counts.sum()), len(multi))


def score_managers(z_panel: pd.DataFrame, model: SkillModel) -> pd.DataFrame:
    """Per-manager shrunk ability estimate, reliability, and expected finish.

    ``expected_percentile`` converts the ability estimate back to the scale a
    manager actually cares about: the percentile they would be expected to
    finish in next season, before knowing anything about that season. It is a
    point estimate of a very wide distribution, and ``theta_sd`` is there so
    nobody quotes it without the interval.
    """
    grouped = z_panel.groupby("entry_id")["z"]
    out = pd.DataFrame({
        "entry_id": grouped.count().index,
        "n_seasons": grouped.count().to_numpy(),
        "z_mean": grouped.mean().to_numpy(),
        "z_sd": grouped.std(ddof=1).to_numpy(),
        "z_best": grouped.max().to_numpy(),
        "z_worst": grouped.min().to_numpy(),
    })
    tau2, sigma2 = model.tau2_between, model.sigma2_within
    denom = tau2 + sigma2 / out["n_seasons"]
    out["reliability"] = 0.0 if tau2 <= 0 else (tau2 / denom)
    out["theta_hat"] = model.mu + out["reliability"] * (out["z_mean"] - model.mu)
    out["theta_sd"] = np.sqrt(1.0 / (1.0 / tau2 + out["n_seasons"] / sigma2)) \
        if tau2 > 0 else np.nan
    out["expected_percentile"] = 1.0 - stats.norm.cdf(out["theta_hat"])
    return out.sort_values("theta_hat", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Does any of this predict anything?
# ---------------------------------------------------------------------------

@dataclass
class Persistence:
    """Measured answers to 'is FPL skill persistent enough to be worth copying?'"""

    lag1_pairs: int = 0
    lag1_pearson: float = float("nan")
    lag1_spearman: float = float("nan")
    icc: float = float("nan")
    seasons_for_reliable_score: float = float("nan")
    walk_forward: dict[str, float] = field(default_factory=dict)

    def verdict(self) -> str:
        """A one-line reading, with the threshold stated rather than implied."""
        if not np.isfinite(self.lag1_pearson):
            return "insufficient data"
        r = self.lag1_pearson
        if r < 0.05:
            return (f"lag-1 r={r:.3f}: no usable season-to-season persistence. "
                    "Copying last season's high finishers is copying noise.")
        if r < 0.20:
            return (f"lag-1 r={r:.3f}: weak but non-zero persistence. A long "
                    "record is informative; a single good season is not.")
        return (f"lag-1 r={r:.3f}: meaningful persistence. Multi-season records "
                "carry real signal about next season.")


def persistence(z_panel: pd.DataFrame, model: SkillModel, *, cut_season: str | None = None) -> Persistence:
    """Measure whether a manager's past finishes predict their next one.

    Three independent readings, because each can mislead alone:

    1. **Lag-1 correlation.** Pair every manager's season with their next one and
       correlate. Directly answers "does last year predict this year".
    2. **ICC.** The variance decomposition's own estimate of the signal share.
       It uses all seasons, not just adjacent pairs, so it is less noisy, but it
       assumes ability is constant over a decade, which it is not.
    3. **Walk-forward.** Fit the score on seasons strictly before ``cut_season``,
       then check it against seasons from ``cut_season`` on. This is the only one
       of the three that is a genuine out-of-sample test, and it is the one to
       believe when they disagree.
    """
    out = Persistence(icc=model.icc,
                      seasons_for_reliable_score=model.seasons_for_reliability())

    panel = z_panel.copy()
    panel["_key"] = panel["season"].map(_season_sort_key)
    panel = panel.sort_values(["entry_id", "_key"])

    prev, nxt = [], []
    for _, grp in panel.groupby("entry_id"):
        seasons = list(grp["_key"])
        zs = list(grp["z"])
        for i in range(len(seasons) - 1):
            a, b = seasons[i], seasons[i + 1]
            # Only genuinely consecutive seasons. A manager who skipped
            # 2020/21 has a gap, and treating 2019/20 -> 2021/22 as "lag 1"
            # measures a two-year lag and understates persistence.
            if b[0] == a[0] + 1:
                prev.append(zs[i])
                nxt.append(zs[i + 1])
    out.lag1_pairs = len(prev)
    if len(prev) >= 3:
        out.lag1_pearson = float(np.corrcoef(prev, nxt)[0, 1])
        out.lag1_spearman = float(stats.spearmanr(prev, nxt).statistic)

    if cut_season is not None:
        out.walk_forward = _walk_forward(panel, cut_season)
    return out


def persistence_by_stratum(
    z_panel: pd.DataFrame, strata: pd.Series, *, min_pairs: int = 30
) -> pd.DataFrame:
    """Lag-1 persistence measured separately within each stratum.

    Pooling strata overstates persistence, and by a lot. A pool containing both
    all-time-list managers and ordinary mini-league players has a large lag-1
    correlation almost mechanically: the elite stay near the top and the rest
    stay near the middle, so consecutive seasons correlate because the two
    groups differ, not because ability predicts anything *within* either group.

    The decision-relevant question is the within-stratum one. Given a manager
    already known to be good, does last season predict next season? If the
    answer inside the elite stratum is near zero, then choosing WHICH elite
    manager to copy is guesswork even though choosing to copy elite managers at
    all is not.
    """
    panel = z_panel.copy()
    panel["_stratum"] = panel["entry_id"].map(strata)
    panel["_key"] = panel["season"].map(_season_sort_key)

    rows = []
    for stratum, grp in panel.groupby("_stratum"):
        prev, nxt = [], []
        for _eid, one in grp.sort_values("_key").groupby("entry_id"):
            keys = list(one["_key"])
            zs = list(one["z"])
            for i in range(len(keys) - 1):
                if keys[i + 1][0] == keys[i][0] + 1:
                    prev.append(zs[i])
                    nxt.append(zs[i + 1])
        row = {"stratum": str(stratum), "n_managers": int(grp["entry_id"].nunique()),
               "lag1_pairs": len(prev), "lag1_pearson": float("nan"),
               "lag1_spearman": float("nan"), "z_mean": float(grp["z"].mean())}
        if len(prev) >= min_pairs:
            row["lag1_pearson"] = float(np.corrcoef(prev, nxt)[0, 1])
            row["lag1_spearman"] = float(stats.spearmanr(prev, nxt).statistic)
        rows.append(row)
    return pd.DataFrame(rows).sort_values("z_mean", ascending=False).reset_index(drop=True)


def _walk_forward(panel: pd.DataFrame, cut_season: str) -> dict[str, float]:
    """Fit on seasons before the cut, evaluate on seasons from the cut onward."""
    cut = _season_sort_key(cut_season)
    before = panel[panel["_key"] < cut]
    after = panel[panel["_key"] >= cut]
    if before.empty or after.empty:
        return {"n_managers": 0.0}

    model = fit_skill(before)
    scores = score_managers(before, model).set_index("entry_id")
    realised = after.groupby("entry_id")["z"].mean()
    common = scores.index.intersection(realised.index)
    if len(common) < 5:
        return {"n_managers": float(len(common))}

    pred = scores.loc[common, "theta_hat"]
    obs = realised.loc[common]
    rho = stats.spearmanr(pred, obs)
    median_obs = float(obs.median())
    top_q = pred >= pred.quantile(0.75)
    hit = float((obs[top_q] > median_obs).mean()) if top_q.any() else float("nan")
    return {
        "n_managers": float(len(common)),
        "cut_season_key": float(cut[0]),
        "spearman": float(rho.statistic),
        "p_value": float(rho.pvalue),
        # Fraction of the pre-cut top quartile that beat the pool median
        # afterwards. 0.5 is chance; anything meaningfully above it means the
        # score has real, usable selection power.
        "top_quartile_beats_median_rate": hit,
        "base_rate": 0.5,
    }


# ---------------------------------------------------------------------------
# Shortlisting
# ---------------------------------------------------------------------------

def shortlist(
    scores: pd.DataFrame,
    z_panel: pd.DataFrame,
    *,
    min_seasons: int = 4,
    top_n: int = 25,
    names: dict[int, str] | None = None,
) -> pd.DataFrame:
    """The managers worth copying, with their actual record attached.

    ``min_seasons`` is the whole argument in one parameter. Dropping it to 1
    would repopulate the list with lottery winners; the default of 4 is set so
    that the probability of reaching the top of the shrunk ranking on noise
    alone is small, while still leaving a usable pool.
    """
    keep = scores[scores["n_seasons"] >= min_seasons].head(top_n).copy()
    if keep.empty:
        return keep

    panel = z_panel.copy()
    panel["_key"] = panel["season"].map(_season_sort_key)
    panel = panel.sort_values("_key")
    record: dict[int, str] = {}
    best: dict[int, int] = {}
    top10k: dict[int, int] = {}
    for eid, grp in panel.groupby("entry_id"):
        record[eid] = ", ".join(
            f"{s}:{int(r):,}" for s, r in zip(grp["season"], grp["overall_rank"])
        )
        best[eid] = int(grp["overall_rank"].min())
        top10k[eid] = int((grp["overall_rank"] <= 10_000).sum())

    keep["best_rank"] = keep["entry_id"].map(best)
    keep["top10k_seasons"] = keep["entry_id"].map(top10k)
    keep["rank_history"] = keep["entry_id"].map(record)
    if names:
        keep["name"] = keep["entry_id"].map(names)
    return keep.reset_index(drop=True)


def compare_to(scores: pd.DataFrame, entry_id: int) -> dict[str, float]:
    """Where one specific manager sits in the pool. Used for the user's own entry."""
    row = scores[scores["entry_id"] == entry_id]
    if row.empty:
        return {}
    r = row.iloc[0]
    return {
        "entry_id": float(entry_id),
        "n_seasons": float(r["n_seasons"]),
        "z_mean": float(r["z_mean"]),
        "theta_hat": float(r["theta_hat"]),
        "reliability": float(r["reliability"]),
        "expected_percentile": float(r["expected_percentile"]),
        "pool_rank": float((scores["theta_hat"] > r["theta_hat"]).sum() + 1),
        "pool_size": float(len(scores)),
    }


def expected_rank(theta_hat: float, field_size: float) -> int:
    """Convert an ability estimate to an expected finishing rank in a given field."""
    pct = 1.0 - stats.norm.cdf(theta_hat)
    return max(1, int(round(pct * field_size)))


def summarise(values: Iterable[float]) -> dict[str, float]:
    """Small helper so report code does not re-derive the same five numbers."""
    arr = np.asarray([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    if arr.size == 0:
        return {"n": 0.0}
    return {
        "n": float(arr.size), "mean": float(arr.mean()), "sd": float(arr.std(ddof=1))
        if arr.size > 1 else 0.0, "median": float(np.median(arr)),
        "p10": float(np.percentile(arr, 10)), "p90": float(np.percentile(arr, 90)),
    }
