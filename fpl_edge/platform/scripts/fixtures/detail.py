"""`fixture_detail`: one fixture expanded into its ten blocks."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import pandas as pd

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import UTC, empty, q, season_param
from fpl_edge.platform.scripts.fixtures.board import (
    _lens,
    _market_state,
    _resolved_odds,
    _team_form,
)
from fpl_edge.platform.scripts.fixtures.constants import (
    FORM_WINDOW,
    INPUT_SCHEMA,
    INTEL_STALE_HOURS,
    LINEUP_STALE_HOURS,
    ODDS_STALE_HOURS,
    ODDS_USELESS_HOURS,
    RATINGS_NAME,
    RATINGS_STALE_HOURS,
    _hours_since,
    _input_row,
    _iso,
)
from fpl_edge.platform.scripts.fixtures.ratings import _Ratings, load_ratings

# ---------------------------------------------------------------------------
# fixture_detail -- one fixture, expanded
# ---------------------------------------------------------------------------

#: Model and market are shown SIDE BY SIDE and flagged when they disagree by
#: more than this, never averaged. They are two estimators with different
#: biases and the mean would hide the signal their gap carries -- the rule
#: odds_derivation.md section 4 already set for competing clean-sheet methods.
DISAGREE_PP = 3.0

DETAIL_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fixture_id"],
    "properties": {
        "season": season_param(),
        "fixture_id": {"type": "integer", "minimum": 1},
        "as_of": {"type": ["string", "null"], "default": None},
        "meetings_limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
    },
}

_SIDE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["team_code", "short_name"],
    "properties": {
        "team_code": {"type": "integer"},
        "short_name": {"type": ["string", "null"]},
        "name": {"type": ["string", "null"]},
        "is_home": {"type": "boolean"},
    },
}

_BLOCK = {"type": ["object", "null"], "additionalProperties": True}

DETAIL_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "fixture_id", "gw", "home", "away", "as_of", "inputs"],
    "properties": {
        "season": {"type": "string"},
        "fixture_id": {"type": "integer"},
        "gw": {"type": "integer"},
        "as_of": {"type": "string"},
        "kickoff_utc": {"type": ["string", "null"]},
        "finished": {"type": "boolean"},
        "score": {"type": ["object", "null"], "additionalProperties": True},
        "home": _SIDE_SCHEMA,
        "away": _SIDE_SCHEMA,
        "inputs": {"type": "array", "items": INPUT_SCHEMA},
        "model": _BLOCK,
        "market": _BLOCK,
        "derived_clean_sheet": _BLOCK,
        "disagreement": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "form": _BLOCK,
        "team_news": _BLOCK,
        "intel": _BLOCK,
        "predicted_lineups": _BLOCK,
        "previous_meetings": _BLOCK,
        "creator_team_talk": _BLOCK,
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}


def _gap(reason: str, **extra: Any) -> dict[str, Any]:
    """A named gap. Not whitespace, not a zero -- a sentence a reader can act on."""
    return {"available": False, "unavailable": reason, **extra}


def _section(label: str, build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one OPTIONAL section builder; an unexpected crash becomes a named gap.

    The same rule `_safe_q` applies to a missing table, applied to the whole
    section: a data edge in team intel (a NULL `ord` in set_piece_duty took the
    entire drawer down for 21 of 60 fixtures) must degrade to the house
    honest-empty shape for THAT section, loudly, instead of 500ing the panel.
    The exception type is named so the gap is a bug report, not whitespace --
    dropped loudly, never silently. The model/market core stays unguarded on
    purpose: a crash there means the panel has no headline number and should
    fail its run visibly through the API error contract.
    """
    try:
        return build()
    except Exception as exc:  # noqa: BLE001 - converted to a named, rendered gap
        return _gap(
            f"the {label} section crashed on this fixture's data "
            f"({type(exc).__name__}: {exc}) and is dropped rather than guessed "
            f"-- a data edge worth reporting, not an empty week"
        )


#: Tables created by feature migrations rather than by the base schema. A fresh
#: clone, or a warehouse whose migrations have only partly run, genuinely does
#: not have them -- ``intel_item``, ``set_piece_duty`` and ``content_insight``
#: are all absent from ``Warehouse()``'s own CREATE list. A drilldown that
#: crashed on that would take the whole panel down over an optional section.
def _safe_q(wh, table: str, sql: str, params: tuple = ()) -> tuple[pd.DataFrame, str | None]:
    """Query an OPTIONAL table. A missing table is a named gap, not a traceback."""
    try:
        return q(wh, sql, params), None
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if "does not exist" in text or "Catalog Error" in text:
            return pd.DataFrame(), (
                f"`{table}` is not in this warehouse. It is created by a feature "
                f"migration that has not run here, so there is nothing to show and "
                f"nothing to infer"
            )
        return pd.DataFrame(), (
            f"`{table}` could not be read ({type(exc).__name__}); the section is "
            f"dropped rather than guessed"
        )


def _model_block(ratings: _Ratings | None, reason: str | None,
                 home: int, away: int, now: dt.datetime) -> dict[str, Any]:
    if ratings is None:
        return _gap(reason or "no fitted ratings artefact")
    if not ratings.has(home, away):
        return _gap(
            "one or both clubs are absent from the stored fit. The fixture was "
            "added or rescheduled after the last ratings build"
        )
    pop = ratings.population()
    anchors = {"anchor_att": pop["anchor_attack_xg"], "anchor_def": pop["anchor_defence_xg"],
               "ref_cs": pop["ref_clean_sheet"], "ref_pen": pop["ref_concede_penalty"]}
    out: dict[str, Any] = {
        "available": True, "unavailable": None,
        "fitted_at": _iso(ratings.fitted_at),
        "age_hours": _hours_since(ratings.fitted_at, now),
        "half_life_days": ratings.half_life_days,
        "n_matches": ratings.n_matches,
        "effective_n": round(ratings.effective_n, 1),
        "rho": round(ratings.rho, 4),
        "converged": ratings.converged,
    }
    for side, me, them, is_home in (("home", home, away, True), ("away", away, home, False)):
        pair = pop["by_pair"][(them, is_home)]
        mu_a, lam_a = ratings.opponent_only_rates(them, is_home)
        mu_f, lam_f = ratings.fixture_rates(me, them, is_home)
        out[side] = {
            "opponent_only": _lens(ratings.quantities(mu_a, lam_a), **anchors,
                                   ranks=(int(pair["attack_rank"]), int(pair["defence_rank"]))),
            "fixture_specific": _lens(ratings.quantities(mu_f, lam_f), **anchors),
            "rating": {"attack": round(ratings.attack[me], 4),
                       "defence": round(ratings.defence[me], 4),
                       **ratings.in_goals(me),
                       "is_promoted": me in ratings.promoted,
                       "matches_seen": ratings.matches_seen.get(me, 0)},
        }
    # The 1X2 view: one matrix, home orientation, own strengths in.
    from fpl_edge.models.team_goals.scoreline import (
        GoalRates,
        outcome_probs,
        prob_over,
        score_matrix,
    )
    mu, lam = ratings.fixture_rates(home, away, True)
    mat = score_matrix(GoalRates(mu, lam, ratings.rho))
    p_h, p_d, p_a = outcome_probs(mat)
    out["match"] = {
        "p_home_win": round(p_h, 4), "p_draw": round(p_d, 4), "p_away_win": round(p_a, 4),
        "p_over_2_5": round(prob_over(mat, 2.5), 4),
        "home_xg": round(mu, 4), "away_xg": round(lam, 4),
        "basis": "fixture_specific: both clubs' own fitted ratings, which is the "
                 "honest prediction. The board's colour uses opponent_only instead.",
    }
    return out


def _market_block(wh, season: str, fixture_id: int, now: dt.datetime,
                  rho: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """De-vigged prices and the goal rates they imply, plus their age.

    Returns ``(market, derived_clean_sheet)``. The second is kept separate and
    is NOT called a market: all 3,260 ``clean_sheet`` rows in ``fact_odds``
    carry ``bookmaker = 'derived#poisson'`` -- they are this repo's own Poisson
    inversion written back, not a quote anybody posted. Calling a derivation a
    bookmaker price would be the most misleading thing this page could do.
    """
    odds, reason = _resolved_odds(wh, season, now)
    if odds.empty:
        return _gap(reason or "no odds"), _gap(reason or "no odds")
    mine = odds[odds["fixture_id"] == fixture_id]
    if mine.empty:
        return (_gap(f"no bookmaker has quoted fixture {fixture_id}; "
                     f"books typically open a Premier League match around a week out"),
                _gap(f"no derived clean-sheet row for fixture {fixture_id}"))

    cs_rows = mine[mine["market"] == "clean_sheet"]
    derived: dict[str, Any]
    if cs_rows.empty:
        derived = _gap("no derived clean-sheet row for this fixture")
    else:
        cs_age = _hours_since(cs_rows["as_of"].max(), now)
        derived = {
            "available": True, "unavailable": None,
            "is_a_market": False,
            "method": str(cs_rows["bookmaker"].iloc[0]),
            "as_of": _iso(cs_rows["as_of"].max()),
            "age_hours": None if cs_age is None else round(cs_age, 2),
            "p_home_clean_sheet": None, "p_away_clean_sheet": None,
            "warning": (
                "This is NOT a posted market. Every clean_sheet row in fact_odds "
                "carries bookmaker='derived#poisson'. It is our own inversion of "
                "the 1X2 and totals prices, written back. Books do post a real "
                "clean-sheet market; this warehouse does not ingest it."
            ),
        }
        for sel, key in (("home", "p_home_clean_sheet"), ("away", "p_away_clean_sheet")):
            hit = cs_rows[cs_rows["selection"] == sel]
            if not hit.empty:
                derived[key] = round(float(1.0 / hit["price_decimal"].mean()), 4)

    priced = mine[mine["market"].isin(["h2h", "totals"])]
    if priced.empty:
        return _gap("only derived rows exist for this fixture; no 1X2 or totals "
                    "price to de-vig"), derived

    from fpl_edge.models.team_goals.market import invert_odds
    from fpl_edge.models.team_goals.odds import devig_frame

    quotes = devig_frame(priced, method="proportional")
    key = f"{season}:{fixture_id}"
    fo = quotes.get(key)
    if fo is None:
        return _gap(
            "the 1X2 rows for this fixture are incomplete. No single bookmaker "
            "quoted all three selections, so there is nothing to de-vig"
        ), derived

    age = _hours_since(priced["as_of"].max(), now)
    state = _market_state(age)
    inv = invert_odds(fo, rho=rho)
    from fpl_edge.models.team_goals.scoreline import GoalRates, score_matrix
    mat = score_matrix(GoalRates(inv.rates.home, inv.rates.away, rho))
    market = {
        "available": True, "unavailable": None,
        "state": state,
        "as_of": _iso(priced["as_of"].max()),
        "age_hours": None if age is None else round(age, 2),
        "n_books": int(fo.n_books),
        "devig_method": "proportional",
        "overround_h2h": round(fo.overround_h2h, 4),
        "overround_totals": None if fo.overround_totals is None else round(fo.overround_totals, 4),
        "p_home_win": round(fo.p_home, 4),
        "p_draw": round(fo.p_draw, 4),
        "p_away_win": round(fo.p_away, 4),
        "p_over_2_5": None if fo.p_over is None else round(fo.p_over, 4),
        "totals_line": fo.totals_line,
        "implied": {
            "home_xg": round(inv.rates.home, 4),
            "away_xg": round(inv.rates.away, 4),
            "residual": round(inv.residual, 6),
            "used_totals": bool(inv.used_totals),
            "p_home_clean_sheet": round(float(mat[:, 0].sum()), 4),
            "p_away_clean_sheet": round(float(mat[0, :].sum()), 4),
            "note": (
                "Two unknowns against three or four constraints, so the residual "
                "is informative: a large one means the quoted prices are not "
                "consistent with ANY bivariate Poisson, which is a data-quality "
                "signal rather than something to absorb silently."
            ),
        },
        "staleness_effect": {
            "priced": "the price is inside the 12h window and reflects current team news",
            "stale": f"older than {ODDS_STALE_HOURS:.0f}h; shown, but it predates any press conference since",
            "expired": f"older than {ODDS_USELESS_HOURS:.0f}h; shown greyed as a contrast only",
            "unpriced": "no quote",
        }[state],
        "casing_workaround": (
            "fact_odds stores selections upper-case (HOME, OVER_2.5) while "
            "team_goals.odds.devig_frame matches lower-case; this panel lowers "
            "them before de-vigging. Unpatched, devig_frame returns zero fixtures "
            "against a warehouse holding 131,921 odds rows."
        ),
    }
    return market, derived


def _disagreement(model: dict[str, Any], market: dict[str, Any]) -> list[dict[str, Any]]:
    """Where the two estimators part company, in percentage points. Never averaged."""
    if not model.get("available") or not market.get("available"):
        return []
    pairs = [
        ("P(home win)", model["match"]["p_home_win"], market["p_home_win"]),
        ("P(draw)", model["match"]["p_draw"], market["p_draw"]),
        ("P(away win)", model["match"]["p_away_win"], market["p_away_win"]),
        ("P(over 2.5)", model["match"]["p_over_2_5"], market.get("p_over_2_5")),
        ("P(home clean sheet)", model["home"]["fixture_specific"]["p_clean_sheet"],
         market["implied"]["p_home_clean_sheet"]),
        ("P(away clean sheet)", model["away"]["fixture_specific"]["p_clean_sheet"],
         market["implied"]["p_away_clean_sheet"]),
    ]
    out = []
    for label, m, k in pairs:
        if m is None or k is None:
            continue
        gap = (m - k) * 100.0
        out.append({
            "metric": label, "model": round(m, 4), "market": round(k, 4),
            "gap_pp": round(gap, 2), "flagged": abs(gap) >= DISAGREE_PP,
            "market_age_hours": market["age_hours"],
        })
    return out


def _news_block(wh, season: str, codes: tuple[int, int], now: dt.datetime) -> dict[str, Any]:
    rows = q(wh, """
        WITH st AS (SELECT * EXCLUDE(rn) FROM (
              SELECT *, row_number() OVER (PARTITION BY season, code ORDER BY as_of DESC) rn
              FROM fact_player_state WHERE season = ? AND as_of <= ?) WHERE rn = 1),
             pl AS (SELECT season, code, web_name, team_code, position FROM (
              SELECT *, row_number() OVER (PARTITION BY season, code ORDER BY as_of DESC) rn
              FROM dim_player WHERE season = ? AND as_of <= ?) WHERE rn = 1)
        SELECT pl.team_code, pl.web_name, pl.position, st.status,
               st.chance_of_playing_next_round AS chance, st.news, st.news_added,
               st.selected_by_pct, st.as_of
        FROM st JOIN pl ON pl.season = st.season AND pl.code = st.code
        WHERE pl.team_code IN (?, ?) AND st.status <> 'a'
        ORDER BY st.selected_by_pct DESC
    """, (season, now, season, now, int(codes[0]), int(codes[1])))
    if rows.empty:
        return _gap(
            "no player at either club carries a non-available status at this "
            "instant, which is a real answer, not a missing one"
        )
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows.itertuples(index=False):
        by.setdefault(str(int(r.team_code)), []).append({
            "web_name": str(r.web_name), "position": int(r.position),
            "status": str(r.status),
            "chance_of_playing": None if pd.isna(r.chance) else int(r.chance),
            "news": None if r.news is None else str(r.news),
            "news_added": _iso(r.news_added),
            "news_age_hours": _hours_since(r.news_added, now),
            "selected_by_pct": float(r.selected_by_pct),
        })
    return {"available": True, "unavailable": None, "by_team": by,
            "as_of": _iso(rows["as_of"].max()),
            "age_hours": _hours_since(rows["as_of"].max(), now),
            "note": "Ordered by ownership, so the notes that move decisions lead. "
                    "Every row carries its own timestamp: a three-day-old injury "
                    "note is not the same claim as a three-hour-old one."}


def _intel_block(wh, season: str, codes: tuple[int, int], now: dt.datetime) -> dict[str, Any]:
    items, items_gap = _safe_q(wh, "intel_item", """
        SELECT kind, team_code, headline, body, source, source_url, confidence,
               published_at, observed_at
        FROM intel_item
        WHERE season = ? AND observed_at <= ? AND team_code IN (?, ?)
          AND kind IN ('set_piece', 'press_conference')
        ORDER BY published_at DESC
    """, (season, now, int(codes[0]), int(codes[1])))
    duties, duties_gap = _safe_q(wh, "set_piece_duty", """
        WITH d AS (SELECT * EXCLUDE(rn) FROM (
              SELECT *, row_number() OVER (
                PARTITION BY season, code, duty ORDER BY as_of DESC) rn
              FROM set_piece_duty WHERE season = ? AND as_of <= ?) WHERE rn = 1),
             pl AS (SELECT season, code, web_name FROM (
              SELECT *, row_number() OVER (PARTITION BY season, code ORDER BY as_of DESC) rn
              FROM dim_player WHERE season = ? AND as_of <= ?) WHERE rn = 1)
        SELECT d.team_code, d.duty, d.ord, pl.web_name, d.source, d.as_of
        FROM d LEFT JOIN pl ON pl.season = d.season AND pl.code = d.code
        WHERE d.team_code IN (?, ?)
        ORDER BY d.team_code, d.duty, d.ord
    """, (season, now, season, now, int(codes[0]), int(codes[1])))

    missing = "; ".join(g for g in (items_gap, duties_gap) if g)
    out: dict[str, Any] = {
        "available": bool(len(items) or len(duties)),
        "unavailable": None if len(items) or len(duties) else (
            missing or "no team-level set-piece or press-conference item for either club"),
        "framing": (
            "Duty, meaning who takes them, never a team trait: set-piece "
            "goals-over-expected barely persists season to season, so 'this "
            "player takes the corners' is durable and 'this club is good at "
            "set pieces' is not."
        ),
        "set_piece_duty": {}, "set_piece_items": [], "press_conference": [],
        "as_of": None, "age_hours": None,
    }
    for r in duties.itertuples(index=False):
        out["set_piece_duty"].setdefault(str(int(r.team_code)), []).append({
            # `ord` is NULL for some bootstrap-static rows (the FPL API lists a
            # taker without ranking him). That is an absent order, not a zero
            # and not an error: `int(NaN)` here was the crash that 500'd every
            # fixture involving those clubs -- 21 of 60 board cells, silently.
            "duty": str(r.duty),
            "order": None if pd.isna(r.ord) else int(r.ord),
            "player": None if r.web_name is None else str(r.web_name),
            "source": str(r.source), "as_of": _iso(r.as_of),
        })
    for r in items.itertuples(index=False):
        entry = {
            "team_code": int(r.team_code), "headline": str(r.headline),
            "body": None if r.body is None else str(r.body),
            "source": str(r.source),
            "source_url": None if r.source_url is None else str(r.source_url),
            "confidence": None if pd.isna(r.confidence) else float(r.confidence),
            "published_at": _iso(r.published_at),
            "age_hours": _hours_since(r.published_at, now),
        }
        out["set_piece_items" if r.kind == "set_piece" else "press_conference"].append(entry)
    stamps = [s for s in (
        None if items.empty else items["observed_at"].max(),
        None if duties.empty else duties["as_of"].max(),
    ) if s is not None]
    if stamps:
        newest = max(pd.to_datetime(s, utc=True) for s in stamps)
        out["as_of"] = _iso(newest)
        out["age_hours"] = _hours_since(newest, now)
    return out


def _lineups_block(wh, season: str, gw: int, codes: tuple[int, int],
                   now: dt.datetime) -> dict[str, Any]:
    # Latest SNAPSHOT per team, not latest ROW per player. When rotowire drops a
    # player from the XI it stops emitting a row for them rather than writing
    # predicted_start = false, so a per-player "latest" resurrects every player
    # who was ever named: Palace's GW2 XI came back with thirteen starters, the
    # eleven plus two dropped a day earlier. A player absent from the newest
    # snapshot is not in the XI.
    rows = q(wh, """
        WITH vis AS (SELECT * FROM fact_predicted_lineup
                     WHERE season = ? AND gw = ? AND as_of <= ?),
             newest AS (SELECT provider, team_code, max(as_of) AS mx
                        FROM vis GROUP BY 1, 2),
             lu AS (SELECT vis.* FROM vis JOIN newest
                      ON newest.provider = vis.provider
                     AND newest.team_code = vis.team_code
                     AND newest.mx = vis.as_of),
             pl AS (SELECT season, code, web_name, position FROM (
              SELECT *, row_number() OVER (PARTITION BY season, code ORDER BY as_of DESC) rn
              FROM dim_player WHERE season = ? AND as_of <= ?) WHERE rn = 1)
        SELECT lu.provider, lu.team_code, lu.code, pl.web_name, pl.position,
               lu.predicted_start, lu.certainty, lu.as_of
        FROM lu LEFT JOIN pl ON pl.season = lu.season AND pl.code = lu.code
        WHERE lu.team_code IN (?, ?)
        ORDER BY lu.team_code, lu.predicted_start DESC, lu.certainty DESC
    """, (season, int(gw), now, season, now, int(codes[0]), int(codes[1])))
    if rows.empty:
        return _gap(
            f"no predicted XI for GW{gw} yet. rotowire publishes roughly 48 hours "
            f"before kickoff, so this is expected until then rather than missing."
        )
    age = _hours_since(rows["as_of"].max(), now)
    by: dict[str, Any] = {}
    for r in rows.itertuples(index=False):
        by.setdefault(str(int(r.team_code)), []).append({
            "web_name": None if r.web_name is None else str(r.web_name),
            "position": None if pd.isna(r.position) else int(r.position),
            "predicted_start": bool(r.predicted_start),
            # `certainty` is a VARCHAR label the provider chooses -- 'expected',
            # 'questionable', 'out', 'suspended' -- not a probability. Passed
            # through verbatim; coercing it to a number would invent a scale.
            "certainty": None if r.certainty is None else str(r.certainty),
        })
    return {
        "available": True, "unavailable": None,
        "provider": str(rows["provider"].iloc[0]),
        "gw": int(gw), "by_team": by,
        "as_of": _iso(rows["as_of"].max()),
        "age_hours": None if age is None else round(age, 2),
        "state": "fresh" if (age or 0) <= LINEUP_STALE_HOURS else "stale",
        "effect_when_stale": "a predicted XI older than a day and a half predates "
                             "the press conference that usually settles it",
    }


def _meetings_block(wh, home: int, away: int, now: dt.datetime, limit: int) -> dict[str, Any]:
    rows = q(wh, """
        SELECT season, gw, kickoff_utc, team_code, opponent_code, is_home,
               goals_for, goals_against, fixture_id
        FROM sem_fixtures(?)
        WHERE finished AND ((team_code = ? AND opponent_code = ?))
        ORDER BY kickoff_utc DESC LIMIT ?
    """, (now, int(home), int(away), int(limit)))
    if rows.empty:
        return _gap(
            "these two clubs have not met in a season this warehouse holds. There "
            "is nothing to show and nothing to infer."
        )
    xg = q(wh, """
        WITH fx AS (SELECT * FROM sem_fixtures(?) WHERE finished),
             pl AS (SELECT season, code, team_code FROM (
                      SELECT *, row_number() OVER (
                        PARTITION BY season, code ORDER BY as_of DESC) rn
                      FROM dim_player WHERE as_of <= ?) WHERE rn = 1),
             pf AS (SELECT * FROM sem_player_form(?))
        SELECT fx.fixture_id, fx.team_code, SUM(pf.expected_goals) AS xg_for
        FROM fx JOIN pl ON pl.season = fx.season AND pl.team_code = fx.team_code
                JOIN pf ON pf.season = fx.season AND pf.fixture_id = fx.fixture_id
                       AND pf.code = pl.code
        WHERE fx.team_code IN (?, ?)
        GROUP BY 1, 2
    """, (now, now, now, int(home), int(away)))
    xg_map = {(int(r.fixture_id), int(r.team_code)): round(float(r.xg_for), 2)
              for r in xg.itertuples(index=False)}
    matches = []
    for r in rows.itertuples(index=False):
        matches.append({
            "season": str(r.season), "gw": int(r.gw),
            "kickoff_utc": _iso(r.kickoff_utc),
            "venue": "home" if bool(r.is_home) else "away",
            "goals_for": None if pd.isna(r.goals_for) else int(r.goals_for),
            "goals_against": None if pd.isna(r.goals_against) else int(r.goals_against),
            "xg_for": xg_map.get((int(r.fixture_id), int(r.team_code))),
            "xg_against": xg_map.get((int(r.fixture_id), int(r.opponent_code))),
        })
    return {
        "available": True, "unavailable": None,
        "orientation": "from the home club's point of view, both venues",
        "matches": matches,
        "caution": (
            f"{len(matches)} matches across several seasons, different managers "
            f"and mostly different players: not evidence about this one. "
            f"Head-to-head is the most over-read object in fixture analysis."
        ),
    }


def _team_talk_block(wh, season: str, codes: tuple[int, int], now: dt.datetime) -> dict[str, Any]:
    have, gap = _safe_q(wh, "content_insight", "SELECT count(*) AS n FROM content_insight")
    if gap is not None:
        return _gap(gap, rows=0)
    total = 0 if have.empty else int(have.iloc[0]["n"])
    if total == 0:
        return _gap(
            "content_insight holds 0 rows. The extraction is wired into both "
            "writers now, so this means no analysed item has yet produced a "
            "team-level observation. Run `fpl-content backfill-insights` to "
            "recover them from analyses already on disk.",
            rows=0,
        )
    # Filtered to THIS fixture's two clubs. It previously took `codes` and
    # ignored them, returning every team-level insight in the season -- so an
    # Arsenal v Villa drawer showed opinions about Hull and Everton under a
    # heading that said they were about this match.
    rows, _ = _safe_q(wh, "content_insight", """
        SELECT creator, topic, entity_kind, entity_name, team_code, claim_text,
               quote, start_s, confidence, published_at, extractor
        FROM content_insight
        WHERE season = ? AND entity_kind = 'team' AND published_at <= ?
          AND team_code IN (?, ?)
        ORDER BY published_at DESC LIMIT 40
    """, (season, now, int(codes[0]), int(codes[1])))
    # An unattributable insight is counted, never shown: the reader is told how
    # much opinion exists that could not be tied to a club, rather than being
    # left to assume the silence means nobody said anything.
    unresolved, _ = _safe_q(wh, "content_insight", """
        SELECT count(*) AS n FROM content_insight
        WHERE season = ? AND entity_kind = 'team' AND published_at <= ?
          AND team_code IS NULL
    """, (season, now))
    n_unresolved = 0 if unresolved.empty else int(unresolved.iloc[0]["n"])
    unresolved_note = (
        f" {n_unresolved} team-level insight(s) this season name a club the "
        f"resolver refused to guess at (ASR mangles club names; this "
        f"warehouse holds 'suddenland' and 'ipsswitch'); they are excluded "
        f"here rather than attached to the nearest-looking club."
        if n_unresolved else ""
    )
    if rows.empty:
        return _gap(
            f"content_insight holds {total} rows, none of them a team-level "
            f"insight about either of these clubs for {season} at this "
            f"instant.{unresolved_note}", rows=total)
    return {
        "available": True, "unavailable": None,
        "items": rows.to_dict("records"),
        "note": "Clubs are resolved once at write time by exact then "
                "containment match, never by edit distance. On this season's "
                "twenty clubs nearest-match sends 'forester' to Brentford."
                + unresolved_note,
    }


def fixture_detail(
    wh, *, season: str, fixture_id: int, as_of: str | None = None,
    meetings_limit: int = 8,
) -> dict[str, Any]:
    """One fixture, expanded: model, market, form, news, XI, meetings, talk.

    Every section carries its own age and its own reason for being empty. The
    model and the market are shown side by side and flagged where they disagree
    by three percentage points or more; they are never averaged, because they
    are two estimators with different biases and the mean would hide the one
    signal their gap carries.

    The clean-sheet numbers in ``fact_odds`` are served under
    ``derived_clean_sheet``, never under ``market``: all 3,260 of them carry
    ``bookmaker='derived#poisson'`` and are this repo's own inversion written
    back, not a price anybody posted.
    """
    now = dt.datetime.now(UTC)
    if as_of:
        parsed = pd.to_datetime(as_of, utc=True, errors="coerce")
        if parsed is pd.NaT or pd.isna(parsed):
            return empty(f"as_of={as_of!r} is not an ISO instant; nothing was read.")
        now = parsed.to_pydatetime()

    fx = q(wh, """
        SELECT fixture_id, gw, kickoff_utc, finished, team_code, opponent_code,
               is_home, team, opponent, goals_for, goals_against
        FROM sem_fixtures(?) WHERE season = ? AND fixture_id = ?
    """, (now, season, int(fixture_id)))
    if fx.empty:
        return empty(
            f"No {season} fixture {fixture_id} is known at {now.isoformat()}. "
            f"Fixture ids come from fact_fixture; run `make ingest` if the "
            f"schedule is behind."
        )
    home_row = fx[fx["is_home"]].iloc[0]
    away_row = fx[~fx["is_home"]].iloc[0]
    home, away = int(home_row["team_code"]), int(away_row["team_code"])
    gw = int(home_row["gw"])

    names = q(wh, """
        SELECT team_code, short_name, name FROM (
          SELECT *, row_number() OVER (
            PARTITION BY season, team_code ORDER BY as_of DESC) rn
          FROM dim_team WHERE season = ? AND as_of <= ?) WHERE rn = 1
    """, (season, now))
    full = {int(r.team_code): (str(r.short_name), None if r.name is None else str(r.name))
            for r in names.itertuples(index=False)}

    ratings, ratings_reason = load_ratings(wh, season)
    model = _model_block(ratings, ratings_reason, home, away, now)
    rho = ratings.rho if ratings is not None else 0.0
    market, derived = _market_block(wh, season, int(fixture_id), now, rho)
    form, _, form_newest = _team_form(wh, season, now, ratings)

    codes = (home, away)
    news = _section("team news", lambda: _news_block(wh, season, codes, now))
    intel = _section("team intel", lambda: _intel_block(wh, season, codes, now))
    lineups = _section("predicted XI",
                       lambda: _lineups_block(wh, season, gw, codes, now))
    meetings = _section("previous meetings",
                        lambda: _meetings_block(wh, home, away, now, meetings_limit))
    talk = _section("creator team-talk",
                    lambda: _team_talk_block(wh, season, codes, now))

    inputs = [
        _input_row("fitted ratings", source=RATINGS_NAME,
                   as_of=None if ratings is None else ratings.fitted_at, now=now,
                   stale_after_hours=RATINGS_STALE_HOURS,
                   rows=None if ratings is None else len(ratings.codes),
                   missing=ratings is None,
                   effect_when_stale="the fit predates recent results; numbers still shown",
                   detail=(ratings_reason or "no fitted ratings artefact"
                           if ratings is None
                           else f"Dixon-Coles, {ratings.n_matches} matches")),
        _input_row("market odds", source="fact_odds h2h + totals",
                   as_of=market.get("as_of"), now=now,
                   stale_after_hours=ODDS_STALE_HOURS,
                   rows=market.get("n_books"), missing=not market.get("available"),
                   effect_when_stale=(
                       f"past {ODDS_USELESS_HOURS:.0f}h the price is shown only as a "
                       f"contrast; it is never blended into the model number"),
                   detail=market.get("unavailable") or
                          f"{market.get('n_books')} books, de-vigged proportionally"),
        _input_row("derived clean sheet", source="fact_odds bookmaker='derived#poisson'",
                   as_of=derived.get("as_of"), now=now,
                   stale_after_hours=ODDS_STALE_HOURS, rows=1 if derived.get("available") else 0,
                   missing=not derived.get("available"),
                   effect_when_stale="inherits the staleness of the prices it was derived from",
                   detail="our own Poisson inversion written back, NOT a posted market"),
        _input_row("predicted XI", source="fact_predicted_lineup",
                   as_of=lineups.get("as_of"), now=now,
                   stale_after_hours=LINEUP_STALE_HOURS,
                   rows=None if not lineups.get("available") else
                        sum(len(v) for v in lineups["by_team"].values()),
                   missing=not lineups.get("available"),
                   effect_when_stale="the XI predates the press conference that usually settles it",
                   detail=lineups.get("unavailable") or f"provider {lineups.get('provider')}"),
        _input_row("team intel", source="intel_item (set_piece, press_conference) + set_piece_duty",
                   as_of=intel.get("as_of"), now=now, stale_after_hours=INTEL_STALE_HOURS,
                   rows=(len(intel.get("set_piece_items", [])) +
                         len(intel.get("press_conference", []))),
                   missing=not intel.get("available"),
                   effect_when_stale="a set-piece order can change in a single training week",
                   detail=intel.get("unavailable") or "team-keyed intel; nothing else in the UI renders it"),
        _input_row("team news", source="fact_player_state",
                   as_of=news.get("as_of"), now=now, stale_after_hours=24.0,
                   rows=None if not news.get("available") else
                        sum(len(v) for v in news["by_team"].values()),
                   missing=not news.get("available"),
                   effect_when_stale="an injury flag can be lifted an hour before a deadline",
                   detail=news.get("unavailable") or "non-available statuses, ordered by ownership"),
        _input_row("creator team-talk", source="content_insight",
                   as_of=None, now=now, stale_after_hours=None, rows=talk.get("rows", 0),
                   missing=not talk.get("available"),
                   effect_when_stale="none; the extraction is not wired up at all",
                   detail=talk.get("unavailable") or "team-level insights"),
    ]

    notes = [
        ("The model number here is FIXTURE-SPECIFIC: both clubs' own fitted "
         "ratings are in it. The board's colour is opponent-only, which is a "
         "different number answering a different question. Both are on every cell."),
    ]
    if market.get("available") and market.get("state") in ("stale", "expired"):
        notes.append(
            f"The market price is {market['age_hours']:.0f} hours old "
            f"({market['state']}). It is shown for contrast and is not in any "
            f"model number on this page."
        )

    return {
        "season": season,
        "fixture_id": int(fixture_id),
        "gw": gw,
        "as_of": now.isoformat(),
        "kickoff_utc": _iso(home_row["kickoff_utc"]),
        "finished": bool(home_row["finished"]),
        "score": None if not bool(home_row["finished"]) else {
            "home": None if pd.isna(home_row["goals_for"]) else int(home_row["goals_for"]),
            "away": None if pd.isna(home_row["goals_against"]) else int(home_row["goals_against"]),
        },
        "home": {"team_code": home, "short_name": full.get(home, (None, None))[0],
                 "name": full.get(home, (None, None))[1], "is_home": True},
        "away": {"team_code": away, "short_name": full.get(away, (None, None))[0],
                 "name": full.get(away, (None, None))[1], "is_home": False},
        "inputs": inputs,
        "model": model,
        "market": market,
        "derived_clean_sheet": derived,
        "disagreement": _disagreement(model, market),
        "form": {
            "home": form.get(home, {"window_matches": 0, "unavailable":
                                    "no completed match for this club at this instant"}),
            "away": form.get(away, {"window_matches": 0, "unavailable":
                                    "no completed match for this club at this instant"}),
            "window": FORM_WINDOW,
            "newest_match": _iso(form_newest),
            "note": "xG for is summed over the club's players; xG against takes one "
                    "representative value per team-match, because "
                    "expected_goals_conceded is written per player and every "
                    "outfielder carries the team's value, so summing it gives "
                    "~30 xGC for a single fixture.",
        },
        "team_news": news,
        "intel": intel,
        "predicted_lineups": lineups,
        "previous_meetings": meetings,
        "creator_team_talk": talk,
        "notes": notes,
    }


register_script(
    "fixture_detail",
    fixture_detail,
    params_schema=DETAIL_PARAMS,
    result_schema=DETAIL_RESULT,
    title="Fixture detail",
    description=(
        "One fixture expanded: model and market side by side with their ages, "
        "clean-sheet and goal probabilities, form, team news, set-piece and "
        "press intel, predicted XIs, previous meetings, creator team-talk."
    ),
)
