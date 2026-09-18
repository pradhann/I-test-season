"""`creator_report_card`: measured track record, never blended into one score."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.eval.creator_report_card import (
    BASELINE_KINDS,
    MIN_GW_MEASURED,
    MIN_SCORED_CLAIMS,
    claims_channel,
    numeric_channel,
    provider_key,
    team_channel,
)
from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import SEASON_DEFAULT, empty, q
from fpl_edge.platform.scripts.creators.identity import (
    _CONTENT_TABLES,
    _PANEL_SHOW_TABLE,
    _PANEL_TABLE,
    UTC,
    _f,
    _i,
    _iso,
    _panel_roster,
    _s,
    _tables_present,
    _weights_as_of,
)

# ---------------------------------------------------------------------------
# creator_report_card -- the backtested view, wherever a claim is shown.
#
# The owner's ask was one sentence: "wherever there are claims, we also get to
# see a backtested view, report card". So this is deliberately NOT part of
# creator_board's payload. It is its own script, it takes one creator, and the
# player drawer, the chat answer and the Creators tab all call the same thing
# and render the same three blocks. A report card computed twice in two places
# is a report card that will eventually say two different numbers.
#
# Three channels, never a blended score. The arithmetic and every sample-size
# rule live in fpl_edge/eval/creator_report_card.py; this function is the
# warehouse read that feeds them, and nothing else.

#: The manager tables the measured-team channel needs. Absent on a warehouse
#: built before the rivals package, which is a stated reason, not a crash.
_MANAGER_TABLES = ("fact_manager_gw", "fact_manager_pick")


def _card_scores(wh, moment: dt.datetime) -> dict[str, dict[str, Any]]:
    """``creator_score`` at ``scope='all'`` in force at ``moment``.

    Exactly ``_weights_as_of``; called through it rather than copied so the
    point-in-time bound has one home.
    """
    return _weights_as_of(wh, moment)


def _card_outcomes(wh, present: set[str], moment: dt.datetime,
                   creators: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Scored ``claim_outcome`` rows per creator, bounded at ``moment``.

    ``resolved_utc`` is the instant the outcome became knowable -- the
    gameweek had finalised and the settlement had landed -- so it is the right
    column to bound on. Bounding on the claim's own ``published_at`` would let
    a card show a verdict on a gameweek that had not been played at ``moment``.

    Only rows with a non-null ``hit`` are returned: an unscoreable claim
    belongs in the denominator of ``claims_total`` (which ``creator_score``
    already holds) and in neither side of a breakdown.
    """
    if "claim_outcome" not in present or not creators:
        return {}
    rows = q(
        wh,
        "SELECT creator, gameweek, action, hit FROM claim_outcome "
        "WHERE hit IS NOT NULL AND resolved_utc <= ? AND creator IN ("
        + ", ".join("?" for _ in creators) + ")",
        (moment, *creators),
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows.to_dict("records"):
        out.setdefault(str(r["creator"]), []).append({
            "gameweek": _i(r.get("gameweek")),
            "action": _s(r.get("action")),
            "hit": bool(r["hit"]),
        })
    return out


def _card_people(wh, present: set[str]) -> tuple[dict[str, list[dict[str, Any]]],
                                                 str | None]:
    """show/creator -> the panel PEOPLE who appear on it, with entry ids.

    ``creator_entry`` is the table that was supposed to carry this and it does
    not: all 29 of its rows have a null ``entry_id`` and a ``method`` of
    ``none``, because the only mapping ever attempted was "find an FPL entry
    whose API name equals the show's name", which fails for every show on the
    list. ``panel_person`` is where the sixteen hand-verified ids actually
    live, keyed by PERSON, joined to shows through ``panel_person_show``.
    Reading ``creator_entry`` here would render every creator as unmapped while
    the ids sat one table away.
    """
    roster, reason = _panel_roster(wh, present)
    if reason is not None:
        return {}, reason
    out: dict[str, list[dict[str, Any]]] = {}
    for person in roster:
        for show in person.get("shows") or []:
            out.setdefault(str(show), []).append(person)
    if not out:
        return {}, ("the panel roster holds people but none is attached to a "
                    "show in panel_person_show, so no creator can be given a "
                    "team")
    return out, None


def _card_gws(wh, present: set[str], moment: dt.datetime,
              entry_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """entry_id -> its measured gameweeks, newest row per (entry, gw).

    Read straight from ``fact_manager_gw`` rather than through
    ``sem_manager_picks``: the picks macro answers "who did they own", and this
    channel's question is "what did the squad SCORE", which is a different
    table with a different grain. Bounded ``as_of <= moment`` like every other
    read in this module.
    """
    if "fact_manager_gw" not in present or not entry_ids:
        return {}
    rows = q(
        wh,
        "SELECT entry_id, gw, points, total_points, overall_rank, "
        "       event_transfers, event_transfers_cost, points_on_bench "
        "FROM ("
        "  SELECT *, row_number() OVER ("
        "    PARTITION BY entry_id, season, gw ORDER BY as_of DESC) rn "
        "  FROM fact_manager_gw WHERE season = ? AND as_of <= ? AND entry_id IN ("
        + ", ".join("?" for _ in entry_ids) + ")"
        ") WHERE rn = 1 ORDER BY entry_id, gw",
        (SEASON_DEFAULT, moment, *entry_ids),
    )
    out: dict[int, list[dict[str, Any]]] = {}
    for r in rows.to_dict("records"):
        entry, gw = _i(r.get("entry_id")), _i(r.get("gw"))
        if entry is None or gw is None:
            continue
        out.setdefault(entry, []).append({
            "gw": gw,
            "points": _i(r.get("points")),
            "bench_points": _i(r.get("points_on_bench")),
            "transfers": _i(r.get("event_transfers")),
            "hit_cost": _i(r.get("event_transfers_cost")),
            "overall_rank": _i(r.get("overall_rank")),
        })
    return out


def _card_baseline(wh, present: set[str], moment: dt.datetime, kind: str,
                   cohort: dict[int, list[dict[str, Any]]]
                   ) -> tuple[dict[int, dict[str, Any]], str, str]:
    """The number a creator's gameweek is compared against, and what it IS.

    Three baselines, all actually in this warehouse, and the label travels with
    every number so a reader can never mistake one for the other:

    ``field_average``
        FPL's own published ``average_entry_score``: 50 in GW1, 81 in GW2, 51
        in GW3. The number a reader means by "beat the field", and the default.
        It is polled in every bootstrap-static fetch and, until dim_event
        gained the column, was persisted nowhere -- which is why this function
        used to report it as a gap and compare creators against each other
        instead. A gameweek FPL has not settled has no average and is skipped
        rather than compared against a zero.

    ``cohort_mean``
        The mean gameweek score of the OTHER measured panel members on the SAME
        gameweeks. This is ``projection_scoring``'s convention transplanted --
        there the baseline is the all-provider mean on the same observations --
        and it answers the question a reader of this page is actually asking,
        which is "is this creator better than the other creators".

    ``crawled_pool_median``
        The median of every manager in ``fact_manager_gw``. Available, and
        heavily biased: the pool was built by crawling top-1k ranks and
        snowballing their mini-leagues, so its GW2 median of 114 is nothing
        like the game's. Offered because a rival-relative number is sometimes
        what is wanted, labelled so it cannot be read as the field.

    """
    if kind == "field_average":
        rows = q(
            wh,
            "SELECT gw, avg_entry_score, ranked_count FROM ("
            "  SELECT *, row_number() OVER ("
            "    PARTITION BY season, gw ORDER BY as_of DESC) rn "
            "  FROM dim_event WHERE season = ? AND as_of <= ?"
            ") WHERE rn = 1 AND avg_entry_score IS NOT NULL ORDER BY gw",
            (SEASON_DEFAULT, moment),
        )
        by_gw = {
            int(r["gw"]): {"points": _f(r["avg_entry_score"], 2),
                           "n": _i(r.get("ranked_count"))}
            for r in rows.to_dict("records")
            if _i(r.get("gw")) is not None
        }
        if not by_gw:
            return {}, kind, (
                "no gameweek in this season has settled yet, so FPL has "
                "published no average entry score to compare against"
            )
        return by_gw, kind, (
            "FPL's own average_entry_score for the gameweek, over every ranked "
            "entry in the game. This is the field, not a sample of it."
        )

    if kind == "crawled_pool_median":
        if "fact_manager_gw" not in present:
            return {}, kind, ("fact_manager_gw is not in this warehouse, so no "
                              "pool baseline can be computed")
        rows = q(
            wh,
            "SELECT gw, median(points) AS points, count(*) AS n FROM ("
            "  SELECT *, row_number() OVER ("
            "    PARTITION BY entry_id, season, gw ORDER BY as_of DESC) rn "
            "  FROM fact_manager_gw WHERE season = ? AND as_of <= ?"
            ") WHERE rn = 1 GROUP BY gw ORDER BY gw",
            (SEASON_DEFAULT, moment),
        )
        by_gw = {int(r["gw"]): {"points": _f(r["points"], 2), "n": _i(r["n"])}
                 for r in rows.to_dict("records") if _i(r.get("gw")) is not None}
        return by_gw, kind, (
            "median gameweek score of every manager crawled into "
            "fact_manager_gw. This pool was selected by crawling top-1k ranks "
            "and snowballing their mini-leagues, so it sits far above the "
            "game's own average and must not be read as 'the field'."
        )

    # cohort_mean
    per_gw: dict[int, list[int]] = {}
    for rows_ in cohort.values():
        for row in rows_:
            pts = row.get("points")
            if pts is not None:
                per_gw.setdefault(int(row["gw"]), []).append(int(pts))
    by_gw = {gw: {"points": round(sum(v) / len(v), 2), "n": len(v)}
             for gw, v in per_gw.items() if v}
    return by_gw, "cohort_mean", (
        "mean gameweek score of the measured panel members themselves, on the "
        "same gameweeks. This is the convention projection_scoring uses, where "
        "the baseline is the all-provider mean on the same observations. It "
        "answers 'better than the other creators', not 'better than the field'."
    )


def _card_projection_scores(wh, present: set[str], creators: list[str]
                            ) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    """creator -> (provider, its ``fact_projection_score`` rows), where linked.

    The link rule is ``eval.creator_report_card.provider_key``: squash both
    names to alphanumerics and compare. It matches nothing in this warehouse --
    the ingested providers are ``fplform``, ``gh_blueladd``,
    ``gh_apex_airsenal``, ``fpl_ep``, ``gh_fplbench`` and
    ``premierinjuries``, and no creator on the content side shares a name with
    any of them -- which is exactly why the numeric channel reports itself
    absent rather than absent-and-unexplained.
    """
    if "fact_projection_score" not in present or not creators:
        return {}
    rows = q(
        wh,
        "SELECT provider, gw, scope, metric, value, baseline, n_obs "
        "FROM fact_projection_score WHERE season = ? AND scope = 'overall'",
        (SEASON_DEFAULT,),
    )
    if rows.empty:
        return {}
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for r in rows.to_dict("records"):
        by_provider.setdefault(str(r["provider"]), []).append({
            "gw": _i(r.get("gw")), "scope": _s(r.get("scope")),
            "metric": _s(r.get("metric")), "value": _f(r.get("value"), 6),
            "baseline": _f(r.get("baseline"), 6), "n_obs": _i(r.get("n_obs")),
        })
    keyed = {provider_key(p): (p, rs) for p, rs in by_provider.items()}
    return {c: keyed[provider_key(c)] for c in creators
            if provider_key(c) in keyed}


CARD_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        # Null means "every creator with evidence", which is the Creators-tab
        # call. A name means one card, which is what the player drawer and a
        # chat answer ask for.
        "creator": {"type": ["string", "null"], "default": None},
        "baseline": {"enum": list(BASELINE_KINDS), "default": "field_average"},
        # Exposed so a caller can SEE the floor move rather than wonder why a
        # rate is quotable on one surface and not another. It defaults to the
        # same MIN_SCORED_CLAIMS earned_weight() runs on and lowering it does
        # not lower that -- a weight stays unearned either way.
        "min_scored": {"type": "integer", "minimum": 1, "maximum": 1000,
                       "default": MIN_SCORED_CLAIMS},
    },
}

_CI = {"type": ["array", "null"], "items": {"type": "number"},
       "minItems": 2, "maxItems": 2}

_CLAIMS_CHANNEL = {
    "type": "object",
    "required": ["channel", "kind", "measured", "quotable", "n_scored",
                 "hit_rate", "wilson_lo95", "wilson_hi95", "ci95", "weight",
                 "earned", "min_scored_claims", "vs_coin_flip", "reason"],
    "properties": {
        "channel": {"const": "claims"},
        "kind": {"const": "binary"},
        "measured": {"type": "boolean"},
        "quotable": {"type": "boolean"},
        "n_total": {"type": ["integer", "null"]},
        "n_scored": {"type": "integer"},
        "hits": {"type": ["integer", "null"]},
        "hit_rate": {"type": ["number", "null"]},
        "wilson_lo95": {"type": ["number", "null"]},
        "wilson_hi95": {"type": ["number", "null"]},
        "ci95": _CI,
        "weight": {"type": ["number", "null"]},
        "earned": {"type": "boolean"},
        "min_scored_claims": {"type": "integer"},
        "vs_coin_flip": {"enum": ["above", "below", "indistinguishable",
                                  "unmeasured"]},
        "by_gw": {"type": "array", "items": {"type": "object"}},
        "by_action": {"type": "array", "items": {"type": "object"}},
        "first_claim_utc": {"type": ["string", "null"]},
        "last_claim_utc": {"type": ["string", "null"]},
        "reason": {"type": "string"},
    },
}

_NUMERIC_CHANNEL = {
    "type": "object",
    "required": ["channel", "kind", "measured", "quotable", "mae", "rmse",
                 "baseline_mae", "baseline_rmse", "n_obs", "reason"],
    "properties": {
        "channel": {"const": "numeric"},
        "kind": {"const": "continuous"},
        "measured": {"type": "boolean"},
        "quotable": {"type": "boolean"},
        "provider": {"type": ["string", "null"]},
        "mae": {"type": ["number", "null"]},
        "rmse": {"type": ["number", "null"]},
        "baseline_mae": {"type": ["number", "null"]},
        "baseline_rmse": {"type": ["number", "null"]},
        "n_obs": {"type": "integer"},
        "n_gw": {"type": "integer"},
        "by_gw": {"type": "array", "items": {"type": "object"}},
        "reason": {"type": "string"},
    },
}

_TEAM_PERSON = {
    "type": "object",
    "required": ["person", "entry_id", "verified", "n_gw", "mean_delta",
                 "delta_ci95", "quotable", "gws", "reason"],
    "properties": {
        "person": {"type": ["string", "null"]},
        "entry_id": {"type": ["integer", "null"]},
        "entry_name": {"type": ["string", "null"]},
        "verified": {"type": "boolean"},
        "n_gw": {"type": "integer"},
        "points": {"type": ["integer", "null"]},
        "baseline_points": {"type": ["number", "null"]},
        "mean_delta": {"type": ["number", "null"]},
        "delta_se": {"type": ["number", "null"]},
        "delta_ci95": _CI,
        "beats_baseline": {"type": ["boolean", "null"]},
        "latest_overall_rank": {"type": ["integer", "null"]},
        "quotable": {"const": False},
        "gws": {"type": "array", "items": {"type": "object"}},
        "reason": {"type": "string"},
    },
}

_TEAM_CHANNEL = {
    "type": "object",
    "required": ["channel", "kind", "measured", "quotable", "baseline",
                 "min_gw_measured", "people", "reason"],
    "properties": {
        "channel": {"const": "team"},
        "kind": {"const": "measured"},
        "measured": {"type": "boolean"},
        "quotable": {"const": False},
        "baseline": {"type": "object"},
        "min_gw_measured": {"type": "integer"},
        "people": {"type": "array", "items": _TEAM_PERSON},
        "reason": {"type": "string"},
    },
}

_CARD = {
    "type": "object",
    "additionalProperties": False,
    "required": ["creator", "claims", "numeric", "team", "headline",
                 "quotable"],
    "properties": {
        "creator": {"type": "string"},
        "people": {"type": "array", "items": {"type": "object"}},
        "claims": _CLAIMS_CHANNEL,
        "numeric": _NUMERIC_CHANNEL,
        "team": _TEAM_CHANNEL,
        # One sentence a human can read aloud. It NEVER combines the channels
        # into a rating -- it names which channels are measured and what the
        # strongest of them actually supports.
        "headline": {"type": "string"},
        # True only when at least one channel cleared its own floor. Nothing
        # here may be shown as a rank while this is false.
        "quotable": {"type": "boolean"},
    },
}

CARD_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["as_of", "season", "cards", "min_scored_claims",
                 "min_gw_measured", "baseline", "gaps", "note"],
    "properties": {
        "as_of": {"type": "string"},
        "season": {"type": "string"},
        "min_scored_claims": {"type": "integer"},
        "min_gw_measured": {"type": "integer"},
        "baseline": {"type": "object"},
        "cards": {"type": "array", "items": _CARD},
        "gaps": {"type": "array", "items": {
            "type": "object",
            "required": ["key", "what", "fix"],
            "properties": {"key": {"type": "string"},
                           "what": {"type": "string"},
                           "fix": {"type": "string"}},
        }},
        "note": {"type": "string"},
    },
}


def _card_headline(creator: str, claims: dict[str, Any], numeric: dict[str, Any],
                   team: dict[str, Any]) -> tuple[str, bool]:
    """The sentence, and whether ANY channel cleared its own floor.

    Written to be safe to read aloud with no chart beside it. The ordering is
    deliberate: the binary record first because it is the channel with a
    sample-size rule that can actually be met, then the measured team, then the
    numeric channel that is absent. A creator with nothing measured gets a
    sentence saying that, not a blank.
    """
    parts: list[str] = []
    quotable = bool(claims.get("quotable")) or bool(numeric.get("quotable"))
    n = int(claims.get("n_scored") or 0)
    if n:
        rate = claims.get("hit_rate")
        lo, hi = claims.get("wilson_lo95"), claims.get("wilson_hi95")
        verdict = claims.get("vs_coin_flip")
        pct = "?" if rate is None else f"{rate * 100:.0f}%"
        tail = ("a coin flip is inside that interval" if verdict ==
                "indistinguishable" else
                "that interval clears the coin flip" if verdict == "above" else
                "that interval sits below the coin flip")
        parts.append(
            f"{claims.get('hits')} of {n} scored claims hit ({pct}, 95% CI "
            f"{lo}-{hi}): {tail}"
            + ("" if claims.get("quotable") else
               f", and {n} is under the {claims.get('min_scored_claims')}-claim "
               f"floor, so it is not a rank")
        )
    else:
        parts.append("no claim of theirs has been scored yet")

    measured = [p for p in team.get("people") or [] if p["n_gw"] > 0]
    if measured:
        best = max(measured, key=lambda p: p.get("mean_delta") or -1e9)
        read = (f"their team is read for {len(measured)} of "
                f"{len(team.get('people') or [])} verified member(s)")
        delta = best.get("mean_delta")
        if delta is None:
            # A crawled gameweek with no baseline point beside it: the points
            # are known, the comparison is not. Saying so beats a "+0.0".
            parts.append(
                f"{read}; {best['person']} scored {best.get('points')} over "
                f"{best['n_gw']} gameweek(s), with no baseline on those "
                f"gameweeks to compare against"
            )
        else:
            base = (team.get("baseline") or {})
            against = base.get("label") or base.get("kind") or "baseline"
            parts.append(
                f"{read}; {best['person']} averages {delta:+.1f} points per "
                f"gameweek against {against} over {best['n_gw']} gameweek(s), "
                f"which at this sample is a record and not an edge"
            )
    elif team.get("people"):
        parts.append("their verified team has not been crawled yet")
    else:
        parts.append("no verified FPL entry is attached to them")

    if numeric.get("measured"):
        parts.append(
            f"as projection provider '{numeric.get('provider')}' their MAE is "
            f"{numeric.get('mae')} against a baseline of "
            f"{numeric.get('baseline_mae')}"
        )
    else:
        parts.append("they publish no numeric prediction, so MAE/RMSE is empty")

    return f"{creator}: " + "; ".join(parts) + ".", quotable


def creator_report_card(wh, *, creator: str | None = None,
                        baseline: str = "field_average",
                        min_scored: int = MIN_SCORED_CLAIMS) -> dict[str, Any]:
    """One creator's backtested record, in three channels that never merge.

    The panel behind "how did this creator actually do?" -- wherever the
    question is asked. Claims are scored hit/flop by the machinery that already
    earns model weights; their own team is measured from
    ``fact_manager_gw`` for a hand-verified ``entry_id``; the numeric channel
    is MAE/RMSE from ``fact_projection_score`` and is empty today, by
    measurement rather than by omission.

    Nothing here produces an overall creator rating and nothing here should.
    """
    moment = dt.datetime.now(UTC)
    present = _tables_present(
        wh, _CONTENT_TABLES + _MANAGER_TABLES
        + (_PANEL_TABLE, _PANEL_SHOW_TABLE, "claim_outcome", "creator_score",
           "creator_entry", "fact_projection_score"),
    )
    if "creator_score" not in present:
        return empty(
            "creator_score is not in this warehouse, so no creator has a "
            "measured record to report. Build it with `python -m "
            "fpl_edge.ingest.content.pipeline score`."
        )

    scores = _card_scores(wh, moment)
    people_by_show, people_reason = _card_people(wh, present)

    known = sorted(set(scores) | set(people_by_show))
    if creator is not None:
        match = next((k for k in known if k.lower() == creator.lower()), None)
        if match is None:
            return empty(
                f"no creator named {creator!r} has a record or a panel member. "
                f"Known: {', '.join(known) if known else 'none'}."
            )
        known = [match]

    if not known:
        return empty(
            "creator_score exists but holds no creator at or before this "
            "instant, and no panel member is attached to a show, so there is "
            "nothing to report a card for."
        )

    outcomes = _card_outcomes(wh, present, moment, known)
    # The cohort is deliberately EVERY panel member with a verified id, not
    # just the ones on the requested creator's shows: a baseline computed from
    # a one-person cohort is that person's own score, and the delta would be
    # exactly zero by construction. Asking for one card must not change what
    # the baseline means.
    all_ids = sorted({
        int(p["entry_id"]) for people in people_by_show.values()
        for p in people if p.get("entry_id") is not None
    })
    gws = _card_gws(wh, present, moment, all_ids)
    baseline_by_gw, baseline_kind, baseline_reason = _card_baseline(
        wh, present, moment, baseline, gws)
    baseline_label = ("FPL's published average entry score"
                      if baseline_kind == "field_average"
                      else "mean of the measured panel cohort"
                      if baseline_kind == "cohort_mean"
                      else "median of the crawled manager pool")
    numeric_rows = _card_projection_scores(wh, present, known)

    cards: list[dict[str, Any]] = []
    for name in known:
        people = people_by_show.get(name, [])
        entries = [{
            "person": p["person"],
            "entry_id": p["entry_id"],
            "entry_name": p.get("entry_name"),
            "verified": bool(p.get("verified")),
            "gws": gws.get(int(p["entry_id"]), []) if p.get("entry_id") else [],
        } for p in people if p.get("entry_id") is not None]

        claims = claims_channel(scores.get(name), outcomes.get(name),
                                min_scored=min_scored)
        claims["first_claim_utc"] = _iso(claims["first_claim_utc"])
        claims["last_claim_utc"] = _iso(claims["last_claim_utc"])
        provider, rows_ = numeric_rows.get(name, (None, None))
        numeric = numeric_channel(rows_, provider=provider)
        team = team_channel(
            entries, baseline_by_gw, baseline_kind=baseline_kind,
            baseline_label=baseline_label, baseline_reason=baseline_reason)
        if not entries and people_reason:
            # A creator with no verified person and a roster-level reason gets
            # the roster's reason, which names the fixable thing, rather than
            # the generic "no verified entry".
            team["reason"] = people_reason
        headline, quotable = _card_headline(name, claims, numeric, team)
        cards.append({
            "creator": name,
            "people": [{"person": p["person"], "entry_id": p.get("entry_id"),
                        "verified": bool(p.get("verified"))} for p in people],
            "claims": claims,
            "numeric": numeric,
            "team": team,
            "headline": headline,
            "quotable": quotable,
        })
    # Most-measured first. NOT a ranking on skill: the sort key is how much
    # evidence exists, which is the opposite of a leaderboard on hit rate --
    # and a hit-rate sort at these sample sizes would put a 1-for-1 creator on
    # top, which is the single most misleading thing this page could do.
    cards.sort(key=lambda c: (-(c["claims"]["n_scored"] or 0),
                              c["creator"]))

    field_average_gws = (
        len(baseline_by_gw) if baseline_kind == "field_average" else 0)
    gaps = _card_gaps(present, people_by_show, gws, numeric_rows,
                      people_reason, field_average_gws)
    return {
        "as_of": moment.isoformat(),
        "season": SEASON_DEFAULT,
        "min_scored_claims": int(min_scored),
        "min_gw_measured": MIN_GW_MEASURED,
        "baseline": {
            "kind": baseline_kind,
            "label": baseline_label,
            "reason": baseline_reason,
            "by_gw": [{"gw": gw, "points": v.get("points"), "n": v.get("n")}
                      for gw, v in sorted(baseline_by_gw.items())],
        },
        "cards": cards,
        "gaps": gaps,
        "note": (
            "Three channels, never one score. Claims are binary and scored "
            "hit/flop against the positional starter median, with claims "
            "published after their own deadline thrown out. The team channel "
            "is measured fact, not opinion. The numeric channel is MAE/RMSE "
            "and is empty because no tracked creator publishes an "
            "expected-points feed we ingest. No creator has earned a nonzero "
            "model weight and none of these numbers should be read as a rank."
        ),
    }


def _card_gaps(present: set[str], people_by_show: dict[str, list[dict[str, Any]]],
               gws: dict[int, list[dict[str, Any]]],
               numeric_rows: dict[str, Any],
               people_reason: str | None,
               field_average_gws: int = 0) -> list[dict[str, Any]]:
    """What this card could not measure, named, with the thing that would fix it.

    A gap list is the honest half of a report card. Every entry here is a
    checked fact about THIS warehouse at this instant, not a remembered one.
    """
    gaps: list[dict[str, Any]] = []
    if not field_average_gws:
        gaps.append({
            "key": "field_average",
            "what": "no gameweek this season carries FPL's own "
                    "average_entry_score yet, so 'beat the field' has nothing "
                    "to measure against and the team channel falls back on a "
                    "cohort or pool baseline.",
            "fix": "the column exists on dim_event and is written by "
                   "fpl_edge/ingest/fpl_api.py; it fills in as soon as a "
                   "gameweek settles.",
        })
    if "creator_entry" in present:
        gaps.append({
            "key": "creator_entry_empty",
            "what": "creator_entry has a row per creator and a null entry_id "
                    "in every one of them: its only mapping method was "
                    "'an FPL entry whose API name equals the creator name', "
                    "which matches no show. The working mapping is "
                    "panel_person.entry_id, keyed by person, and that is what "
                    "this card reads.",
            "fix": "either backfill creator_entry from panel_person or drop "
                   "it; two tables for one mapping is how a surface ends up "
                   "reading the empty one.",
        })
    verified = {int(p["entry_id"]) for people in people_by_show.values()
                for p in people if p.get("entry_id") is not None}
    unread = sorted(verified - set(gws))
    if unread:
        gaps.append({
            "key": "uncrawled_entries",
            "what": f"{len(unread)} of {len(verified)} verified panel entries "
                    f"have no row in fact_manager_gw: {unread}. Their teams "
                    f"are unread, not empty.",
            "fix": "run the rivals crawl over these entry ids "
                   "(fpl_edge.ingest.rivals) so their gameweek scores land.",
        })
    if people_reason:
        gaps.append({
            "key": "panel_roster",
            "what": people_reason,
            "fix": "python -m fpl_edge.ingest.content.panel load",
        })
    if not numeric_rows:
        gaps.append({
            "key": "no_numeric_channel",
            "what": "no tracked creator is also an ingested projection "
                    "provider, so MAE/RMSE has nothing to measure. Creators "
                    "state binary calls; the numeric feeds in "
                    "fact_projection_score come from providers nobody on the "
                    "content side publishes under.",
            "fix": "ingest a creator-published expected-points feed under a "
                   "provider key that squashes to the creator's name (see "
                   "eval.creator_report_card.provider_key); the channel then "
                   "fills itself.",
        })
    return gaps


register_script(
    name="creator_report_card",
    fn=creator_report_card,
    params_schema=CARD_PARAMS,
    result_schema=CARD_RESULT,
    title="Report card",
    description="One creator backtested in three separate channels: binary "
                "claims scored hit/flop with a Wilson interval, their own "
                "verified FPL team measured per gameweek against a named "
                "baseline, and MAE/RMSE where a numeric prediction exists. "
                "Never blended into one score.",
)
