"""`fixture_board`: the horizon ticker, both lenses per cell."""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import (
    UTC,
    empty,
    latest_as_of,
    next_gw,
    q,
    season_param,
    source_dir,
)
from fpl_edge.platform.scripts.fixtures.constants import (
    ATTACKER_SHARE,
    CALIBRATION_NAME,
    DIVERGENCE_RANKS,
    FORM_MIN_MATCHES,
    FORM_WINDOW,
    FPL_GOAL_POINTS,
    INPUT_SCHEMA,
    ODDS_STALE_HOURS,
    ODDS_USELESS_HOURS,
    RATINGS_NAME,
    RATINGS_STALE_HOURS,
    SCALE_DOMAIN,
    _hours_since,
    _input_row,
    _iso,
)
from fpl_edge.platform.scripts.fixtures.ratings import (
    BUILD_HINT,
    _Ratings,
    _read_parquet,
    load_legacy_difficulty,
    load_ratings,
    model_calibration,
)

# ---------------------------------------------------------------------------
# odds
# ---------------------------------------------------------------------------


def _resolved_odds(wh, season: str, as_of: dt.datetime) -> tuple[pd.DataFrame, str | None]:
    """``fact_odds`` re-keyed to ``season:fixture_id``, selections normalised.

    Two live bugs are worked around here rather than in the model package, which
    this module does not own:

    1. ``fact_odds`` stores natural keys (``2026-27:2026-08-29:hull:man-united``)
       while the goal model looks up ``2026-27:11``. ``odds_with_fixture_keys``
       is the repo's designated read-time resolver and is used as-is.
    2. ``team_goals.odds.devig_frame`` matches selections as ``("home", "draw",
       "away")`` and ``startswith("over")``, but every live row is upper case
       (``HOME``, ``OVER_2.5``). Unpatched, ``devig_frame`` returns **zero**
       fixtures against a warehouse holding 131,921 odds rows. Lower-casing the
       selection column here makes it match; the fix belongs upstream.
    """
    try:
        from fpl_edge.models.ensemble.sources import odds_with_fixture_keys
        odds, _ = odds_with_fixture_keys(wh, season, as_of)
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), (
            f"odds could not be re-keyed to fixture ids ({type(exc).__name__}); "
            f"the market leg is dropped rather than guessed"
        )
    if odds.empty:
        return odds, f"no {season} rows in fact_odds at or before this instant"
    odds = odds.copy()
    odds["selection"] = odds["selection"].astype(str).str.lower()
    parts = odds["fixture_key"].astype(str).str.split(":")
    numeric = parts.str.len() == 2
    odds = odds[numeric].copy()
    if odds.empty:
        return odds, (
            "no odds row could be matched to a fixture id; every key is still a "
            "natural key and the name-matching resolver found no fixture"
        )
    odds["fixture_id"] = odds["fixture_key"].astype(str).str.split(":").str[1].astype(int)
    return odds, None


def _market_state(age_hours: float | None) -> str:
    if age_hours is None:
        return "unpriced"
    if age_hours > ODDS_USELESS_HOURS:
        return "expired"
    if age_hours > ODDS_STALE_HOURS:
        return "stale"
    return "priced"


MARKET_CELL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["state", "reason"],
    "properties": {
        "state": {"enum": ["priced", "stale", "expired", "unpriced"]},
        "as_of": {"type": ["string", "null"]},
        "age_hours": {"type": ["number", "null"]},
        "n_books": {"type": ["integer", "null"]},
        "reason": {"type": ["string", "null"]},
    },
}


# ---------------------------------------------------------------------------
# fixture_ticker is GONE. The legacy blended-difficulty panel was deleted
# (2026-09): its own description said "superseded by fixture_board", and
# fixture_board serves the same blend per cell as the deprecated
# `legacy_difficulty` field, so nothing the ticker published is lost -- only
# the second, blended data path is. `load_legacy_difficulty` above survives
# because fixture_board reads it for exactly that field.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# fixture_board -- the horizon ticker, both lenses
# ---------------------------------------------------------------------------

_LENS_SCHEMA_PROPS: dict[str, Any] = {
    "attack_ease": {"type": ["number", "null"],
                    "description": "Signed goals vs a league-average fixture. POSITIVE = easier to score."},
    "defence_ease": {"type": ["number", "null"],
                     "description": "Signed goals vs a league-average fixture. POSITIVE = easier to keep a clean sheet."},
    "attack_xg": {"type": ["number", "null"],
                  "description": "Expected goals FOR, from the score matrix."},
    "defence_xg": {"type": ["number", "null"],
                   "description": "Expected goals AGAINST, from the same matrix."},
    "attack_pts": {"type": ["number", "null"],
                   "description": "attack_ease converted to FPL points for a reference attacker."},
    "defence_pts": {"type": ["number", "null"],
                    "description": "defence_ease converted to FPL points for a defender/keeper."},
    "p_clean_sheet": {"type": ["number", "null"]},
    "p_opponent_clean_sheet": {"type": ["number", "null"]},
    "p_concede_2plus": {"type": ["number", "null"]},
    "attack_rank": {"type": ["integer", "null"],
                    "description": "Rank over the 2N (opponent, venue) league population. 1 = EASIEST."},
    "defence_rank": {"type": ["integer", "null"]},
    "rank_gap": {"type": ["integer", "null"],
                 "description": "attack_rank - defence_rank. Large magnitude = the blend would have lied."},
    "unavailable": {"type": ["string", "null"],
                    "description": "Why every number above is null. Renderable prose, never a code."},
}

_LENS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["unavailable"],
    "properties": _LENS_SCHEMA_PROPS,
}

BOARD_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "horizon": {"type": "integer", "minimum": 1, "maximum": 12, "default": 6},
        "from_gw": {"type": ["integer", "null"], "default": None,
                    "description": "Start gameweek; defaults to the next unplayed one."},
        "as_of": {"type": ["string", "null"], "default": None,
                  "description": "ISO instant. Every warehouse read is point-in-time to it."},
        "include_form": {"type": "boolean", "default": True},
        "include_calibration": {"type": "boolean", "default": True},
        "divergence_ranks": {"type": "integer", "minimum": 1, "maximum": 40,
                             "default": DIVERGENCE_RANKS},
    },
}

BOARD_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "gws", "teams", "row_count", "inputs", "scale", "as_of"],
    "properties": {
        "season": {"type": "string"},
        "as_of": {"type": "string", "description": "The point-in-time instant every read used."},
        "gws": {"type": "array", "items": {"type": "integer"}},
        "from_gw": {"type": "integer"},
        "horizon": {"type": "integer"},
        "row_count": {"type": "integer"},
        "fixture_as_of": {"type": ["string", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
        "inputs": {"type": "array", "items": INPUT_SCHEMA},
        "scale": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "unit", "domain", "available"],
            "properties": {
                "kind": {"const": "diverging"},
                "unit": {"type": "string"},
                "polarity": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "number"}},
                "domain_applies_to": {"const": "opponent_only"},
                "domain_note": {"type": "string"},
                "available": {"type": "boolean"},
                "anchor_attack_xg": {"type": ["number", "null"]},
                "anchor_defence_xg": {"type": ["number", "null"]},
                "population": {"type": ["integer", "null"]},
                "clipped_pairs": {"type": ["integer", "null"]},
                "rank_convention": {"type": "string"},
                "unavailable": {"type": ["string", "null"]},
            },
        },
        "calibration": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["headline", "model", "empirical"],
            "properties": {
                "headline": {"type": "string"},
                "model": {"type": ["object", "null"], "additionalProperties": True},
                "empirical": {"type": ["object", "null"], "additionalProperties": True},
                "unavailable": {"type": ["string", "null"]},
            },
        },
        "divergent": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gw", "fixture_id", "team_code", "sentence"],
                "properties": {
                    "gw": {"type": "integer"},
                    "fixture_id": {"type": "integer"},
                    "team_code": {"type": "integer"},
                    "short_name": {"type": "string"},
                    "opponent_code": {"type": "integer"},
                    "opponent": {"type": "string"},
                    "is_home": {"type": "boolean"},
                    "attack_rank": {"type": "integer"},
                    "defence_rank": {"type": "integer"},
                    "gap": {"type": "integer"},
                    "sentence": {"type": "string"},
                },
            },
        },
        "teams": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["team_code", "short_name", "fixtures", "n_fixtures"],
                "properties": {
                    "team_code": {"type": "integer"},
                    "short_name": {"type": "string"},
                    "name": {"type": ["string", "null"]},
                    "n_fixtures": {"type": "integer"},
                    "n_blanks": {"type": "integer"},
                    "n_doubles": {"type": "integer"},
                    "rating": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "properties": {
                            "attack": {"type": "number"},
                            "defence": {"type": "number",
                                        "description": "Leakiness: HIGHER concedes more."},
                            "attack_rank": {"type": "integer"},
                            "defence_rank": {"type": "integer"},
                            "is_promoted": {"type": "boolean"},
                            "matches_seen": {"type": "integer"},
                            "scores_pg": {"type": "number",
                                          "description": "Goals scored per game against a league-average opponent, venue-averaged."},
                            "concedes_pg": {"type": "number",
                                            "description": "Goals conceded per game against a league-average opponent, venue-averaged."},
                            "league_pg": {"type": "number",
                                          "description": "The same figure for a league-average club, as the comparison."},
                        },
                    },
                    "horizon": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "properties": {
                            "attack_ease_sum": {"type": "number"},
                            "defence_ease_sum": {"type": "number"},
                            "attack_ease_per_game": {"type": "number"},
                            "defence_ease_per_game": {"type": "number"},
                            "attack_pts_sum": {"type": "number"},
                            "defence_pts_sum": {"type": "number"},
                            "attack_rank": {"type": "integer"},
                            "defence_rank": {"type": "integer"},
                            "rank_gap": {"type": "integer"},
                            "n_rated": {"type": "integer"},
                        },
                    },
                    "form": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["window_matches", "unavailable"],
                        "properties": {
                            "window_matches": {"type": "integer"},
                            "xg_for_pg": {"type": ["number", "null"]},
                            "xg_against_pg": {"type": ["number", "null"]},
                            "xg_for_resid": {"type": ["number", "null"],
                                             "description": "Actual minus what the fitted rating expected. A diagnostic, never an input."},
                            "xg_against_resid": {"type": ["number", "null"]},
                            "unavailable": {"type": ["string", "null"]},
                        },
                    },
                    "unavailable": {"type": ["string", "null"]},
                    "fixtures": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["gw", "blank", "double", "opponents"],
                            "properties": {
                                "gw": {"type": "integer"},
                                "blank": {"type": "boolean"},
                                "double": {"type": "boolean"},
                                "opponents": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["fixture_id", "opponent", "opponent_code",
                                                     "is_home", "label", "opponent_only",
                                                     "fixture_specific", "market"],
                                        "properties": {
                                            "fixture_id": {"type": "integer"},
                                            "opponent": {"type": "string"},
                                            "opponent_code": {"type": "integer"},
                                            "is_home": {"type": "boolean"},
                                            "kickoff_utc": {"type": ["string", "null"]},
                                            "label": {"type": "string"},
                                            "opponent_only": _LENS_SCHEMA,
                                            "fixture_specific": _LENS_SCHEMA,
                                            "market": MARKET_CELL_SCHEMA,
                                            "legacy_difficulty": {"type": ["number", "null"]},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def _blank_lens(reason: str) -> dict[str, Any]:
    """Every field null and one sentence saying why. Never a zero, never a 0.5."""
    return {k: None for k in _LENS_SCHEMA_PROPS} | {"unavailable": reason}


def _lens(qty: dict[str, float], *, anchor_att: float, anchor_def: float,
          ref_cs: float, ref_pen: float,
          ranks: tuple[int, int] | None = None) -> dict[str, Any]:
    attack_ease = qty["xg"] - anchor_att
    defence_ease = anchor_def - qty["xg_against"]
    out = {
        "attack_ease": round(attack_ease, 4),
        "defence_ease": round(defence_ease, 4),
        "attack_xg": round(qty["xg"], 4),
        "defence_xg": round(qty["xg_against"], 4),
        "attack_pts": round(attack_ease * ATTACKER_SHARE * FPL_GOAL_POINTS, 4),
        "defence_pts": round(
            (qty["p_clean_sheet"] - ref_cs) * FPL_GOAL_POINTS
            + (qty["e_concede_penalty"] - ref_pen), 4),
        "p_clean_sheet": round(qty["p_clean_sheet"], 4),
        "p_opponent_clean_sheet": round(qty["p_opponent_clean_sheet"], 4),
        "p_concede_2plus": round(qty["p_concede_2plus"], 4),
        "attack_rank": None,
        "defence_rank": None,
        "rank_gap": None,
        "unavailable": None,
    }
    if ranks is not None:
        out["attack_rank"], out["defence_rank"] = ranks
        out["rank_gap"] = ranks[0] - ranks[1]
    return out


def _team_form(wh, season: str, now: dt.datetime, ratings: _Ratings | None
               ) -> tuple[dict[int, dict[str, Any]], int, Any]:
    """Rolling team xG for and against, and the residual against the rating.

    The aggregation trap, which is worth a comment because summing looks right:
    ``expected_goals_conceded`` is written PER PLAYER and every outfielder on the
    pitch carries the team's value, so a naive SUM gives 30-plus xGC for a single
    fixture. One representative value per team-match is the correct read; xG FOR
    is genuinely per player and is summed.

    ``was_home`` is 100% NULL for the 2026-27 rows in ``fact_player_fixture``, so
    the side is resolved through ``dim_player.team_code`` rather than that
    column. That mis-attributes a mid-season transfer's earlier matches to the
    new club; over a six-match window it is a small and stated risk, and it is
    the only join available while the column is empty.
    """
    rows = q(wh, """
        WITH fx AS (SELECT * FROM sem_fixtures(?) WHERE season = ? AND finished),
             pl AS (SELECT season, code, team_code FROM (
                      SELECT *, row_number() OVER (
                        PARTITION BY season, code ORDER BY as_of DESC) rn
                      FROM dim_player WHERE season = ? AND as_of <= ?) WHERE rn = 1),
             pf AS (SELECT * FROM sem_player_form(?) WHERE season = ?)
        SELECT fx.team_code, fx.fixture_id, fx.gw, fx.kickoff_utc,
               fx.opponent_code, fx.is_home,
               SUM(pf.expected_goals) AS xg_for,
               MAX(pf.expected_goals_conceded) AS xg_against
        FROM fx JOIN pl ON pl.season = fx.season AND pl.team_code = fx.team_code
                JOIN pf ON pf.season = fx.season AND pf.fixture_id = fx.fixture_id
                       AND pf.code = pl.code
        GROUP BY 1, 2, 3, 4, 5, 6
    """, (now, season, season, now, now, season))
    if rows.empty:
        return {}, 0, None
    newest = pd.to_datetime(rows["kickoff_utc"], utc=True).max()
    rows = rows.sort_values("kickoff_utc", ascending=False)
    out: dict[int, dict[str, Any]] = {}
    for team, grp in rows.groupby("team_code"):
        window = grp.head(FORM_WINDOW)
        n = len(window)
        entry: dict[str, Any] = {
            "window_matches": int(n),
            "xg_for_pg": round(float(window["xg_for"].mean()), 3),
            "xg_against_pg": round(float(window["xg_against"].mean()), 3),
            "xg_for_resid": None,
            "xg_against_resid": None,
            "unavailable": None,
        }
        if n < FORM_MIN_MATCHES:
            entry["unavailable"] = (
                f"{n} completed match{'' if n == 1 else 'es'} this season, below "
                f"the {FORM_MIN_MATCHES} needed for the residual to mean anything. "
                f"The per-game figures are shown; the residual is not."
            )
        elif ratings is not None:
            exp_for, exp_against = [], []
            for r in window.itertuples(index=False):
                if not ratings.has(int(r.team_code), int(r.opponent_code)):
                    continue
                mu, lam = ratings.fixture_rates(
                    int(r.team_code), int(r.opponent_code), bool(r.is_home))
                got = ratings.quantities(mu, lam)
                exp_for.append(got["xg"])
                exp_against.append(got["xg_against"])
            if exp_for:
                entry["xg_for_resid"] = round(
                    float(window["xg_for"].mean()) - float(np.mean(exp_for)), 3)
                entry["xg_against_resid"] = round(
                    float(window["xg_against"].mean()) - float(np.mean(exp_against)), 3)
        elif ratings is None:
            entry["unavailable"] = (
                "the fitted ratings artefact is absent, so there is nothing to "
                "take a residual against; the raw per-game figures stand alone."
            )
        out[int(team)] = entry
    return out, int(len(rows) // 2), newest


def fixture_board(
    wh, *, season: str, horizon: int = 6, from_gw: int | None = None,
    as_of: str | None = None, include_form: bool = True,
    include_calibration: bool = True, divergence_ranks: int = DIVERGENCE_RANKS,
) -> dict[str, Any]:
    """The horizon ticker: clubs down, gameweeks across, TWO difficulties a cell.

    Every cell carries ``opponent_only`` (your club held at league average --
    what the colour is for) and ``fixture_specific`` (your club's own strength
    folded in -- what a drilldown shows). They are different numbers answering
    different questions and they are never merged, never defaulted into each
    other, and never served under one name.

    Also returned: ``inputs[]`` with every source's age and what its staleness
    costs, ``scale`` so the legend is payload-led, ``calibration`` so the page
    can print how big a fixture actually is, and ``divergent[]`` -- the fixtures
    where the two lenses disagree, which is the finding a blended number erases.
    """
    now = dt.datetime.now(UTC)
    if as_of:
        parsed = pd.to_datetime(as_of, utc=True, errors="coerce")
        if parsed is pd.NaT or pd.isna(parsed):
            return empty(f"as_of={as_of!r} is not an ISO instant; nothing was read.")
        now = parsed.to_pydatetime()

    teams = q(wh, """
        SELECT team_code, short_name, name FROM (
          SELECT *, row_number() OVER (
            PARTITION BY season, team_code ORDER BY as_of DESC) rn
          FROM dim_team WHERE season = ? AND as_of <= ?) WHERE rn = 1
        ORDER BY short_name
    """, (season, now))
    if teams.empty:
        return empty(
            f"No {season} clubs known at {now.isoformat()}. dim_team comes from "
            f"the FPL bootstrap; run `make ingest`."
        )

    notes: list[str] = []
    start = from_gw if from_gw is not None else next_gw(wh, season, now)
    if start is None:
        played = q(wh, "SELECT max(gw) AS g FROM fact_fixture WHERE season = ? AND as_of <= ?",
                   (season, now))
        latest = None if played.empty else played.iloc[0]["g"]
        if latest is None:
            return empty(f"No {season} fixtures or deadlines known yet. Run `make ingest`.")
        start = int(latest)
        notes.append(
            f"Every {season} deadline has passed, so the board starts at the last "
            f"known gameweek (GW{start}) rather than a future one."
        )
    gws = list(range(int(start), int(start) + int(horizon)))

    fx = q(wh, """
        SELECT fixture_id, gw, kickoff_utc, team_code, opponent_code, is_home,
               team, opponent
        FROM sem_fixtures(?)
        WHERE season = ? AND gw >= ? AND gw <= ?
        ORDER BY gw, kickoff_utc, team_code
    """, (now, season, gws[0], gws[-1]))
    if fx.empty:
        return empty(
            f"No {season} fixtures scheduled for GW{gws[0]}-GW{gws[-1]} as known at "
            f"{now.isoformat()}. The fixture list is ingested from the FPL API; "
            f"run `make ingest`."
        )

    ratings, ratings_reason = load_ratings(wh, season)
    legacy = load_legacy_difficulty(wh, season)
    population: dict[str, Any] = {}
    if ratings is not None:
        population = ratings.population()
        if not ratings.converged:
            notes.append(
                "The stored Dixon-Coles fit did not converge; every difficulty "
                "below inherits that. Re-run the ratings build."
            )
        # A cached artefact is stamped when it was BUILT, not when it was asked
        # for, so a backdated as_of reads a fit that saw results the caller's
        # instant could not. Every warehouse read here is point-in-time; the
        # artefact cannot be, and pretending otherwise is exactly the leak the
        # snapshot discipline exists to stop. Say it loudly instead.
        built = pd.to_datetime(ratings.snapshot_as_of, utc=True, errors="coerce")
        if pd.notna(built) and built.to_pydatetime() > now:
            notes.append(
                f"LEAKAGE WARNING: as_of is {now.isoformat()} but the cached fit "
                f"was trained on the warehouse as at {built.isoformat()}, so the "
                f"difficulties below saw results that did not exist at the "
                f"requested instant. Every warehouse read is point-in-time; a "
                f"cached artefact cannot be. Rebuild with --build at the target "
                f"instant before using this for a backtest."
            )

    odds, odds_reason = _resolved_odds(wh, season, now)
    per_fixture_odds: dict[int, dict[str, Any]] = {}
    if not odds.empty:
        agg = (odds[odds["market"].isin(["h2h", "totals"])]
               .groupby("fixture_id")
               .agg(newest=("as_of", "max"), n_books=("bookmaker", "nunique")))
        for fid, quote in agg.iterrows():
            age = _hours_since(quote["newest"], now)
            per_fixture_odds[int(fid)] = {
                "state": _market_state(age),
                "as_of": _iso(quote["newest"]),
                "age_hours": age,
                "n_books": int(quote["n_books"]),
                "reason": None,
            }

    form: dict[int, dict[str, Any]] = {}
    form_matches, form_newest = 0, None
    if include_form:
        form, form_matches, form_newest = _team_form(wh, season, now, ratings)

    rating_ranks = ratings.rating_rank() if ratings is not None else {}
    anchors = {
        "anchor_att": population.get("anchor_attack_xg", 0.0),
        "anchor_def": population.get("anchor_defence_xg", 0.0),
        "ref_cs": population.get("ref_clean_sheet", 0.0),
        "ref_pen": population.get("ref_concede_penalty", 0.0),
    }

    per: dict[tuple[int, int], list[dict[str, Any]]] = {}
    divergent: list[dict[str, Any]] = []
    short = dict(zip(teams["team_code"], teams["short_name"]))
    for r in fx.itertuples(index=False):
        team, opp = int(r.team_code), int(r.opponent_code)
        is_home = bool(r.is_home)
        fid, gw = int(r.fixture_id), int(r.gw)
        label = str(r.opponent) if r.opponent is not None else short.get(opp, str(opp))

        if ratings is None:
            oo = _blank_lens(ratings_reason or "no fitted ratings artefact")
            fs = _blank_lens(ratings_reason or "no fitted ratings artefact")
        elif not ratings.has(team, opp):
            unrated = (f"{label} is not in the stored fit. The fixture was added "
                       f"or rescheduled after the last ratings build.")
            oo, fs = _blank_lens(unrated), _blank_lens(unrated)
        else:
            pair = population["by_pair"][(opp, is_home)]
            mu_a, lam_a = ratings.opponent_only_rates(opp, is_home)
            mu_f, lam_f = ratings.fixture_rates(team, opp, is_home)
            oo = _lens(ratings.quantities(mu_a, lam_a), **anchors,
                       ranks=(int(pair["attack_rank"]), int(pair["defence_rank"])))
            fs = _lens(ratings.quantities(mu_f, lam_f), **anchors)
            rank_gap = int(oo["rank_gap"] or 0)
            if abs(rank_gap) >= divergence_ranks:
                venue = "you host" if is_home else "you visit"
                easier, harder = (("defensive", "attacking") if rank_gap > 0
                                  else ("attacking", "defensive"))
                divergent.append({
                    "gw": gw, "fixture_id": fid, "team_code": team,
                    "short_name": str(short.get(team, team)),
                    "opponent_code": opp, "opponent": label, "is_home": is_home,
                    "attack_rank": int(oo["attack_rank"]),
                    "defence_rank": int(oo["defence_rank"]),
                    "gap": rank_gap,
                    "sentence": (
                        f"GW{gw}, {venue} {label}: {oo['attack_rank']} of "
                        f"{population['population']} as an attacking fixture, "
                        f"{oo['defence_rank']} as a defensive one. It is a good "
                        f"{easier} fixture and a poor {harder} one; a blended "
                        f"number would call it average."
                    ),
                })

        per.setdefault((team, gw), []).append({
            "fixture_id": fid,
            "opponent": label,
            "opponent_code": opp,
            "is_home": is_home,
            "kickoff_utc": None if r.kickoff_utc is None else _iso(r.kickoff_utc),
            "label": label.upper() if is_home else label.lower(),
            "opponent_only": oo,
            "fixture_specific": fs,
            "market": per_fixture_odds.get(fid, {
                "state": "unpriced", "as_of": None, "age_hours": None, "n_books": None,
                "reason": odds_reason or "no bookmaker has quoted this fixture yet",
            }),
            "legacy_difficulty": legacy.get((fid, team)),
        })

    out: list[dict[str, Any]] = []
    for t in teams.itertuples(index=False):
        code = int(t.team_code)
        slots, total, blanks, doubles = [], 0, 0, 0
        att_sum = def_sum = att_pts = def_pts = 0.0
        n_rated = 0
        for gw in gws:
            opps = per.get((code, gw), [])
            total += len(opps)
            blanks += 1 if not opps else 0
            doubles += 1 if len(opps) > 1 else 0
            for o in opps:
                lens = o["opponent_only"]
                if lens["attack_ease"] is not None:
                    att_sum += lens["attack_ease"]
                    def_sum += lens["defence_ease"]
                    att_pts += lens["attack_pts"]
                    def_pts += lens["defence_pts"]
                    n_rated += 1
            slots.append({"gw": gw, "blank": not opps,
                          "double": len(opps) > 1, "opponents": opps})
        if total == 0:
            # A club with nothing in the whole window is not in this league;
            # listing it as N blank gameweeks would read as a real run.
            continue
        row: dict[str, Any] = {
            "team_code": code,
            "short_name": str(t.short_name),
            "name": None if t.name is None else str(t.name),
            "n_fixtures": total, "n_blanks": blanks, "n_doubles": doubles,
            "rating": None, "horizon": None, "unavailable": None,
            "form": form.get(code, {
                "window_matches": 0, "xg_for_pg": None, "xg_against_pg": None,
                "xg_for_resid": None, "xg_against_resid": None,
                "unavailable": "no completed match for this club at this instant",
            }),
            "fixtures": slots,
        }
        if ratings is not None and ratings.has(code):
            ranks = rating_ranks[code]
            row["rating"] = {
                "attack": round(ratings.attack[code], 4),
                "defence": round(ratings.defence[code], 4),
                "attack_rank": ranks[0], "defence_rank": ranks[1],
                "is_promoted": code in ratings.promoted,
                "matches_seen": ratings.matches_seen.get(code, 0),
                **ratings.in_goals(code),
            }
        if n_rated:
            row["horizon"] = {
                "attack_ease_sum": round(att_sum, 4),
                "defence_ease_sum": round(def_sum, 4),
                "attack_ease_per_game": round(att_sum / n_rated, 4),
                "defence_ease_per_game": round(def_sum / n_rated, 4),
                "attack_pts_sum": round(att_pts, 4),
                "defence_pts_sum": round(def_pts, 4),
                "attack_rank": 0, "defence_rank": 0, "rank_gap": 0,
                "n_rated": n_rated,
            }
        else:
            row["unavailable"] = ratings_reason or (
                "no fitted rating covers any of this club's fixtures in the window"
            )
        out.append(row)

    if not out:
        return empty(f"No club has a fixture in GW{gws[0]}-GW{gws[-1]} for {season}.")

    # Horizon ranks: computed after every club is built, over the clubs that
    # actually have numbers, so a club with no rating is absent from the ranking
    # rather than silently ranked last.
    rated = [r for r in out if r["horizon"] is not None]
    if rated:
        att_order = pd.Series({r["team_code"]: r["horizon"]["attack_ease_sum"] for r in rated})
        def_order = pd.Series({r["team_code"]: r["horizon"]["defence_ease_sum"] for r in rated})
        att_rank = att_order.rank(ascending=False, method="min").astype(int)
        def_rank = def_order.rank(ascending=False, method="min").astype(int)
        for r in rated:
            code = r["team_code"]
            r["horizon"]["attack_rank"] = int(att_rank[code])
            r["horizon"]["defence_rank"] = int(def_rank[code])
            r["horizon"]["rank_gap"] = int(att_rank[code]) - int(def_rank[code])

    divergent.sort(key=lambda d: (-abs(d["gap"]), d["gw"]))

    calibration = None
    if include_calibration:
        calibration = _calibration_block(wh, ratings, fx, gws)

    scale: dict[str, Any] = {
        "kind": "diverging",
        "unit": "goals per match versus a league-average fixture",
        "polarity": "positive is better for you on BOTH axes; defence_ease is a flip of the opponent's goal rate",
        "domain": [-SCALE_DOMAIN, SCALE_DOMAIN],
        "domain_applies_to": "opponent_only",
        "available": ratings is not None,
        "anchor_attack_xg": None, "anchor_defence_xg": None,
        "population": None, "clipped_pairs": None,
        "rank_convention": "1 = easiest, over the 2N (opponent, venue) pairs of this league",
        # Measured on the live 2026-27 fit: the opponent-only population has SD
        # 0.29 (attack) / 0.31 (defence), so +/-0.60 is two SD and saturates 5 of
        # the 40 pairs -- the best and worst four clubs read off-scale on purpose
        # and everybody else uses the full ramp. `fixture_specific` is a WIDER
        # distribution (SD ~0.40, ~13% of cells outside this domain) because our
        # own club's strength is in it, so a renderer that colours the
        # fixture-specific number on THIS domain will clip much more than the
        # legend implies. Colour opponent_only; print fixture_specific.
        "domain_note": (
            "This domain is calibrated on the opponent-only population, which is "
            "what the ticker colours. fixture_specific is a wider distribution "
            "and will clip harder on the same ramp."
        ),
        "unavailable": ratings_reason,
    }
    if population:
        scale |= {
            "anchor_attack_xg": round(population["anchor_attack_xg"], 4),
            "anchor_defence_xg": round(population["anchor_defence_xg"], 4),
            "population": population["population"],
            "clipped_pairs": population["clipped_pairs"],
        }

    horizon_fixture_ids = {int(f) for f in fx["fixture_id"].unique()}
    ratings_detail = ratings_reason or "" if ratings is None else (
        f"Dixon-Coles, {ratings.n_matches} matches, effective n "
        f"{ratings.effective_n:.0f}, {ratings.half_life_days:.0f}-day half-life, "
        f"{'converged' if ratings.converged else 'DID NOT CONVERGE'}"
    )
    n_horizon_fixtures = len(horizon_fixture_ids)
    n_priced = len(horizon_fixture_ids & set(per_fixture_odds))

    inputs = [
        _input_row(
            "fitted ratings", source=RATINGS_NAME,
            as_of=None if ratings is None else ratings.fitted_at, now=now,
            stale_after_hours=RATINGS_STALE_HOURS,
            rows=None if ratings is None else len(ratings.codes),
            missing=ratings is None,
            effect_when_stale=(
                "the split difficulties are still shown, because a week-old fitted "
                "rating beats a made-up fresh one, but the fit predates recent results"
            ),
            detail=ratings_detail,
        ),
        _input_row(
            "schedule", source="fact_fixture via sem_fixtures(as_of)",
            as_of=latest_as_of(wh, "fact_fixture", season), now=now,
            stale_after_hours=48.0, rows=len(fx),
            effect_when_stale="a rescheduled fixture may still be shown at its old date",
            detail=f"{len(fx)} team-fixture rows over GW{gws[0]}-GW{gws[-1]}",
        ),
        _input_row(
            "market odds", source="fact_odds (h2h + totals), re-keyed to fixture ids",
            as_of=None if odds.empty else odds["as_of"].max(), now=now,
            stale_after_hours=ODDS_STALE_HOURS, rows=len(odds),
            missing=odds.empty,
            effect_when_stale=(
                f"the market is not in any difficulty on this board and never is, "
                f"but a price older than {ODDS_USELESS_HOURS:.0f}h predates the team "
                f"news that decides the fixture and the drilldown marks it expired"
            ),
            detail=odds_reason or (
                f"{n_priced} of {n_horizon_fixtures} fixtures in the horizon carry "
                f"a quote; {n_horizon_fixtures - n_priced} are not priced by any "
                f"book yet, which is normal more than two or three gameweeks out"
            ),
        ),
        _input_row(
            "team form (xG)", source="fact_player_fixture via sem_player_form(as_of)",
            as_of=form_newest, now=now, stale_after_hours=None,
            rows=form_matches, missing=not form,
            effect_when_stale=(
                "nothing: the residual is a diagnostic that the colour might be "
                "wrong, and it never enters a difficulty. Its age is the age of "
                "the last completed match, not of a fetch, so there is no "
                "staleness threshold to cross."
            ),
            detail=(
                f"{form_matches} completed team-matches this season, newest "
                f"{_iso(form_newest)}"
                if form else "no completed match for this season yet"
            ),
        ),
    ]

    notes.append(
        "Colour holds your own club at league average and asks only what the "
        "opponent does at that venue, so two clubs facing the same opponent get "
        "the same cell. That is on purpose. The fixture-specific number, with "
        "your own club's strength in it, is on the same cell under "
        "`fixture_specific`."
    )
    if odds_reason is None and per_fixture_odds:
        notes.append(
            "The market is reported per cell but is NOT blended into any "
            "difficulty. `blend.py`'s weight has never been tuned out of sample, "
            "so a blend here would be an untuned constant wearing a number's "
            "clothes. The drilldown shows model and market side by side instead."
        )

    return {
        "season": season,
        "as_of": now.isoformat(),
        "gws": gws,
        "from_gw": int(start),
        "horizon": int(horizon),
        "row_count": len(out),
        "fixture_as_of": latest_as_of(wh, "fact_fixture", season),
        "teams": out,
        "inputs": inputs,
        "scale": scale,
        "calibration": calibration,
        "divergent": divergent,
        "notes": notes,
    }


def _calibration_block(wh, ratings: _Ratings | None, fx: pd.DataFrame,
                       gws: list[int]) -> dict[str, Any]:
    """How big is a fixture, in points, next to how big a club is.

    The page prints this to stop itself being over-trusted: an easy run is a
    tie-breaker between similar assets, not a reason to pick one. Two
    independent answers are served and neither is allowed to stand in for the
    other -- ``model`` is the fitted model's own arithmetic over the requested
    horizon, ``empirical`` is a regression on four seasons of realised FPL
    points. If they disagree the page should say so; averaging them would hide
    exactly the thing worth knowing.
    """
    model = model_calibration(ratings, fx, gws) if ratings is not None else None
    df, err = _read_parquet(source_dir(wh) / CALIBRATION_NAME)
    empirical = None
    emp_reason = err
    if df is not None and not df.empty and {"position", "fixture_pts_6gw"} <= set(df.columns):
        outfield = df[df["position"].isin(["DEF", "MID", "FWD"])]
        empirical = {
            "by_position": [
                {"position": str(r.position), "n_starts": int(r.n_starts),
                 "fixture_pts_6gw": round(float(r.fixture_pts_6gw), 2),
                 "team_pts_6gw": round(float(r.team_pts_6gw), 2),
                 "ratio": round(float(r.ratio), 2)}
                for r in df.itertuples(index=False)
            ],
            "outfield_fixture_pts_6gw": round(float(outfield["fixture_pts_6gw"].mean()), 2),
            "outfield_team_pts_6gw": round(float(outfield["team_pts_6gw"].mean()), 2),
            "outfield_ratio": round(
                float(outfield["team_pts_6gw"].mean() / outfield["fixture_pts_6gw"].mean()), 2),
            "seasons": str(df.iloc[0]["seasons"]),
            "method": str(df.iloc[0]["method"]),
            "computed_at": _iso(df.iloc[0]["computed_at"]),
            "caveat": (
                "max-minus-min over twenty estimated effects is biased upward by "
                "sampling noise, so this is an upper bound; the model figure, "
                "which has no estimation noise in it, is the lower bound. Read "
                "them as a bracket."
            ),
        }
    elif df is not None:
        emp_reason = f"{CALIBRATION_NAME} is present but empty or misshapen"

    if model is None and empirical is None:
        return {
            "headline": "How much a fixture is worth has not been measured here yet.",
            "model": None, "empirical": None,
            "unavailable": (
                f"neither figure is available: {BUILD_HINT}. Without it the page "
                f"must not claim a size for the fixture effect."
            ),
        }
    if model is not None:
        head = (
            f"Over {model['horizon_gws']} gameweeks, the fixture run is worth about "
            f"{model['fixture_swing_attack_pts']:.1f} points to an attacker and "
            f"{model['fixture_swing_defence_pts']:.1f} to a defender, best club to "
            f"worst. Which club you own is worth about "
            f"{model['ratio_attack']:.0f}x that. Use this page to break ties "
            f"between similar assets, not to pick them."
        )
    else:
        assert empirical is not None  # the both-None case returned above
        head = (
            f"Over six gameweeks the fixture run is worth about "
            f"{empirical['outfield_fixture_pts_6gw']:.1f} realised points to an "
            f"outfielder and the club itself about "
            f"{empirical['outfield_ratio']:.1f}x that. Tie-breaker, not picker."
        )
    return {"headline": head, "model": model, "empirical": empirical,
            "unavailable": None if empirical is not None else emp_reason}


register_script(
    "fixture_board",
    fixture_board,
    params_schema=BOARD_PARAMS,
    result_schema=BOARD_RESULT,
    title="Fixture board",
    description=(
        "The horizon ticker with BOTH difficulties per cell: opponent-only "
        "(what the colour is for) and fixture-specific (what the drilldown "
        "shows). Every input reports its own age."
    ),
)
