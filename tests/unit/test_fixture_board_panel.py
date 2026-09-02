"""The fixtures data path: the two-lens contract, freshness, and honest gaps.

The load-bearing assertion in this file is
``test_opponent_only_is_identical_for_two_clubs_facing_the_same_opponent``
together with its twin
``test_fixture_specific_differs_for_those_same_two_clubs``. Those two together
ARE the page's claim: the colour asks only what the opponent does, and the
fixture-specific number -- which is a different number -- is served beside it
rather than instead of it. If the first passes and the second fails, the panel
has quietly become a power ranking; if the second passes and the first fails,
rows have stopped being comparable and the ticker means nothing.

Every test here was watched fail before it was kept: the guard was neutered, the
suite was run, the failure was read, and the guard was restored. A test that has
never failed is not a test.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.platform import scripts  # noqa: F401 - registers the scripts
from fpl_edge.platform.registry import registered, run_script, script
from fpl_edge.platform.scripts import fixtures as fxmod
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC
SEASON = "2026-27"

#: Deliberately spread so the two lenses cannot agree by accident. `defence` is
#: LEAKINESS: higher concedes more. HUL is the design's canonical torn fixture --
#: a poor attack (you will not score off them being easy is the opposite: their
#: low attack makes them a kind DEFENSIVE fixture) with a tight defence, so it is
#: an easy clean sheet and a hard afternoon for your forwards.
CLUBS = {
    1: ("ARS", "Arsenal", 0.34, -0.57),
    2: ("CHE", "Chelsea", 0.05, 0.02),
    3: ("HUL", "Hull", -0.15, -0.27),
    4: ("COV", "Coventry", -0.44, 0.25),
}

STAMP = pd.Timestamp("2026-08-01", tz="UTC")
KICKOFF = {1: "2026-08-14 19:00", 2: "2026-08-14 19:00",
           3: "2026-08-21 19:00", 4: "2026-08-21 19:00",
           5: "2026-08-28 19:00", 6: "2026-08-29 19:00"}

# (fixture_id, gw, home, away). GW1 and GW2 are arranged so that ARS and CHE
# each host HUL: same opponent, same venue, two different clubs. GW3 gives ARS a
# double and COV a blank.
FIXTURES = [
    (1, 1, 1, 3), (2, 1, 2, 4),
    (3, 2, 2, 3), (4, 2, 1, 4),
    (5, 3, 1, 2), (6, 3, 3, 1),
]


def _seed(tmp_path, *, with_ratings=True, fitted_at=None, odds=None,
          content_tables=True):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    if content_tables:
        # `content_insight` is created by a feature migration, not by the base
        # schema, so a bare Warehouse() does not have it. Applying the real DDL
        # here is what lets the zero-rows path -- which is the LIVE production
        # state -- be tested, rather than only the table-absent path.
        from fpl_edge.ingest.content.store import MIGRATIONS_DIR
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            wh.sql(sql_file.read_text())
    wh.append("dim_team", pd.DataFrame([
        {"season": SEASON, "team_code": code, "team_id": code, "name": name,
         "short_name": short, "as_of": STAMP}
        for code, (short, name, _, _) in CLUBS.items()
    ]))
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": g, "is_finished": False,
         "deadline_utc": pd.Timestamp(f"2099-08-{10 + g:02d} 17:30", tz="UTC"),
         "as_of": STAMP}
        for g in (1, 2, 3)
    ]))
    wh.append("fact_fixture", pd.DataFrame([
        {"season": SEASON, "fixture_id": fid, "gw": gw,
         "kickoff_utc": pd.Timestamp(KICKOFF[fid], tz="UTC"),
         "home_team_code": h, "away_team_code": a, "finished": False,
         "home_score": None, "away_score": None, "as_of": STAMP}
        for fid, gw, h, a in FIXTURES
    ]))
    if odds is not None:
        wh.append("fact_odds", odds)
    wh.close()
    if with_ratings:
        _write_ratings(path, fitted_at=fitted_at or pd.Timestamp.now(tz="UTC"))
    return path


def _write_ratings(db_path, *, fitted_at, season=SEASON):
    """A fixture_ratings.parquet as ``--build`` would leave it."""
    pd.DataFrame([
        {"season": season, "team_code": code, "attack": atk, "defence": dfn,
         "is_promoted": code in (3, 4), "matches_seen": 38,
         "intercept": 0.2386, "home_adv": 0.1890, "rho": -0.0739,
         "mean_attack": 0.0, "mean_defence": -0.1425,
         "half_life_days": 400.0, "n_matches": 1530, "effective_n": 512.0,
         "converged": True, "fitted_at": fitted_at, "snapshot_as_of": fitted_at}
        for code, (_, _, atk, dfn) in CLUBS.items()
    ]).to_parquet(db_path.parent / fxmod.RATINGS_NAME, index=False)


def _cell(result, short_name, gw, index=0):
    team = next(t for t in result["teams"] if t["short_name"] == short_name)
    slot = next(f for f in team["fixtures"] if f["gw"] == gw)
    return slot["opponents"][index]


# --------------------------------------------------------------------------
# registration and the empty contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["fixture_board", "fixture_detail"])
def test_the_new_scripts_are_registered_with_both_schemas(name):
    assert name in registered()
    spec = script(name)
    assert spec.params_schema["type"] == "object"
    assert spec.result_schema.get("oneOf"), "result schema must admit the empty shape"
    assert len(spec.doc) > 20
    assert spec.title and spec.description


@pytest.mark.parametrize("name,params", [
    ("fixture_board", {}), ("fixture_detail", {"fixture_id": 1}),
])
def test_an_empty_warehouse_yields_only_empty_and_a_fixable_reason(name, params, tmp_path):
    path = tmp_path / "fpl.duckdb"
    Warehouse(path).close()
    run = run_script(name, params, db=path)
    # Disjoint from the real schema: not an empty rows list beside a summary,
    # which renders as a live panel with nothing in it.
    assert set(run.result) == {"empty", "reason"}
    assert "ingest" in run.result["reason"].lower()


def test_a_bad_as_of_reads_nothing_rather_than_reading_now(tmp_path):
    path = _seed(tmp_path)
    run = run_script("fixture_board", {"as_of": "not-a-date"}, db=path)
    assert run.result["empty"] is True
    assert "iso" in run.result["reason"].lower()


# --------------------------------------------------------------------------
# THE contract: two numbers, never merged
# --------------------------------------------------------------------------


def test_opponent_only_is_identical_for_two_clubs_facing_the_same_opponent(tmp_path):
    """The page's biggest claim, as an assertion.

    ARS host HUL in GW1 and CHE host HUL in GW2. Same opponent, same venue. The
    opponent-only lens holds our club at league average, so these two cells must
    be byte-identical. The moment they differ, the ticker has become a power
    ranking and rows have stopped being comparable.
    """
    run = run_script("fixture_board", {"horizon": 3}, db=_seed(tmp_path))
    ars = _cell(run.result, "ARS", 1)["opponent_only"]
    che = _cell(run.result, "CHE", 2)["opponent_only"]
    assert ars == che, "opponent-only must not depend on which club is asking"
    assert ars["attack_ease"] is not None and ars["defence_ease"] is not None


def test_fixture_specific_differs_for_those_same_two_clubs(tmp_path):
    """...and the other half of the claim: the fixture-specific number is a
    DIFFERENT number, served on the same cell, with our own strength in it."""
    run = run_script("fixture_board", {"horizon": 3}, db=_seed(tmp_path))
    ars = _cell(run.result, "ARS", 1)["fixture_specific"]
    che = _cell(run.result, "CHE", 2)["fixture_specific"]
    assert ars != che
    # Arsenal's attack is far stronger, so their expected goals must be higher
    # against the same opponent at the same venue.
    assert ars["attack_xg"] > che["attack_xg"]
    # And Arsenal's defence is far tighter, so their clean-sheet chance is higher.
    assert ars["p_clean_sheet"] > che["p_clean_sheet"]


def test_the_two_lenses_live_under_separate_keys_and_cannot_be_confused(tmp_path):
    """No flat ``attack_ease`` on the cell: a caller must name which lens it
    means. The result schema forbids the flattened form outright."""
    run = run_script("fixture_board", {"horizon": 1}, db=_seed(tmp_path))
    cell = _cell(run.result, "ARS", 1)
    assert "attack_ease" not in cell and "defence_ease" not in cell
    assert set(cell["opponent_only"]) == set(cell["fixture_specific"])


def test_positive_ease_means_better_on_both_axes(tmp_path):
    """One polarity for both axes, or the diverging scale is meaningless.

    COV are the weakest attack and the leakiest defence in the seed, so hosting
    them must read positive on BOTH lenses; HUL have the league's tightest
    defence of the four, so they must read negative on the attack lens.
    """
    run = run_script("fixture_board", {"horizon": 3}, db=_seed(tmp_path))
    vs_cov = _cell(run.result, "ARS", 2)["opponent_only"]
    vs_hul = _cell(run.result, "ARS", 1)["opponent_only"]
    assert vs_cov["attack_ease"] > 0 and vs_cov["defence_ease"] > 0
    assert vs_hul["attack_ease"] < vs_cov["attack_ease"]
    assert vs_hul["defence_ease"] > 0, "a low-scoring opponent is an easy clean sheet"


def test_rank_one_is_the_easiest_fixture_not_the_hardest(tmp_path):
    """A rank whose direction is guessed is a rank that gets rendered backwards."""
    run = run_script("fixture_board", {"horizon": 3}, db=_seed(tmp_path))
    assert run.result["scale"]["rank_convention"].startswith("1 = easiest")
    easy = _cell(run.result, "ARS", 2)["opponent_only"]   # hosting COV
    hard = _cell(run.result, "COV", 2)["opponent_only"]   # visiting ARS
    assert easy["attack_rank"] < hard["attack_rank"]
    assert easy["defence_rank"] < hard["defence_rank"]


def test_the_divergent_strip_finds_the_fixture_the_blend_would_hide(tmp_path):
    """HUL are a kind defensive fixture and a mean attacking one. That gap is
    the finding a single blended number erases, so it is surfaced by name."""
    run = run_script("fixture_board", {"horizon": 3, "divergence_ranks": 2},
                     db=_seed(tmp_path))
    hits = [d for d in run.result["divergent"] if d["opponent"] == "HUL"]
    assert hits, "the torn fixture must be called out"
    top = hits[0]
    assert top["gap"] == top["attack_rank"] - top["defence_rank"]
    assert "blended" in top["sentence"]


# --------------------------------------------------------------------------
# honest gaps
# --------------------------------------------------------------------------


def test_without_the_ratings_artefact_every_number_is_null_with_a_reason(tmp_path):
    """No cached fit means no difficulty at all -- not a default, not a 0.5 --
    and the reason must name the command that fixes it."""
    path = _seed(tmp_path, with_ratings=False)
    run = run_script("fixture_board", {"horizon": 1}, db=path)
    assert run.result["row_count"] == 4
    cell = _cell(run.result, "ARS", 1)
    for lens in ("opponent_only", "fixture_specific"):
        assert cell[lens]["attack_ease"] is None
        assert cell[lens]["defence_ease"] is None
        reason = cell[lens]["unavailable"]
        assert reason and "--build" in reason
    assert run.result["scale"]["available"] is False
    fitted = next(i for i in run.result["inputs"] if i["name"] == "fitted ratings")
    assert fitted["state"] == "missing"


def test_a_ratings_artefact_for_another_season_is_absence_not_data(tmp_path):
    path = _seed(tmp_path, with_ratings=False)
    _write_ratings(path, fitted_at=pd.Timestamp.now(tz="UTC"), season="2019-20")
    run = run_script("fixture_board", {"horizon": 1}, db=path)
    assert _cell(run.result, "ARS", 1)["opponent_only"]["attack_ease"] is None
    assert "different season" in run.result["scale"]["unavailable"]


def test_a_stale_fit_is_still_served_but_says_how_stale(tmp_path):
    """A week-old fitted rating beats a made-up fresh one, so the numbers stay;
    what changes is that the reader is told."""
    old = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)
    run = run_script("fixture_board", {"horizon": 1},
                     db=_seed(tmp_path, fitted_at=old))
    assert _cell(run.result, "ARS", 1)["opponent_only"]["attack_ease"] is not None
    fitted = next(i for i in run.result["inputs"] if i["name"] == "fitted ratings")
    assert fitted["state"] == "stale"
    assert fitted["age_hours"] > fxmod.RATINGS_STALE_HOURS
    assert fitted["effect_when_stale"]


def test_blank_and_double_gameweeks_are_explicit(tmp_path):
    run = run_script("fixture_board", {"horizon": 3}, db=_seed(tmp_path))
    cov = next(t for t in run.result["teams"] if t["short_name"] == "COV")
    gw3 = next(f for f in cov["fixtures"] if f["gw"] == 3)
    assert gw3["blank"] is True and gw3["opponents"] == []
    assert cov["n_blanks"] == 1
    ars = next(t for t in run.result["teams"] if t["short_name"] == "ARS")
    ars_gw3 = next(f for f in ars["fixtures"] if f["gw"] == 3)
    assert ars_gw3["double"] is True and len(ars_gw3["opponents"]) == 2
    assert ars["n_doubles"] == 1 and ars["n_fixtures"] == 4


def test_a_form_window_too_short_to_mean_anything_says_so(tmp_path):
    """Nothing is finished in the seed, so there is no form and the panel must
    say that rather than drawing a sparkline over nothing."""
    run = run_script("fixture_board", {"horizon": 1}, db=_seed(tmp_path))
    ars = next(t for t in run.result["teams"] if t["short_name"] == "ARS")
    assert ars["form"]["window_matches"] == 0
    assert ars["form"]["unavailable"]


# --------------------------------------------------------------------------
# freshness
# --------------------------------------------------------------------------


def test_every_input_reports_its_own_age_and_what_staleness_costs(tmp_path):
    run = run_script("fixture_board", {"horizon": 1}, db=_seed(tmp_path))
    inputs = run.result["inputs"]
    assert {i["name"] for i in inputs} >= {
        "fitted ratings", "schedule", "market odds", "team form (xG)"}
    for row in inputs:
        assert row["state"] in ("fresh", "stale", "missing", "failed")
        assert row["effect_when_stale"], f"{row['name']} does not say what staleness does"
        assert row["detail"]


@pytest.mark.parametrize("age,expected", [
    (0.5, "priced"),
    (fxmod.ODDS_STALE_HOURS + 0.1, "stale"),
    (fxmod.ODDS_USELESS_HOURS + 0.1, "expired"),
    (None, "unpriced"),
])
def test_market_state_is_a_function_of_age_not_of_hope(age, expected):
    assert fxmod._market_state(age) == expected


def test_an_unpriced_fixture_says_so_rather_than_looking_priced(tmp_path):
    run = run_script("fixture_board", {"horizon": 1}, db=_seed(tmp_path))
    market = _cell(run.result, "ARS", 1)["market"]
    assert market["state"] == "unpriced"
    assert market["age_hours"] is None
    assert market["reason"]


def test_the_odds_path_lowercases_selections_or_devig_finds_nothing(tmp_path):
    """A regression guard on a live bug in another module.

    ``team_goals.odds.devig_frame`` matches ``("home", "draw", "away")`` and
    ``startswith("over")``; every row in the live ``fact_odds`` is upper case.
    Unpatched, de-vigging returns zero fixtures against a warehouse holding
    131,921 odds rows. This asserts the workaround is still in place.
    """
    from fpl_edge.models.team_goals.odds import devig_frame

    raw = pd.DataFrame([
        {"fixture_key": f"{SEASON}:2026-08-14:arsenal:hull", "bookmaker": "bk1",
         "market": "h2h", "selection": sel, "price_decimal": price,
         "as_of": pd.Timestamp.now(tz="UTC")}
        for sel, price in (("HOME", 1.5), ("DRAW", 4.0), ("AWAY", 7.0))
    ])
    assert devig_frame(raw) == {}, "upstream still cannot read upper-case selections"

    path = _seed(tmp_path, odds=raw)
    with __import__("fpl_edge.platform.query", fromlist=["read_copy"]).read_copy(path) as wh:
        wh.source_path = path
        odds, reason = fxmod._resolved_odds(wh, SEASON, dt.datetime.now(UTC))
    assert reason is None and not odds.empty
    assert set(odds["selection"]) == {"home", "draw", "away"}
    assert devig_frame(odds), "the panel's normalised frame must de-vig"


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------


def test_the_calibration_says_tie_breaker_and_shows_its_working(tmp_path):
    run = run_script("fixture_board", {"horizon": 3}, db=_seed(tmp_path))
    calib = run.result["calibration"]
    model = calib["model"]
    assert model["horizon_gws"] == 3
    assert model["fixture_swing_attack_pts"] > 0
    # Who you are must beat who you play, or the page's second sentence is wrong.
    assert model["ratio_attack"] > 1.0
    assert "tie" in calib["headline"].lower() or "tie" in calib["headline"]


def test_calibration_is_null_with_a_reason_when_nothing_can_be_measured(tmp_path):
    run = run_script("fixture_board", {"horizon": 1},
                     db=_seed(tmp_path, with_ratings=False))
    calib = run.result["calibration"]
    assert calib["model"] is None and calib["empirical"] is None
    assert "--build" in calib["unavailable"]


def test_the_empirical_calibration_is_a_six_gameweek_total_not_six_singles(tmp_path):
    """The arithmetic error this guards against inflates fixtures ~3x.

    Six times a per-fixture spread is not a six-gameweek spread: over a horizon
    the fixture component averages out across clubs while team quality does not.
    A synthetic schedule where every club faces every opponent equally must show
    a fixture spread of ZERO over the full round-robin, and a positive team
    spread -- which only holds if the aggregation walks real schedules.
    """
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    stamp = pd.Timestamp("2026-01-01", tz="UTC")
    codes = [1, 2, 3, 4]
    rows, prows, fid = [], [], 1
    # A double round robin: each club meets each other home and away, so over
    # the whole schedule every club's opponent set is identical.
    rounds = [[(1, 2), (3, 4)], [(1, 3), (4, 2)], [(1, 4), (2, 3)],
              [(2, 1), (4, 3)], [(3, 1), (2, 4)], [(4, 1), (3, 2)]]
    for gw, pairs in enumerate(rounds, start=1):
        for home, away in pairs:
            kickoff = stamp + pd.Timedelta(days=fid)
            # as_of AFTER kickoff: the warehouse refuses a finished fixture that
            # claims to be observable before it was played, and rightly so.
            observed = kickoff + pd.Timedelta(hours=3)
            rows.append({"season": "2025-26", "fixture_id": fid, "gw": gw,
                         "kickoff_utc": kickoff,
                         "home_team_code": home, "away_team_code": away,
                         "finished": True, "home_score": 1, "away_score": 1,
                         "as_of": observed})
            for team, opp in ((home, away), (away, home)):
                prows.append({"season": "2025-26", "code": team * 100, "fixture_id": fid,
                              "gw": gw, "minutes": 90, "starts": 1,
                              "total_points": 2.0 + team, "was_home": team == home,
                              "as_of": observed})
            fid += 1
    wh.append("fact_fixture", pd.DataFrame(rows))
    wh.append("fact_player_fixture", pd.DataFrame(prows))
    wh.append("dim_player", pd.DataFrame([
        {"season": "2025-26", "code": c * 100, "element_id": c, "web_name": f"P{c}",
         "first_name": "P", "second_name": str(c), "position": 3,
         "team_code": c, "as_of": stamp} for c in codes]))
    wh.close()

    from fpl_edge.platform.query import read_copy
    with read_copy(path) as w:
        out = fxmod.build_calibration(
            w, seasons=("2025-26",), min_starts=8, min_clubs=4)
    assert not out.empty
    row = out.iloc[0]
    # Points depend only on the club, never on the opponent, so the fixture
    # component must be indistinguishable from zero and the team one must not.
    assert row["fixture_pts_6gw"] == pytest.approx(0.0, abs=1e-6)
    assert row["team_pts_6gw"] > 1.0


# --------------------------------------------------------------------------
# the drilldown
# --------------------------------------------------------------------------


def test_the_derived_clean_sheet_is_never_called_a_market(tmp_path):
    """All 3,260 ``clean_sheet`` rows in the live warehouse carry
    ``bookmaker='derived#poisson'``: they are this repo's own inversion written
    back. Calling a derivation a bookmaker price is the most misleading thing
    this page could do, so it gets its own key and a warning."""
    odds = pd.DataFrame([
        {"fixture_key": f"{SEASON}:2026-08-14:arsenal:hull",
         "bookmaker": "derived#poisson", "market": "clean_sheet",
         "selection": "HOME", "price_decimal": 3.0,
         "as_of": pd.Timestamp.now(tz="UTC")},
    ])
    run = run_script("fixture_detail", {"fixture_id": 1}, db=_seed(tmp_path, odds=odds))
    derived = run.result["derived_clean_sheet"]
    assert derived["available"] is True
    assert derived["is_a_market"] is False
    assert "NOT a posted market" in derived["warning"]
    assert derived["method"] == "derived#poisson"
    # ...and it never leaks into the market block.
    assert run.result["market"]["available"] is False


def test_the_drilldown_is_fixture_specific_and_says_so(tmp_path):
    run = run_script("fixture_detail", {"fixture_id": 1}, db=_seed(tmp_path))
    model = run.result["model"]
    assert model["available"] is True
    assert model["home"]["opponent_only"] != model["home"]["fixture_specific"]
    assert "fixture_specific" in model["match"]["basis"]
    assert any("FIXTURE-SPECIFIC" in n for n in run.result["notes"])


def test_creator_team_talk_degrades_by_naming_what_would_fill_it(tmp_path):
    """An empty ``content_insight`` renders as a named gap, not whitespace.

    The reason changed once the extraction was wired in: an empty table used to
    mean "nothing calls the extractor" and now means "nothing analysed has
    produced a team-level observation yet", which has a different remedy. The
    gap names the remedy.
    """
    run = run_script("fixture_detail", {"fixture_id": 1}, db=_seed(tmp_path))
    talk = run.result["creator_team_talk"]
    assert talk["available"] is False
    assert talk["rows"] == 0
    assert "backfill-insights" in talk["unavailable"], talk["unavailable"]


def test_a_content_table_that_does_not_exist_is_a_gap_not_a_traceback(tmp_path):
    """`intel_item`, `set_piece_duty` and `content_insight` are created by
    feature migrations. A warehouse whose migrations have only partly run is a
    real state, and an optional section must not take the whole panel down."""
    run = run_script("fixture_detail", {"fixture_id": 1},
                     db=_seed(tmp_path, content_tables=False))
    assert run.result.get("empty") is not True
    talk = run.result["creator_team_talk"]
    assert talk["available"] is False
    assert "migration" in talk["unavailable"]
    assert run.result["intel"]["available"] is False


def test_previous_meetings_carry_their_own_caution(tmp_path):
    run = run_script("fixture_detail", {"fixture_id": 1}, db=_seed(tmp_path))
    meetings = run.result["previous_meetings"]
    assert meetings["available"] is False
    assert "nothing to infer" in meetings["unavailable"]


def test_a_missing_predicted_xi_names_the_publisher_lead_time(tmp_path):
    run = run_script("fixture_detail", {"fixture_id": 1}, db=_seed(tmp_path))
    xi = run.result["predicted_lineups"]
    assert xi["available"] is False
    assert "rotowire" in xi["unavailable"]


def test_model_and_market_are_flagged_when_they_disagree_never_averaged():
    model = {"available": True,
             "match": {"p_home_win": 0.50, "p_draw": 0.25, "p_away_win": 0.25,
                       "p_over_2_5": 0.60},
             "home": {"fixture_specific": {"p_clean_sheet": 0.30}},
             "away": {"fixture_specific": {"p_clean_sheet": 0.20}}}
    market = {"available": True, "age_hours": 4.0,
              "p_home_win": 0.40, "p_draw": 0.26, "p_away_win": 0.34,
              "p_over_2_5": 0.61,
              "implied": {"p_home_clean_sheet": 0.29, "p_away_clean_sheet": 0.21}}
    rows = {r["metric"]: r for r in fxmod._disagreement(model, market)}
    assert rows["P(home win)"]["flagged"] is True   # 10pp apart
    assert rows["P(draw)"]["flagged"] is False      # 1pp apart
    assert rows["P(over 2.5)"]["flagged"] is False
    # The pair is carried side by side; no blended field exists to read.
    assert set(rows["P(home win)"]) == {
        "metric", "model", "market", "gap_pp", "flagged", "market_age_hours"}


def test_the_detail_carries_the_age_of_every_input_it_used(tmp_path):
    run = run_script("fixture_detail", {"fixture_id": 1}, db=_seed(tmp_path))
    names = {i["name"] for i in run.result["inputs"]}
    assert names >= {"fitted ratings", "market odds", "derived clean sheet",
                     "predicted XI", "team intel", "team news", "creator team-talk"}
    for row in run.result["inputs"]:
        assert row["effect_when_stale"] and row["detail"]


# --------------------------------------------------------------------------
# the budget
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name,params", [
    ("fixture_board", {"horizon": 3}), ("fixture_detail", {"fixture_id": 1}),
])
def test_the_panels_stay_inside_the_soft_budget(name, params, tmp_path):
    run = run_script(name, params, db=_seed(tmp_path))
    assert run.performance == "ok", run.notes


def test_a_backdated_as_of_against_a_newer_fit_warns_about_leakage(tmp_path):
    """Every warehouse read here is point-in-time; a cached artefact cannot be.

    A backtest that asks for GW2 and gets a fit trained through GW10 will look
    brilliant and be worthless. The panel cannot fix that -- refitting inside a
    panel is the thing this module exists not to do -- so it says so.
    """
    future = pd.Timestamp("2099-01-01", tz="UTC")
    path = _seed(tmp_path, fitted_at=future)
    run = run_script("fixture_board", {"horizon": 1}, db=path)
    leak = [n for n in run.result["notes"] if "LEAKAGE" in n]
    assert leak, "a fit from after the requested instant must be called out"
    assert "2099" in leak[0]
    # ...and it must NOT fire when the fit predates the request.
    clean = _seed(tmp_path / "clean", fitted_at=pd.Timestamp("2026-01-01", tz="UTC"))
    run2 = run_script("fixture_board", {"horizon": 1}, db=clean)
    assert not any("LEAKAGE" in n for n in run2.result["notes"])


def test_a_player_dropped_from_the_xi_does_not_stay_in_it(tmp_path):
    """rotowire drops a player by ceasing to emit them, not by writing false.

    So "latest row per player" resurrects everyone ever named: the player's own
    last row still says predicted_start = true, forever. Live, this returned a
    thirteen-man Crystal Palace XI for GW2 -- the real eleven plus two dropped
    the day before. The dedupe has to be the latest SNAPSHOT per team; a player
    absent from it is not in the XI.
    """
    path = _seed(tmp_path)
    old = pd.Timestamp("2026-08-13 10:00", tz="UTC")
    new = pd.Timestamp("2026-08-14 10:00", tz="UTC")
    from fpl_edge.ingest.projections.store import ProjectionStore
    wh = Warehouse(path)
    store = ProjectionStore(wh)
    rows = []
    # Yesterday: codes 1-11 start. Today: 11 is dropped and 12 comes in, and
    # rotowire says nothing at all about 11 in the newer snapshot.
    for code in range(1, 12):
        rows.append({"provider": "rotowire", "season": SEASON, "gw": 1,
                     "code": code, "team_code": 1, "predicted_start": True,
                     "certainty": "expected", "as_of": old})
    for code in list(range(1, 11)) + [12]:
        rows.append({"provider": "rotowire", "season": SEASON, "gw": 1,
                     "code": code, "team_code": 1, "predicted_start": True,
                     "certainty": "expected", "as_of": new})
    store.append("fact_predicted_lineup", pd.DataFrame(rows))
    wh.close()

    run = run_script("fixture_detail", {"fixture_id": 1}, db=path)
    xi = run.result["predicted_lineups"]["by_team"]["1"]
    starters = [r for r in xi if r["predicted_start"]]
    assert len(starters) == 11, (
        f"an XI is eleven; got {len(starters)} because a dropped player kept "
        "his stale predicted_start from an older snapshot")


def _seed_insight(path, *, team_code, name, claim, season=SEASON):
    """One team-level content_insight row, as the extractor would write it."""
    import hashlib
    wh = Warehouse(path)
    ins_id = hashlib.sha256(f"{team_code}{claim}".encode()).hexdigest()[:32]
    wh.sql(
        "INSERT INTO content_insight (insight_id, item_id, creator, source_key, "
        "topic, entity_kind, player_code, entity_ref, entity_name, claim_text, "
        "quote, start_s, horizon_gw, horizon_gw_end, confidence, published_at, "
        "season, gameweek, extractor, team_code) VALUES "
        "(?, 'item-1', 'Someone', 'src', 'tactical', 'team', NULL, ?, ?, ?, "
        "?, NULL, NULL, NULL, 0.6, ?, ?, 1, 'llm:test', ?)",
        [ins_id, str(name).lower(), name, claim, f'"{claim}"',
         pd.Timestamp("2026-08-13 09:00", tz="UTC"), season, team_code],
    )
    wh.close()


def test_team_talk_shows_only_the_two_clubs_in_this_fixture(tmp_path):
    """It took the fixture's team codes and ignored them.

    Every team-level insight in the season came back for every fixture, so an
    ARS v HUL drawer carried opinions about Coventry under a heading saying they
    were about this match. Nothing looked broken: the quotes were real, the
    creators were real, and the wrong club was never named on screen.
    """
    path = _seed(tmp_path)
    _seed_insight(path, team_code=1, name="Arsenal",
                  claim="Arsenal press higher since the break.")
    _seed_insight(path, team_code=4, name="Coventry",
                  claim="Coventry sit deep away from home.")

    # fixture 1 is ARS (1) at home to HUL (3). Coventry is in neither.
    run = run_script("fixture_detail", {"fixture_id": 1}, db=path)
    talk = run.result["creator_team_talk"]
    assert talk["available"] is True, talk.get("unavailable")
    names = {r["entity_name"] for r in talk["items"]}
    assert names == {"Arsenal"}, (
        f"the drawer for ARS v HUL showed {sorted(names)}; an insight about a "
        "club that is not playing in this fixture is not about this fixture")


def test_an_unattributable_insight_is_counted_never_shown(tmp_path):
    """A club the resolver refused is excluded AND disclosed.

    Silently dropping it would make "no team-talk" and "team-talk we could not
    place" look identical, which is the same failure as showing it under the
    wrong club -- just quieter.
    """
    path = _seed(tmp_path)
    _seed_insight(path, team_code=1, name="Arsenal",
                  claim="Arsenal press higher since the break.")
    _seed_insight(path, team_code=None, name="Suddenland",
                  claim="Sunderland keep an unchanged eleven.")

    run = run_script("fixture_detail", {"fixture_id": 1}, db=path)
    talk = run.result["creator_team_talk"]
    assert {r["entity_name"] for r in talk["items"]} == {"Arsenal"}
    assert "1 team-level insight" in talk["note"], talk["note"]
    assert "refused" in talk["note"]


# --------------------------------------------------------------------------
# regression: the NULL-ord set-piece row that 500'd a third of the board
# --------------------------------------------------------------------------


def _seed_duty(path, *, team_code, code, duty, ord, season=SEASON):
    """One set_piece_duty row, exactly as bootstrap-static ingestion writes it.

    ``ord`` may be None: the schema documents NULL as "dropped off the list",
    and the live FPL API also lists takers with no rank. Both are real rows.
    """
    from fpl_edge.intel.store import IntelStore
    wh = Warehouse(path)
    IntelStore(wh)  # applies the intel migrations (intel_item, set_piece_duty)
    wh.sql(
        "INSERT INTO set_piece_duty (season, code, duty, ord, note, team_code, "
        "source, as_of) VALUES (?, ?, ?, ?, NULL, ?, "
        "'fpl_api:bootstrap-static', ?)",
        [season, code, duty, ord, team_code,
         pd.Timestamp("2026-08-13 09:00", tz="UTC")],
    )
    wh.close()


def test_a_null_duty_order_is_served_not_a_crash(tmp_path):
    """The live data shape behind the 21-of-60 fixture_detail 500s.

    Four clubs carried set_piece_duty rows with ``ord IS NULL`` (the FPL API
    names a taker without ranking him, and the store also writes NULL for a
    player dropped off a list). ``int(NaN)`` in the intel block then took the
    WHOLE drawer down for every fixture involving those clubs -- a silent dead
    click on a third of the board. The row must be served with order: null.
    """
    path = _seed(tmp_path)
    _seed_duty(path, team_code=1, code=101, duty="penalties", ord=None)
    _seed_duty(path, team_code=1, code=102, duty="corners_indirect", ord=1)

    run = run_script("fixture_detail", {"fixture_id": 1}, db=path)
    assert run.result.get("empty") is not True
    intel = run.result["intel"]
    assert intel["available"] is True, intel.get("unavailable")
    duties = intel["set_piece_duty"]["1"]
    by_duty = {d["duty"]: d for d in duties}
    assert by_duty["penalties"]["order"] is None, (
        "a NULL ord is an absent order, not an error and not a zero")
    assert by_duty["corners_indirect"]["order"] == 1


def test_a_crashing_optional_section_degrades_to_a_named_gap(tmp_path, monkeypatch):
    """An optional section that hits a data edge must yield the house gap
    shape for THAT section -- naming the exception -- not 500 the panel."""
    path = _seed(tmp_path)

    def _boom(*args, **kwargs):
        raise ValueError("a data edge this section cannot survive")

    monkeypatch.setattr(fxmod, "_intel_block", _boom)
    run = run_script("fixture_detail", {"fixture_id": 1}, db=path)
    assert run.result.get("empty") is not True, "the panel must still serve"
    intel = run.result["intel"]
    assert intel["available"] is False
    assert "ValueError" in intel["unavailable"]
    assert "dropped rather than guessed" in intel["unavailable"]
    # ...and the untouched sections are unaffected.
    assert run.result["model"] is not None
