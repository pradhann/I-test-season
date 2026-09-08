"""The report card: three channels, three floors, and no blended score.

Hermetic. Every test builds its own DuckDB file, runs the content migrations
through :class:`ContentStore`, and plants the exact rows under test.

The fixture carries the failure modes this card exists to prevent, so each one
is a test rather than a paragraph:

* **The rank that is noise.** A creator with 20 scored claims at 65% looks
  better than one with 103 at 39%, and is not distinguishable from a coin flip.
  Anything below ``MIN_SCORED_CLAIMS`` must come back ``quotable: False``.
* **The blended score.** There must be NO key anywhere in the payload that
  mixes the binary channel with the measured-team channel. A test asserts the
  absence, because the pressure to add one is permanent.
* **The unread team read as a bad team.** A verified entry with no crawled
  gameweek is ``n_gw: 0`` plus a reason, never ``points: 0``.
* **The leak.** A ``creator_score`` row and a ``claim_outcome`` row stamped in
  the future must not reach a card answering "now".
* **The zero-width interval.** One measured gameweek yields a mean delta and an
  explicit ``None`` interval, never ``[x, x]``.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registers the scripts)
from fpl_edge.eval.creator_report_card import (
    MIN_GW_MEASURED,
    MIN_SCORED_CLAIMS,
    claims_channel,
    numeric_channel,
    provider_key,
    team_channel,
    wilson_interval,
)
from fpl_edge.platform.registry import run_script
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc
SEASON = "2026-27"

NOW = dt.datetime.now(UTC)
PAST = NOW - dt.timedelta(days=2)
FUTURE = NOW + dt.timedelta(days=2)
SEEDED = NOW - dt.timedelta(days=40)


# ---------------------------------------------------------------------------
# The pure channels.

def test_the_wilson_interval_brackets_the_lower_bound_the_weights_run_on():
    lo, hi = wilson_interval(13, 20)
    assert lo == pytest.approx(0.4329, abs=1e-4)
    assert lo < 13 / 20 < hi <= 1.0


def test_a_perfect_record_still_has_an_interval_below_one():
    lo, hi = wilson_interval(6, 6)
    assert hi == 1.0
    assert lo < 1.0, "6/6 must not be reported as certainty"


def test_no_observations_yields_no_interval_not_a_zero():
    assert wilson_interval(0, 0) == (None, None)


def test_a_record_under_the_house_floor_is_not_quotable():
    card = claims_channel({"claims_total": 33, "claims_scored": 20, "hits": 13,
                           "hit_rate": 0.65, "weight": 0.0})
    assert card["quotable"] is False
    assert card["n_scored"] == 20 < MIN_SCORED_CLAIMS
    assert str(MIN_SCORED_CLAIMS) in card["reason"]
    assert card["vs_coin_flip"] == "indistinguishable"


def test_a_record_over_the_floor_is_quotable_and_still_carries_its_interval():
    card = claims_channel({"claims_total": 129, "claims_scored": 103,
                           "hits": 40, "hit_rate": 40 / 103, "weight": 0.0})
    assert card["quotable"] is True
    assert card["ci95"] == [card["wilson_lo95"], card["wilson_hi95"]]
    # 40/103 is genuinely below a coin flip and the card is allowed to say so.
    assert card["vs_coin_flip"] == "below"


def test_an_unscored_creator_is_unmeasured_with_a_reason_not_zeros():
    card = claims_channel({"claims_total": 12, "claims_scored": 0, "hits": 0,
                           "hit_rate": None, "weight": 0.0})
    assert card["measured"] is False
    assert card["hit_rate"] is None
    assert card["vs_coin_flip"] == "unmeasured"
    assert "finalised" in card["reason"]


def test_no_score_row_at_all_is_null_everywhere_never_a_zero_rate():
    card = claims_channel(None)
    assert card["hit_rate"] is None and card["wilson_lo95"] is None
    assert card["n_scored"] == 0 and card["quotable"] is False


def test_the_weight_zero_is_reported_because_it_is_a_measurement():
    card = claims_channel({"claims_total": 200, "claims_scored": 170,
                           "hits": 80, "hit_rate": 80 / 170, "weight": 0.0})
    assert card["weight"] == 0.0 and card["earned"] is False


def test_breakdowns_are_emitted_but_never_quotable_at_split_sizes():
    outcomes = [{"gameweek": 1, "action": "captain", "hit": True}] * 6
    outcomes += [{"gameweek": 2, "action": "hold", "hit": False}] * 4
    card = claims_channel({"claims_total": 10, "claims_scored": 10, "hits": 6,
                           "hit_rate": 0.6, "weight": 0.0}, outcomes)
    by_action = {row["action"]: row for row in card["by_action"]}
    assert by_action["captain"]["n"] == 6 and by_action["captain"]["hits"] == 6
    assert all(row["quotable"] is False for row in card["by_action"])
    assert {row["gw"] for row in card["by_gw"]} == {1, 2}


def test_unscoreable_outcomes_land_in_neither_side_of_a_breakdown():
    outcomes = [{"gameweek": 1, "action": "buy", "hit": None},
                {"gameweek": 1, "action": "buy", "hit": True}]
    card = claims_channel({"claims_total": 2, "claims_scored": 1, "hits": 1,
                           "hit_rate": 1.0, "weight": 0.0}, outcomes)
    assert card["by_action"][0]["n"] == 1


# ---------------------------------------------------------------------------
# The numeric channel: absent, and absent for a stated reason.

def test_the_numeric_channel_is_empty_with_a_reason_not_a_zero():
    card = numeric_channel(None)
    assert card["measured"] is False and card["quotable"] is False
    assert card["mae"] is None and card["rmse"] is None
    assert "expected-points" in card["reason"]


def test_rmse_pools_over_squares_not_over_rmses():
    """Averaging RMSEs directly is wrong; the card must pool the squares."""
    rows = [
        {"gw": 1, "scope": "overall", "metric": "rmse", "value": 2.0,
         "baseline": 2.0, "n_obs": 100},
        {"gw": 2, "scope": "overall", "metric": "rmse", "value": 4.0,
         "baseline": 4.0, "n_obs": 100},
    ]
    card = numeric_channel(rows, provider="somebody")
    # sqrt((4 + 16) / 2) = 3.1623, NOT (2 + 4) / 2 = 3.0
    assert card["rmse"] == pytest.approx(3.1623, abs=1e-4)


def test_mae_pools_observation_weighted():
    rows = [
        {"gw": 1, "scope": "overall", "metric": "mae", "value": 1.0,
         "baseline": 1.5, "n_obs": 100},
        {"gw": 2, "scope": "overall", "metric": "mae", "value": 2.0,
         "baseline": 1.5, "n_obs": 300},
    ]
    card = numeric_channel(rows, provider="somebody")
    assert card["mae"] == pytest.approx(1.75, abs=1e-6)
    assert card["baseline_mae"] == pytest.approx(1.5, abs=1e-6)
    assert card["n_obs"] == 400 and card["quotable"] is True


def test_the_provider_link_squashes_both_sides_of_the_join():
    assert provider_key("FPL Review") == provider_key("fpl_review")
    assert provider_key("Fantasy Football Hub") != provider_key("fplform")


# ---------------------------------------------------------------------------
# The measured-team channel.

def _entry(person: str, entry_id: int, points: list[tuple[int, int]]):
    return {"person": person, "entry_id": entry_id, "verified": True,
            "gws": [{"gw": gw, "points": p} for gw, p in points]}


def _team(entries, baseline):
    return team_channel(
        entries, baseline, baseline_kind="cohort_mean",
        baseline_label="mean of the measured panel cohort",
        baseline_reason="test baseline")


def test_a_verified_entry_with_no_crawled_gameweek_is_unread_not_zero():
    team = _team([_entry("Nobody Crawled", 999, [])], {1: {"points": 66.0, "n": 7}})
    person = team["people"][0]
    assert person["n_gw"] == 0
    assert person["points"] is None, "an unread team must never render 0 points"
    assert "unread rather than empty" in person["reason"]


def test_one_gameweek_gives_a_mean_and_no_interval():
    team = _team([_entry("One Week", 1, [(2, 129)])], {2: {"points": 112.12, "n": 8}})
    person = team["people"][0]
    assert person["mean_delta"] == pytest.approx(16.88, abs=1e-2)
    assert person["delta_ci95"] is None, "n=1 has no spread to estimate from"
    assert person["beats_baseline"] is None


def test_two_gameweeks_give_an_interval_that_contains_zero():
    team = _team([_entry("Harry", 3054, [(1, 74), (2, 108)])],
                 {1: {"points": 66.0, "n": 7}, 2: {"points": 112.12, "n": 8}})
    person = team["people"][0]
    lo, hi = person["delta_ci95"]
    assert lo < 0 < hi
    assert person["beats_baseline"] is None, "an interval spanning zero decides nothing"


def test_the_team_channel_is_never_quotable_however_good_the_numbers():
    team = _team([_entry("Runaway", 7, [(gw, 200) for gw in range(1, 20)])],
                 {gw: {"points": 50.0, "n": 8} for gw in range(1, 20)})
    person = team["people"][0]
    assert person["n_gw"] > MIN_GW_MEASURED
    assert person["beats_baseline"] is True
    assert person["quotable"] is False and team["quotable"] is False
    assert "more than a season" in team["reason"]


def test_no_verified_entry_says_so_rather_than_showing_an_empty_table():
    team = _team([], {})
    assert team["measured"] is False
    assert "stranger's team" in team["reason"]


# ---------------------------------------------------------------------------
# The panel, against a seeded warehouse.

def _score(wh, creator, as_of, *, total, scored, hits, weight=0.0, lo95=0.0):
    wh.sql(
        "INSERT INTO creator_score VALUES (?, 'all', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [creator, as_of, total, scored, hits,
         (hits / scored) if scored else None, lo95, weight, as_of, as_of],
    )


def _outcome(wh, claim_id, creator, gw, action, hit, resolved):
    wh.sql(
        "INSERT INTO claim_outcome VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [claim_id, creator, SEASON, gw, 223094, action, 8.0, "pos4_starter_median",
         5.0, hit, None, resolved],
    )


def _person(wh, key, name, entry_id, show, api_name=None):
    wh.sql(
        "INSERT INTO panel_person (person_key, display_name, entry_id, "
        "entry_verified, entry_api_name, entry_reason, active, as_of) "
        "VALUES (?, ?, ?, ?, ?, ?, TRUE, ?)",
        [key, name, entry_id, entry_id is not None, api_name,
         None if entry_id is not None else "no public id", SEEDED],
    )
    wh.sql(
        "INSERT INTO panel_person_show (person_key, show_creator, source_key, "
        "role, as_of) VALUES (?, ?, NULL, 'host', ?)", [key, show, SEEDED],
    )


def _gw(wh, entry_id, gw, points, rank, as_of=SEEDED):
    wh.append("fact_manager_gw", pd.DataFrame([{
        "entry_id": entry_id, "season": SEASON, "gw": gw, "points": points,
        "total_points": points, "overall_rank": rank, "bank_tenths": 0,
        "value_tenths": 1000, "event_transfers": 0, "event_transfers_cost": 0,
        "points_on_bench": 3, "as_of": as_of,
    }]))


@pytest.fixture()
def seeded_db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    # avg_entry_score is FPL's own published average for the gameweek and is
    # the default baseline: "beat the field" is the question a reader asks,
    # and comparing creators against each other answered a different one.
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 1, "is_finished": True,
         "deadline_utc": NOW - dt.timedelta(days=9), "as_of": SEEDED,
         "avg_entry_score": 50, "highest_score": 131,
         "ranked_count": 8903411},
        {"season": SEASON, "gw": 2, "is_finished": True,
         "deadline_utc": NOW - dt.timedelta(days=2), "as_of": SEEDED,
         "avg_entry_score": 81, "highest_score": 161,
         "ranked_count": 9904815},
    ]))

    from fpl_edge.ingest.content.store import ContentStore
    from fpl_edge.ingest.rivals.schema import migrate as rivals_migrate

    ContentStore(wh)
    # The manager tables live in the rivals package, not schema.sql. Importing
    # that module is also what registers them in PIT_KEYS, so `append` will
    # take them -- the same order the real ingest runs in.
    rivals_migrate(wh)

    # A creator over the floor whose interval sits BELOW the coin flip.
    _score(wh, "Big Sample", PAST, total=129, scored=103, hits=40, lo95=0.2999)
    # A creator under the floor who looks good and is not measurable.
    _score(wh, "Small Sample", PAST, total=33, scored=20, hits=13, lo95=0.4329)
    for i in range(13):
        _outcome(wh, f"h{i}", "Small Sample", 1 + i % 2, "captain", True, PAST)
    for i in range(7):
        _outcome(wh, f"m{i}", "Small Sample", 2, "hold", False, PAST)
    # THE LEAK: a future score row and a future outcome for the same creator.
    _score(wh, "Small Sample", FUTURE, total=999, scored=900, hits=800,
           weight=0.9, lo95=0.85)
    _outcome(wh, "future1", "Small Sample", 2, "buy", True, FUTURE)
    # A creator with claims but nothing scoreable yet.
    _score(wh, "Unscored", PAST, total=4, scored=0, hits=0)

    # Two people on one show: one crawled, one verified-but-unread.
    _person(wh, "crawled", "Crawled Person", 3054, "Small Sample", "Harry D")
    _person(wh, "unread", "Unread Person", 6816, "Small Sample", "Pranil S")
    # A cohort member on another show, so the baseline is not one person.
    _person(wh, "other", "Other Person", 41, "Big Sample", "Andy L")
    _gw(wh, 3054, 1, 74, 279827)
    _gw(wh, 3054, 2, 108, 141593)
    _gw(wh, 41, 1, 58, 900000)
    _gw(wh, 41, 2, 116, 57623)
    wh.close()
    return path


def card(db, **params):
    return run_script("creator_report_card", params, db=db).result


def _by_name(res):
    return {c["creator"]: c for c in res["cards"]}


def test_a_warehouse_without_creator_score_is_an_honest_empty(tmp_path):
    path = tmp_path / "fpl.duckdb"
    Warehouse(path).close()
    res = card(path)
    assert res.get("empty") is True
    assert set(res) == {"empty", "reason"}
    assert "creator_score" in res["reason"]


def test_an_unknown_creator_names_the_ones_that_exist(seeded_db):
    res = card(seeded_db, creator="Nobody At All")
    assert res.get("empty") is True
    assert "Big Sample" in res["reason"] and "Small Sample" in res["reason"]


def test_the_card_validates_against_its_own_registered_schema(seeded_db):
    """run_script validates the result; reaching this line IS the assertion."""
    res = card(seeded_db)
    assert res.get("empty") is not True
    assert len(res["cards"]) >= 3


def test_no_key_anywhere_blends_the_channels(seeded_db):
    """The permanent temptation, asserted against.

    A single 'creator rating' would have to weigh a hit rate against a points
    delta, and there is no exchange rate between them. If a future edit adds
    one, this test names it.
    """
    banned = {"score", "rating", "overall_score", "grade", "composite",
              "combined", "creator_score"}
    for entry in _by_name(card(seeded_db)).values():
        assert not (set(entry) & banned), f"blended key in {entry['creator']}"


def test_a_creator_under_the_floor_is_not_quotable_on_the_panel(seeded_db):
    small = _by_name(card(seeded_db))["Small Sample"]
    assert small["claims"]["n_scored"] == 20
    assert small["claims"]["quotable"] is False
    assert small["quotable"] is False
    assert "not a rank" in small["headline"]


def test_a_creator_over_the_floor_is_quotable(seeded_db):
    big = _by_name(card(seeded_db))["Big Sample"]
    assert big["claims"]["n_scored"] == 103 and big["claims"]["quotable"] is True
    assert big["claims"]["vs_coin_flip"] == "below"


def test_a_future_creator_score_row_is_not_in_force_now(seeded_db):
    small = _by_name(card(seeded_db))["Small Sample"]
    assert small["claims"]["n_scored"] == 20, "the future row leaked in"
    assert small["claims"]["weight"] == 0.0, "the future weight leaked in"


def test_a_future_claim_outcome_is_invisible_in_the_breakdown(seeded_db):
    small = _by_name(card(seeded_db))["Small Sample"]
    actions = {row["action"] for row in small["claims"]["by_action"]}
    assert "buy" not in actions, "the future outcome leaked into the breakdown"
    assert sum(row["n"] for row in small["claims"]["by_action"]) == 20


def test_a_creator_with_claims_but_nothing_scored_says_so(seeded_db):
    unscored = _by_name(card(seeded_db))["Unscored"]
    assert unscored["claims"]["measured"] is False
    assert unscored["claims"]["hit_rate"] is None
    assert "finalised" in unscored["claims"]["reason"]


def test_the_verified_but_uncrawled_person_keeps_a_row_and_a_reason(seeded_db):
    people = {p["person"]: p
              for p in _by_name(card(seeded_db))["Small Sample"]["team"]["people"]}
    assert people["Unread Person"]["n_gw"] == 0
    assert people["Unread Person"]["points"] is None
    assert people["Crawled Person"]["n_gw"] == 2


def test_the_cohort_baseline_spans_every_verified_member_not_just_this_card(
        seeded_db):
    """Asking for ONE creator must not turn the baseline into their own score.

    With a one-person cohort the delta is zero by construction, which would
    read as "exactly average" and mean nothing at all.
    """
    # Explicit baseline: field_average is the default now that dim_event
    # carries FPL's own average_entry_score, and this test is about what the
    # cohort baseline does when one creator is asked for.
    one = card(seeded_db, creator="Small Sample", baseline="cohort_mean")
    by_gw = {b["gw"]: b for b in one["baseline"]["by_gw"]}
    assert by_gw[1]["n"] == 2, "the baseline collapsed to the requested creator"
    assert by_gw[1]["points"] == pytest.approx((74 + 58) / 2)


def test_the_default_baseline_is_the_field_not_the_other_creators(seeded_db):
    """The question is "did they beat the field", and dim_event now carries
    FPL's own answer. Comparing a creator against the other creators on the
    panel answers "better than this room", which is a different claim."""
    res = card(seeded_db)
    assert res["baseline"]["kind"] == "field_average"
    by_gw = {b["gw"]: b for b in res["baseline"]["by_gw"]}
    assert by_gw[1]["points"] == 50 and by_gw[2]["points"] == 81, (
        "the baseline must be FPL's published average, verbatim"
    )
    # The denominator is the whole game, not a sample of it.
    assert by_gw[1]["n"] == 8903411
    assert "average_entry_score" in res["baseline"]["reason"]
    # And the gap it used to report is gone once the numbers are there.
    assert "field_average" not in {g["key"] for g in res["gaps"]}


def test_an_unsettled_gameweek_contributes_no_field_average(seeded_db):
    """FPL reports 0 for a gameweek that has not been played. Storing that
    zero would tell every reader the field scored nothing, so an unfinished
    event carries NULL and is skipped rather than compared against."""
    import pandas as pd_

    from fpl_edge.store.warehouse import Warehouse as _W
    with _W(seeded_db) as wh_:
        wh_.append("dim_event", pd_.DataFrame([
            {"season": SEASON, "gw": 3, "is_finished": False,
             "deadline_utc": NOW + dt.timedelta(days=3), "as_of": SEEDED,
             "avg_entry_score": None, "highest_score": None,
             "ranked_count": None},
        ]))
    gws = {b["gw"] for b in card(seeded_db)["baseline"]["by_gw"]}
    assert 3 not in gws, "an unplayed gameweek became a zero baseline"


def test_the_pool_baseline_is_labelled_as_not_the_field(seeded_db):
    res = card(seeded_db, baseline="crawled_pool_median")
    assert res["baseline"]["kind"] == "crawled_pool_median"
    assert "must not be read as 'the field'" in res["baseline"]["reason"]


def test_the_gaps_name_the_uncrawled_entries_and_the_empty_numeric_channel(
        seeded_db):
    gaps = {g["key"]: g for g in card(seeded_db)["gaps"]}
    assert "6816" in gaps["uncrawled_entries"]["what"]
    assert gaps["no_numeric_channel"]["fix"]


def test_the_field_average_gap_appears_only_while_the_number_is_missing(
        seeded_db):
    """The gap list is a claim about THIS warehouse, so it is checked rather
    than remembered. With averages present the gap is absent; with a newer
    snapshot carrying none, the same code reports it again."""
    import pandas as pd_

    from fpl_edge.store.warehouse import Warehouse as _W

    assert "field_average" not in {g["key"] for g in card(seeded_db)["gaps"]}

    # A later observation in which FPL published no average: the point-in-time
    # read takes the newest row per gameweek, so the baseline empties out.
    later = SEEDED + dt.timedelta(hours=1)
    with _W(seeded_db) as wh_:
        wh_.append("dim_event", pd_.DataFrame([
            {"season": SEASON, "gw": gw, "is_finished": True,
             "deadline_utc": NOW - dt.timedelta(days=9 if gw == 1 else 2),
             "as_of": later, "avg_entry_score": None,
             "highest_score": None, "ranked_count": None}
            for gw in (1, 2)
        ]))
    res = card(seeded_db)
    gaps = {g["key"]: g for g in res["gaps"]}
    assert "field_average" in gaps, (
        "no gameweek carries an average, so the gap must be reported"
    )
    assert "average_entry_score" in gaps["field_average"]["what"]
    assert res["baseline"]["by_gw"] == [], (
        "an absent average must not become a zero baseline"
    )


def test_the_ordering_is_by_evidence_not_by_hit_rate(seeded_db):
    """A 1-for-1 creator on top is the most misleading thing this page could do."""
    names = [c["creator"] for c in card(seeded_db)["cards"]]
    assert names.index("Big Sample") < names.index("Small Sample")


def test_a_lower_min_scored_moves_quotable_but_never_the_weight(seeded_db):
    small = _by_name(card(seeded_db, min_scored=5))["Small Sample"]
    assert small["claims"]["quotable"] is True
    assert small["claims"]["weight"] == 0.0 and small["claims"]["earned"] is False


# ------------------------------------------------------------ the headline
#
# The headline is the one sentence read aloud with no chart beside it, so it
# has to survive every shape the channels can take and obey the house rule.

def test_the_headline_survives_a_measured_gameweek_with_no_baseline_beside_it():
    """`field_average` has a row per gameweek FPL has published, and a crawled
    squad can sit on a gameweek it does not cover. `mean_delta` is then None,
    and formatting None as "+0.0" would invent a comparison that was never
    made."""
    from fpl_edge.platform.scripts.creators import _card_headline

    line, _ = _card_headline(
        "X", {"n_scored": 0}, {"measured": False},
        {"people": [{"person": "P", "n_gw": 2, "mean_delta": None, "points": 140}],
         "baseline": {"kind": "field_average", "label": "the field"}},
    )
    assert "140" in line and "2 gameweek(s)" in line
    assert "no baseline on those gameweeks" in line
    assert "+0.0" not in line


def test_the_headline_names_the_baseline_rather_than_its_key():
    """"vs the field_average" is an identifier, not a sentence. The payload
    carries a label written for a reader; the headline uses it."""
    from fpl_edge.platform.scripts.creators import _card_headline

    line, _ = _card_headline(
        "X",
        {"n_scored": 274, "hits": 110, "hit_rate": 0.4, "wilson_lo95": 0.34,
         "wilson_hi95": 0.46, "vs_coin_flip": "below", "quotable": True,
         "min_scored_claims": 25},
        {"measured": False},
        {"people": [{"person": "P", "n_gw": 3, "mean_delta": 15.0, "points": 227}],
         "baseline": {"kind": "field_average",
                      "label": "FPL's published average entry score"}},
    )
    assert "FPL's published average entry score" in line
    assert "field_average" not in line


def test_the_headline_obeys_the_house_rule_on_every_branch():
    from fpl_edge.platform.scripts.creators import _card_headline

    shapes = [
        ({"n_scored": 0}, {"measured": False}, {"people": []}),
        ({"n_scored": 12, "hits": 5, "hit_rate": 0.42, "wilson_lo95": 0.2,
          "wilson_hi95": 0.67, "vs_coin_flip": "indistinguishable",
          "quotable": False, "min_scored_claims": 25},
         {"measured": True, "provider": "p", "mae": 1.0, "baseline_mae": 2.0},
         {"people": [{"person": "P", "n_gw": 0, "mean_delta": None, "points": None}]}),
        ({"n_scored": 30, "hits": 20, "hit_rate": 0.67, "wilson_lo95": 0.52,
          "wilson_hi95": 0.8, "vs_coin_flip": "above", "quotable": True,
          "min_scored_claims": 25},
         {"measured": False},
         {"people": [{"person": "P", "n_gw": 4, "mean_delta": 3.0, "points": 200}],
          "baseline": {"kind": "cohort_mean", "label": "the cohort"}}),
    ]
    for claims, numeric, team in shapes:
        line, _ = _card_headline("X", claims, numeric, team)
        assert " -- " not in line and "—" not in line, line
        assert "None" not in line, line
