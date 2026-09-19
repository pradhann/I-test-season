"""The creator report card: three evidence channels that are never one number.

A creator makes two completely different kinds of statement, and the whole
value of this module is that it refuses to average them:

1. **Claims** -- "buy Semenyo", "captain Haaland", "avoid Isak". Binary, scored
   hit/flop by ``fpl_edge.ingest.content.scoring`` against the positional
   starter median, with the published-before-deadline leak check. The evidence
   is a proportion, so the honest summary is a proportion *with an interval*.
2. **Their own team** -- the squad they actually locked in, read from
   ``fact_manager_pick`` / ``fact_manager_gw`` for a verified ``entry_id``.
   Not an opinion: a fact with a deadline on it. The evidence is a sequence of
   gameweek scores, so the honest summary is a mean difference *with an
   interval*.
3. **Numeric predictions** -- MAE / RMSE, in the exact vocabulary
   ``fpl_edge.eval.projection_scoring`` already uses (``scope`` / ``metric`` /
   ``value`` / ``baseline`` / ``n_obs``), read from ``fact_projection_score``
   for a creator who is ALSO an ingested projection provider. Today no creator
   is, and :func:`numeric_channel` says so instead of inventing a third number.

Blending them would be the one thing that makes the card worse than nothing.
A hit rate of 0.65 over 20 claims and a squad 14 points above the cohort over
two gameweeks are not two measurements of the same quantity, they do not share
units, and no weighting of them is defensible. So the card carries three
blocks, each with its own ``n``, its own interval and its own ``quotable``
flag, and the caller renders three things.

Two sample-size floors, both derived rather than chosen
-------------------------------------------------------

``MIN_SCORED_CLAIMS`` (25) is not this module's invention -- it is imported
from ``ingest.content.scoring``, where it already gates ``earned_weight``.
Restating it as a local constant is how two numbers drift apart, so it is
imported and re-exported.

``MIN_GW_MEASURED`` (10) is new here, and the honest framing is that it is not
enough. Manager gameweek scores have a standard deviation around 20 points
across a field; detecting a genuine 5-points-per-gameweek edge at 95%
confidence needs roughly ``(1.96 * 20 / 5)**2`` ~= 61 gameweeks, which is more
than a season and a half. So a season of data CANNOT establish that one
creator's team is better than another's at any effect size a reader would care
about, and the team channel therefore never returns ``quotable: True`` on the
strength of a points delta alone -- it returns the delta, its 95% interval, and
the fact that the interval contains zero. Ten gameweeks is the floor below
which even the interval is too wide to print as anything but a record.

Untrusted text does not enter here. This module sees counts, points and
gameweek numbers only; creator prose stays in ``creators.py``.
"""

from __future__ import annotations

import math
from typing import Any

from fpl_edge.ingest.content.scoring import (
    MIN_SCORED_CLAIMS,
    earned_weight,
    wilson_lower_bound,
)

__all__ = [
    "BASELINE_KINDS",
    "MIN_GW_MEASURED",
    "MIN_SCORED_CLAIMS",
    "claims_channel",
    "numeric_channel",
    "provider_key",
    "team_channel",
    "wilson_interval",
]

#: Gameweeks of a creator's own team below which the points delta is not even
#: worth an interval. See the module docstring for why no achievable number
#: makes it *quotable*.
MIN_GW_MEASURED = 10

#: The baselines the team channel understands.
#:
#: ``field_average`` is the default and is FPL's own published
#: ``average_entry_score`` for the gameweek: 50 in GW1, 81 in GW2, 51 in GW3.
#: It is what a reader means by "beat the field", and it now sits in
#: ``dim_event`` -- the ``field_average`` gap this panel used to report.
#:
#: ``cohort_mean`` mirrors ``projection_scoring``'s convention exactly: the
#: baseline is the mean of the OTHER measured subjects on the SAME
#: observations. It answers "better than the other creators".
#:
#: ``crawled_pool_median`` is the crawled rival pool, which is top-1k biased
#: and must never be read as the field.
BASELINE_KINDS = ("field_average", "cohort_mean", "crawled_pool_median")


def wilson_interval(hits: int, n: int, z: float = 1.96
                    ) -> tuple[float | None, float | None]:
    """Both ends of the 95% Wilson interval on a hit rate.

    The lower end is ``ingest.content.scoring.wilson_lower_bound`` verbatim --
    the number the weight rule already runs on -- and the upper end is the same
    algebra with the margin added rather than subtracted. Returning both is the
    point: a lower bound alone reads as a pessimistic estimate, while
    ``[0.43, 0.83]`` reads as what it is, which is "we do not know yet".
    """
    if n <= 0:
        return None, None
    lo = wilson_lower_bound(hits, n, z)
    phat = hits / n
    denom = 1.0 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    hi = min(1.0, (centre + margin) / denom)
    return round(lo, 4), round(hi, 4)


def _r(x: Any, nd: int = 4) -> float | None:
    """Round, mapping NaN to None.

    DuckDB's SQL NULL arrives here as ``float('nan')`` through pandas, and a
    NaN that survives into the payload serialises as the JSON token ``NaN``,
    which is not JSON, or as ``null`` with a ``hit_rate`` that a strict schema
    then rejects. ``hit_rate`` is legitimately NULL for a creator with claims
    and nothing scored yet, so this path is the common case, not the edge one.
    """
    if x is None:
        return None
    try:
        value = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else round(value, nd)


def _int(x: Any) -> int | None:
    """Int, mapping NaN and unparseable to None. Same reason as :func:`_r`."""
    value = _r(x, 0)
    return None if value is None else int(value)


def _verdict_vs_coin_flip(lo: float | None, hi: float | None,
                          n: int) -> tuple[str, str]:
    """Where 0.5 sits relative to the interval, in words, plus the reason.

    Three outcomes and no fourth: the interval is entirely above the coin flip,
    entirely below it, or it contains it. "Contains it" is by far the most
    common answer in this warehouse and it is a real finding, not a failure.
    """
    if n <= 0 or lo is None or hi is None:
        return "unmeasured", "no claim of theirs has been scored yet"
    if lo > 0.5:
        return "above", (
            f"the 95% interval [{lo}, {hi}] over {n} scored claims lies "
            f"entirely above the 0.5 coin flip"
        )
    if hi < 0.5:
        return "below", (
            f"the 95% interval [{lo}, {hi}] over {n} scored claims lies "
            f"entirely below the 0.5 coin flip"
        )
    return "indistinguishable", (
        f"the 95% interval [{lo}, {hi}] over {n} scored claims contains 0.5, "
        f"so this record is indistinguishable from a coin flip"
    )


def claims_channel(
    score_row: dict[str, Any] | None,
    outcomes: list[dict[str, Any]] | None = None,
    *,
    min_scored: int = MIN_SCORED_CLAIMS,
) -> dict[str, Any]:
    """The binary channel, from one ``creator_score`` row plus its outcomes.

    ``score_row`` is a ``creator_score`` row at ``scope='all'`` already bounded
    point-in-time by the caller. ``outcomes`` are ``claim_outcome`` rows for
    the same creator (``gameweek``, ``action``, ``hit``), used only for the
    breakdowns -- the headline numbers come from ``creator_score`` so that this
    card and the weights the model runs on can never disagree.

    Every rate carries its ``n`` and its interval; nothing below ``min_scored``
    is marked quotable. ``weight`` is reported verbatim including the 0.0 that
    every creator currently has, because that zero is a measurement.
    """
    if score_row is None:
        return {
            "channel": "claims",
            "kind": "binary",
            "measured": False,
            "quotable": False,
            "n_total": None, "n_scored": 0, "hits": None,
            "hit_rate": None, "wilson_lo95": None, "wilson_hi95": None,
            "ci95": None,
            "weight": None, "earned": False,
            "min_scored_claims": int(min_scored),
            "vs_coin_flip": "unmeasured",
            "by_gw": [], "by_action": [],
            "first_claim_utc": None, "last_claim_utc": None,
            "reason": "no creator_score row at or before this instant: nothing "
                      "this creator said has been scored yet",
        }

    n_scored = _int(score_row.get("claims_scored")) or 0
    hits = _int(score_row.get("hits"))
    lo, hi = wilson_interval(hits or 0, n_scored)
    verdict, verdict_reason = _verdict_vs_coin_flip(lo, hi, n_scored)
    quotable = n_scored >= int(min_scored)

    if n_scored == 0:
        reason = (
            "claims are recorded but none is scoreable yet: a claim becomes "
            "checkable only once its gameweek has finalised, and one published "
            "after its own deadline is never scoreable at all"
        )
    elif not quotable:
        reason = (
            f"{n_scored} scored claim(s) is below the house floor of "
            f"{int(min_scored)}, the same floor earned_weight() uses. "
            f"{verdict_reason}. Not quotable as a rank."
        )
    else:
        reason = verdict_reason

    return {
        "channel": "claims",
        "kind": "binary",
        "measured": n_scored > 0,
        "quotable": quotable,
        "n_total": _int(score_row.get("claims_total")),
        "n_scored": n_scored,
        "hits": hits,
        "hit_rate": _r(score_row.get("hit_rate")),
        "wilson_lo95": lo,
        "wilson_hi95": hi,
        "ci95": None if lo is None else [lo, hi],
        "weight": _r(score_row.get("weight")) or 0.0,
        "earned": bool((_r(score_row.get("weight")) or 0.0) > 0.0),
        "min_scored_claims": int(min_scored),
        "vs_coin_flip": verdict,
        "by_gw": _breakdown(outcomes, "gameweek", min_scored),
        "by_action": _breakdown(outcomes, "action", min_scored),
        "first_claim_utc": score_row.get("first_claim_utc"),
        "last_claim_utc": score_row.get("last_claim_utc"),
        "reason": reason,
    }


def _breakdown(outcomes: list[dict[str, Any]] | None, key: str,
               min_scored: int) -> list[dict[str, Any]]:
    """Hit rates split by gameweek or by action, each with its own interval.

    A split is ALWAYS smaller than the whole, so every one of these is below
    the floor by construction today. They are emitted anyway -- "3/4 on
    captaincy" is a useful thing for a reader to see -- with ``quotable``
    false, so a UI cannot accidentally rank on them.
    """
    if not outcomes:
        return []
    buckets: dict[Any, list[int]] = {}
    for row in outcomes:
        hit = row.get("hit")
        if hit is None:
            continue
        bucket = row.get(key)
        if bucket is None:
            continue
        got = buckets.setdefault(bucket, [0, 0])
        got[0] += 1
        got[1] += int(bool(hit))
    out: list[dict[str, Any]] = []
    for bucket, (n, hits) in sorted(buckets.items(), key=lambda kv: str(kv[0])):
        lo, hi = wilson_interval(hits, n)
        out.append({
            key if key == "action" else "gw":
                int(bucket) if key == "gameweek" else str(bucket),
            "n": n,
            "hits": hits,
            "hit_rate": round(hits / n, 4),
            "wilson_lo95": lo,
            "wilson_hi95": hi,
            "quotable": n >= int(min_scored),
        })
    return out


def provider_key(creator: str) -> str:
    """Normalised join key between a creator name and a projection provider.

    ``fact_projection`` keys providers as ``fplform`` / ``gh_blueladd`` /
    ``fpl_ep``; ``content_item`` keys creators as ``FPL Review`` /
    ``Fantasy Football Hub``. A creator who publishes a projection feed we
    ingest would appear on both sides under the same squashed name, and this is
    the rule that would notice. It matches nothing in this warehouse today,
    which is a fact the numeric channel reports rather than a reason to delete
    the rule: the day an FPL Review or Hub projection feed is ingested, the
    numeric half of that creator's card lights up with no code change.
    """
    return "".join(ch for ch in creator.lower() if ch.isalnum())


def numeric_channel(
    rows: list[dict[str, Any]] | None,
    *,
    provider: str | None = None,
    link_reason: str | None = None,
) -> dict[str, Any]:
    """MAE / RMSE from ``fact_projection_score``, or the reason there is none.

    ``rows`` are ``fact_projection_score`` rows for one provider, in that
    table's own vocabulary (``scope``, ``metric``, ``value``, ``baseline``,
    ``n_obs``) -- deliberately not re-spelled, so a reader who has seen the
    projection scoreboard reads this block without translation. Per-gameweek
    rows are pooled observation-weighted, the same way ``fit_weights`` pools
    them: MAE by ``n_obs``-weighted mean, RMSE by ``n_obs``-weighted mean of
    the SQUARES then square root, because averaging RMSEs directly is wrong.
    """
    empty = {
        "channel": "numeric",
        "kind": "continuous",
        "measured": False,
        "quotable": False,
        "provider": provider,
        "mae": None, "rmse": None,
        "baseline_mae": None, "baseline_rmse": None,
        "n_obs": 0, "n_gw": 0,
        "by_gw": [],
        "reason": link_reason or (
            "this creator publishes no numeric prediction. content_claim "
            "carries (player, action, gameweek) and an extractor confidence, "
            "which is a binary call and not an expected-points number, so "
            "there is nothing here for MAE or RMSE to measure. The channel "
            "lights up only for a creator who is also an ingested projection "
            "provider in fact_projection_score."
        ),
    }
    if not rows:
        return empty

    overall = [r for r in rows if str(r.get("scope")) == "overall"]
    by_metric: dict[str, list[dict[str, Any]]] = {}
    for r in overall:
        by_metric.setdefault(str(r.get("metric")), []).append(r)
    mae_rows = by_metric.get("mae", [])
    rmse_rows = by_metric.get("rmse", [])
    if not mae_rows and not rmse_rows:
        return empty

    def _pool(rs: list[dict[str, Any]], field: str, square: bool) -> float | None:
        pairs = [(float(r[field]), int(r["n_obs"])) for r in rs
                 if r.get(field) is not None and r.get("n_obs")]
        if not pairs:
            return None
        n = sum(p[1] for p in pairs)
        if square:
            return math.sqrt(sum(v * v * w for v, w in pairs) / n)
        return sum(v * w for v, w in pairs) / n

    n_obs = sum(int(r["n_obs"]) for r in mae_rows or rmse_rows if r.get("n_obs"))
    gws = sorted({int(r["gw"]) for r in overall if r.get("gw") is not None})
    per_gw = []
    for gw in gws:
        m = next((r for r in mae_rows if int(r.get("gw", -1)) == gw), None)
        s = next((r for r in rmse_rows if int(r.get("gw", -1)) == gw), None)
        per_gw.append({
            "gw": gw,
            "mae": _r(None if m is None else m.get("value")),
            "rmse": _r(None if s is None else s.get("value")),
            "baseline_mae": _r(None if m is None else m.get("baseline")),
            "baseline_rmse": _r(None if s is None else s.get("baseline")),
            "n_obs": int((m or s or {}).get("n_obs") or 0),
        })

    return {
        "channel": "numeric",
        "kind": "continuous",
        "measured": True,
        # The projection-scoring floor is 200 player-gameweek observations,
        # and it is that module's constant, not one restated here.
        "quotable": n_obs >= 200,
        "provider": provider,
        "mae": _r(_pool(mae_rows, "value", False)),
        "rmse": _r(_pool(rmse_rows, "value", True)),
        "baseline_mae": _r(_pool(mae_rows, "baseline", False)),
        "baseline_rmse": _r(_pool(rmse_rows, "baseline", True)),
        "n_obs": int(n_obs),
        "n_gw": len(gws),
        "by_gw": per_gw,
        "reason": (
            f"scored as projection provider '{provider}' over {len(gws)} "
            f"gameweek(s), {n_obs} player-gameweek observations; the baseline "
            f"is the all-provider mean on the same observations"
        ),
    }


def _mean_ci(values: list[float]) -> tuple[float | None, float | None,
                                           float | None, float | None]:
    """Mean, standard error and the 95% normal interval. Nulls below n=2.

    A single gameweek has no spread to estimate from, so it gets a mean and an
    explicit ``None`` interval rather than a zero-width one -- a zero-width
    interval on n=1 is the exact lie this card exists to prevent.
    """
    n = len(values)
    if n == 0:
        return None, None, None, None
    mean = sum(values) / n
    if n < 2:
        return round(mean, 3), None, None, None
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    se = math.sqrt(var / n)
    return (round(mean, 3), round(se, 3),
            round(mean - 1.96 * se, 3), round(mean + 1.96 * se, 3))


def team_channel(
    entries: list[dict[str, Any]],
    baseline_by_gw: dict[int, dict[str, Any]],
    *,
    baseline_kind: str,
    baseline_label: str,
    baseline_reason: str,
    min_gw: int = MIN_GW_MEASURED,
) -> dict[str, Any]:
    """What their squad actually scored, per gameweek, against the baseline.

    ``entries`` is one dict per verified person: ``person``, ``entry_id``,
    ``verified``, and ``gws`` -- a list of measured gameweek rows carrying
    ``gw``, ``points`` and whatever else the caller read (bench points, hit
    cost, overall rank). A person with a verified id but no crawled gameweek
    keeps their row with ``n_gw: 0`` and a reason, because "we have not read
    their team" and "their team scored nothing" are opposite claims.

    ``quotable`` is False for every person here regardless of the numbers, and
    the reason says why: see the module docstring's power calculation. The
    delta and its interval are still reported -- the reader should see that the
    interval contains zero, which is the actual finding.
    """
    people: list[dict[str, Any]] = []
    for entry in entries:
        gws = sorted(entry.get("gws") or [], key=lambda r: int(r["gw"]))
        deltas: list[float] = []
        rows: list[dict[str, Any]] = []
        for row in gws:
            gw = int(row["gw"])
            pts = row.get("points")
            base = baseline_by_gw.get(gw, {})
            base_pts = base.get("points")
            delta = (None if pts is None or base_pts is None
                     else float(pts) - float(base_pts))
            if delta is not None:
                deltas.append(delta)
            rows.append({
                "gw": gw,
                "points": None if pts is None else int(pts),
                "baseline_points": _r(base_pts, 2),
                "delta": _r(delta, 2),
                "bench_points": row.get("bench_points"),
                "transfers": row.get("transfers"),
                "hit_cost": row.get("hit_cost"),
                "overall_rank": row.get("overall_rank"),
            })
        mean, se, lo, hi = _mean_ci(deltas)
        n_gw = len(rows)
        total = sum(int(r["points"]) for r in rows if r["points"] is not None)
        if n_gw == 0:
            reason = (
                f"entry {entry.get('entry_id')} is verified but no gameweek of "
                f"theirs has been crawled into fact_manager_gw yet, so their "
                f"team is unread rather than empty"
            )
        elif n_gw < int(min_gw):
            reason = (
                f"{n_gw} measured gameweek(s), below the {int(min_gw)}-gameweek "
                f"floor; a points delta over this few weeks has an interval "
                f"wider than any edge worth acting on"
            )
        else:
            reason = (
                f"{n_gw} measured gameweek(s) against the {baseline_label}"
                + ("" if lo is None else
                   f"; 95% interval on the mean delta is [{lo}, {hi}] "
                   f"points per gameweek")
            )
        people.append({
            "person": entry.get("person"),
            "entry_id": entry.get("entry_id"),
            "entry_name": entry.get("entry_name"),
            "verified": bool(entry.get("verified")),
            "n_gw": n_gw,
            "points": total if n_gw else None,
            "baseline_points": _r(
                sum(float(baseline_by_gw[int(r["gw"])]["points"])
                    for r in rows
                    if int(r["gw"]) in baseline_by_gw
                    and baseline_by_gw[int(r["gw"])].get("points") is not None),
                2) if n_gw else None,
            "mean_delta": mean,
            "delta_se": se,
            "delta_ci95": None if lo is None else [lo, hi],
            "beats_baseline": (None if lo is None else
                               (True if lo > 0 else False if hi < 0 else None)),
            "latest_overall_rank": next(
                (r["overall_rank"] for r in reversed(rows)
                 if r.get("overall_rank") is not None), None),
            "quotable": False,
            "gws": rows,
            "reason": reason,
        })
    people.sort(key=lambda p: (p["n_gw"] == 0, str(p["person"] or "")))
    measured = [p for p in people if p["n_gw"] > 0]
    return {
        "channel": "team",
        "kind": "measured",
        "measured": bool(measured),
        "quotable": False,
        "baseline": {
            "kind": baseline_kind,
            "label": baseline_label,
            "reason": baseline_reason,
            "by_gw": [
                {"gw": gw, "points": _r(v.get("points"), 2), "n": v.get("n")}
                for gw, v in sorted(baseline_by_gw.items())
            ],
        },
        "min_gw_measured": int(min_gw),
        "people": people,
        "reason": (
            "no verified FPL entry is attached to this creator, so no squad "
            "of theirs can be read. An unverified id renders a stranger's "
            "team under their name, so none is guessed."
            if not people else
            f"{len(measured)} of {len(people)} verified entrant(s) have "
            f"crawled gameweeks. A points delta is never quotable as a rank: "
            f"at a field standard deviation near 20 points, resolving a "
            f"5-point-per-gameweek edge at 95% needs roughly 61 gameweeks, "
            f"which is more than a season."
        ),
    }


# Re-exported so a caller never reaches past this module for the floor that
# governs the card it just received.
_ = earned_weight
