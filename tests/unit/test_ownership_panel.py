"""ownership_eo against a seeded warehouse: the season/gw trap, pinned.

The audit (docs/platform/data_audit.md Q6) found ``eo_top10k``/``eo_elite``
stored under season 2025-26 GW38 — last season's final state — while
``eo_predicted`` lives under the current season. The trap test here seeds a
*stale* ``eo_predicted`` row under the old season at a HIGHER gw than the
live one: a pivot that forgets the season filter picks the stale row (gw 38
beats gw 1) and quietly reports last season's EO as current. Verified to fail
when the season filter is removed from the script's live pivot.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registers ownership_eo)
from fpl_edge.platform.registry import run_script
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc
T = pd.Timestamp("2026-08-01 12:00", tz="UTC")
SEASON = "2026-27"
OLD = "2025-26"


def _player(season, code, element_id, name, pos, team_code=1):
    return {"season": season, "code": code, "element_id": element_id,
            "web_name": name, "first_name": "A", "second_name": name,
            "position": pos, "team_code": team_code, "as_of": T}


def _state(season, code, element_id, own, price=100):
    return {"season": season, "code": code, "element_id": element_id,
            "price_tenths": price, "selected_by_pct": own, "status": "a",
            "chance_of_playing_next_round": None, "news": "",
            "news_added": None, "transfers_in_event": 0,
            "transfers_out_event": 0, "cost_change_start": 0, "as_of": T}


@pytest.fixture()
def empty_db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    Warehouse(path).close()
    return path


@pytest.fixture()
def seeded_db(tmp_path):
    """Two current players, one old-season player, EO rows on both sides of
    the season split, and a two-source consensus for GW1."""
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("dim_team", pd.DataFrame([
        {"season": SEASON, "team_code": 1, "team_id": 1, "name": "Arsenal",
         "short_name": "ARS", "as_of": T},
    ]))
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 1, "is_finished": False,
         "deadline_utc": pd.Timestamp("2099-08-14 17:30", tz="UTC"), "as_of": T},
    ]))
    wh.append("dim_player", pd.DataFrame([
        _player(SEASON, 100, 10, "Premium", 4),
        _player(SEASON, 200, 20, "Diff", 3),
        _player(OLD, 300, 30, "OldTemplate", 3),
    ]))
    wh.append("fact_player_state", pd.DataFrame([
        _state(SEASON, 100, 10, 60.0, price=150),
        _state(SEASON, 200, 20, 4.0, price=70),
        _state(OLD, 300, 30, 45.0),
    ]))
    ins = ("INSERT INTO fact_external_ownership "
           "(provider, season, gw, code, metric, value, as_of) VALUES "
           "(?, ?, ?, ?, ?, ?, TIMESTAMPTZ '2026-08-01 12:00:00+00')")
    # live: the current season's predicted EO
    wh.sql(ins, ["livefpl", SEASON, 1, 100, "eo_predicted", 0.95])
    # stale: LAST SEASON's final state — including a stale eo_predicted for
    # the same player at gw 38, the row a season-blind pivot would pick.
    wh.sql(ins, ["livefpl", OLD, 38, 100, "eo_predicted", 0.20])
    wh.sql(ins, ["livefpl", OLD, 38, 300, "eo_top10k", 1.20])
    wh.sql(ins, ["livefpl", OLD, 38, 300, "eo_elite", 1.10])
    wh.sql(ins, ["livefpl", OLD, 38, 100, "eo_top10k", 0.50])
    pj = ("INSERT INTO fact_projection (provider, season, gw, code, xp, as_of) "
          "VALUES (?, ?, ?, ?, ?, TIMESTAMPTZ '2026-08-01 12:00:00+00')")
    for provider, code, xp in (("a", 100, 5.0), ("b", 100, 6.0),
                               ("a", 200, 4.4), ("b", 200, 4.6)):
        wh.sql(pj, [provider, SEASON, 1, code, xp])
    wh.close()
    return path


def run(db, **params):
    params.setdefault("coverage", False)  # tests never reach the network
    return run_script("ownership_eo", params, db=db).result


def test_empty_warehouse_is_an_honest_empty(empty_db):
    res = run(empty_db)
    assert res.get("empty") is True
    assert set(res) == {"empty", "reason"}
    assert "ingest" in res["reason"].lower()


def test_live_eo_ranks_the_template(seeded_db):
    res = run(seeded_db)
    assert res.get("empty") is not True
    top = res["rows"][0]
    assert top["code"] == 100 and top["name"] == "Premium"
    assert top["eo_pred_pct"] == 95.0          # 0.95 fraction -> percent
    assert top["own_pct"] == 60.0              # marginal, no captaincy
    assert top["xpts"] == 5.5                  # mean of the two sources
    assert res["xpts_gw"] == 1


def test_the_trap_stale_season_rows_never_enter_the_current_template(seeded_db):
    """A season-blind pivot would report the 2025-26 GW38 eo_predicted (20%)
    as current, because gw 38 out-sorts gw 1. It must not."""
    res = run(seeded_db)
    by_code = {r["code"]: r for r in res["rows"]}
    assert by_code[100]["eo_pred_pct"] == 95.0, (
        "the 2025-26 GW38 row leaked into the current template"
    )
    # the old-season-only player has no current-season identity at all
    assert 300 not in by_code
    assert all(r["code"] != 300 for r in res["differentials"])


def test_stale_metrics_are_quarantined_and_labelled(seeded_db):
    res = run(seeded_db)
    ls = res["last_season"]
    assert ls is not None
    assert ls["season"] == OLD and ls["gw"] == 38
    old = next(r for r in ls["rows"] if r["code"] == 300)
    assert old["name"] == "OldTemplate"
    assert old["eo_top10k_pct"] == 120.0 and old["eo_elite_pct"] == 110.0
    # gws_covered enumerates both sides of the split, flagged live/stale
    flags = {(c["metric"], c["season"]): c["live"] for c in res["gws_covered"]}
    assert flags[("eo_predicted", SEASON)] is True
    assert flags[("eo_top10k", OLD)] is False
    assert OLD in res["metrics_note"] and "never merged" in res["metrics_note"]


def test_differentials_are_low_owned_high_xpts(seeded_db):
    res = run(seeded_db, diff_max_own=15.0)
    codes = [r["code"] for r in res["differentials"]]
    assert codes == [200]                       # 4% owned, 4.5 xPts
    assert res["differentials"][0]["xpts"] == 4.5
    assert 100 not in codes                     # 60% owned is nobody's differential


def test_no_elite_picks_degrades_honestly(seeded_db):
    res = run(seeded_db)
    assert all(r["elite_own_pct"] is None for r in res["rows"])
    assert "no elite picks" in res["cohort_note"].lower()


def _manager(entry_id: int, source: str) -> dict:
    return {"entry_id": entry_id, "player_name": f"M{entry_id}",
            "entry_name": f"T{entry_id}", "region": None, "years_active": None,
            "favourite_team_id": None, "started_event": 1, "source": source,
            "as_of": T}


def _plant_picks(db, managers: list[dict], picks: list[dict]) -> None:
    from fpl_edge.ingest.rivals.schema import migrate

    wh = Warehouse(db)
    migrate(wh)
    if managers:
        wh.append("dim_manager", pd.DataFrame(managers))
    wh.append("fact_manager_pick", pd.DataFrame(picks))
    wh.close()


# entry 1: owns Premium as captain; entry 2: owns both, no armband.
ELITE_PICKS = [
    {"entry_id": 1, "season": SEASON, "gw": 1, "element_id": 10, "slot": 1,
     "multiplier": 2, "is_captain": True, "is_vice_captain": False, "as_of": T},
    {"entry_id": 2, "season": SEASON, "gw": 1, "element_id": 10, "slot": 1,
     "multiplier": 1, "is_captain": False, "is_vice_captain": False, "as_of": T},
    {"entry_id": 2, "season": SEASON, "gw": 1, "element_id": 20, "slot": 2,
     "multiplier": 1, "is_captain": False, "is_vice_captain": False, "as_of": T},
]


def test_elite_picks_produce_a_real_cohort_column(seeded_db):
    _plant_picks(
        seeded_db,
        [_manager(1, "elite_list"), _manager(2, "snowball:1")],
        ELITE_PICKS,
    )

    res = run(seeded_db)
    by_code = {r["code"]: r for r in res["rows"]}
    assert by_code[100]["elite_own_pct"] == 100.0
    assert by_code[100]["elite_eo_pct"] == 150.0    # (2 + 1) / 2 managers
    assert by_code[200]["elite_own_pct"] == 50.0
    assert "2 crawled managers" in res["cohort_note"]
    assert "GW1" in res["cohort_note"]
    assert res["cohort"] == "elite" and res["cohort_n"] == 2
    assert res["cohort_gw"] == 1


def test_the_elite_denominator_excludes_the_top1k_sample(seeded_db):
    """B7/B8: the panel used to divide by EVERY crawled entry and call it elite.

    Entry 3 is a top-1k standings pick, not one of the named elite. Blending it
    in gave a denominator of 3 and an "elite" EO of 100% for a player the two
    elite managers actually rate at 150%. The cohort is now filtered, and the
    other cohort is reported beside it rather than merged into it.
    """
    _plant_picks(
        seeded_db,
        [_manager(1, "elite_list"), _manager(2, "snowball:1"),
         _manager(3, "top1k:2026-27:gw1:rank7")],
        ELITE_PICKS + [
            {"entry_id": 3, "season": SEASON, "gw": 1, "element_id": 20,
             "slot": 1, "multiplier": 1, "is_captain": False,
             "is_vice_captain": False, "as_of": T},
        ],
    )

    res = run(seeded_db)
    by_code = {r["code"]: r for r in res["rows"]}
    assert res["cohort_n"] == 2, "the top1k entry leaked into the elite denominator"
    assert by_code[100]["elite_eo_pct"] == 150.0    # (2 + 1) / 2, not / 3
    assert by_code[200]["elite_own_pct"] == 50.0    # 1 of 2, not 2 of 3
    assert "top1k n=1" in res["cohort_note"]

    top = run(seeded_db, cohort="top1k")
    top_by_code = {r["code"]: r for r in top["rows"]}
    assert top["cohort_n"] == 1
    assert top_by_code[200]["elite_own_pct"] == 100.0
    assert top_by_code[100]["elite_own_pct"] is None


def test_a_manager_in_both_pools_is_counted_once_as_top1k(seeded_db):
    """B8: two crawls, one manager, ONE cohort — precedence, not duplication."""
    _plant_picks(
        seeded_db,
        [_manager(1, "elite_list"), _manager(2, "snowball:1"),
         # entry 2 was ALSO seen by the standings sampler.
         {**_manager(2, "top1k:2026-27:gw1:rank7"),
          "as_of": T + pd.Timedelta(hours=1)}],
        ELITE_PICKS,
    )

    res = run(seeded_db)
    top = run(seeded_db, cohort="top1k")
    assert res["cohort_n"] == 1 and top["cohort_n"] == 1, (
        "an entry in both pools was counted in both denominators"
    )
    by_code = {r["code"]: r for r in res["rows"]}
    assert by_code[100]["elite_eo_pct"] == 200.0    # the one elite captains him
    assert by_code[200]["elite_own_pct"] is None    # only the top1k entry has him


def test_picks_without_a_manager_row_are_labelled_not_dropped(seeded_db):
    """A squad we hold and cannot classify is a crawl bug, not a row to lose."""
    _plant_picks(seeded_db, [], ELITE_PICKS)

    res = run(seeded_db)
    assert res["cohort_n"] is None
    assert "no elite picks" in res["cohort_note"].lower()
    assert "unclassified n=2" in res["cohort_note"]
    unc = run(seeded_db, cohort="unclassified")
    by_code = {r["code"]: r for r in unc["rows"]}
    assert unc["cohort_n"] == 2 and by_code[100]["elite_eo_pct"] == 150.0


def test_squad_coverage_marks_owned_and_missing(seeded_db, monkeypatch):
    class FakeRouter:
        def __init__(self, wh, *, season, entry_id):
            pass

        def _team_state(self):
            return SimpleNamespace(
                picks=[SimpleNamespace(code=100)],
                provenance=SimpleNamespace(name="MANUAL"),
            )

    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", FakeRouter)
    res = run(seeded_db, coverage=True)
    by_code = {r["code"]: r for r in res["rows"]}
    assert by_code[100]["in_squad"] is True
    assert by_code[200]["in_squad"] is False
    assert "MANUAL" in res["squad_note"]


def test_unreadable_squad_means_null_coverage_not_a_crash(seeded_db, monkeypatch):
    class ExplodingRouter:
        def __init__(self, *a, **k):
            raise ConnectionError("FPL API down")

    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", ExplodingRouter)
    res = run(seeded_db, coverage=True)
    assert all(r["in_squad"] is None for r in res["rows"])
    assert "unreadable" in res["squad_note"]


# ---------------------------------------------------------------------------
# The field ladder.
#
# The panel used to hand the web view one ownership column and one EO column
# and leave every interpretation to a hard-coded string in the JS. It now
# enumerates every field it can measure, with the denominator each percentage
# is a percentage OF, and hangs per-player measurements off `rows[].fields`.
# These tests pin the three ways that can go wrong: a measured metric that
# never reaches a row, a head-count share substituted for an EO, and a
# denominator (or a re-stamped gameweek) that is claimed rather than measured.
# ---------------------------------------------------------------------------


def _external(db, rows):
    """Insert (season, gw, code, metric, value) external-ownership rows."""
    wh = Warehouse(db)
    for season, gw, code, metric, value in rows:
        wh.sql(
            "INSERT INTO fact_external_ownership "
            "(provider, season, gw, code, metric, value, as_of) VALUES "
            "(?, ?, ?, ?, ?, ?, TIMESTAMPTZ '2026-08-01 12:00:00+00')",
            ["livefpl", season, gw, code, metric, value],
        )
    wh.close()


def _fields(res):
    return {f["key"]: f for f in res["fields"]}


def test_the_ladder_enumerates_a_field_per_measurable_population(seeded_db):
    _plant_picks(seeded_db, [_manager(1, "elite_list"), _manager(2, "snowball:1")],
                 ELITE_PICKS)
    res = run(seeded_db)
    f = _fields(res)

    assert f["global"]["role"] == "baseline" and f["global"]["measures"] == ["own"]
    assert f["eo_predicted"]["role"] == "baseline"
    assert f["eo_predicted"]["measures"] == ["eo"]
    assert f["cohort:elite"]["role"] == "field"
    assert set(f["cohort:elite"]["measures"]) == {"own", "eo"}
    # every field says, in words, what its percentages are percentages of
    assert all(x["denominator"].strip() for x in res["fields"])
    # and no field invents a manager count it cannot observe
    assert f["global"]["n"] is None, "claimed a count of every FPL entry"
    assert f["eo_predicted"]["n"] is None
    assert f["cohort:elite"]["n"] == res["cohort_n"] == 2


def test_external_metrics_past_eo_predicted_reach_the_rows(seeded_db):
    """eo_top10k/eo_elite went live for the current season and the panel
    computed them, then threw them away: the pivot named eo_predicted and
    nothing downstream ever saw the other two. A field the warehouse can
    measure must reach a row."""
    _external(seeded_db, [(SEASON, 1, 100, "eo_top10k", 1.10),
                          (SEASON, 1, 100, "eo_elite", 1.30)])
    res = run(seeded_db)
    f = _fields(res)
    assert "eo_top10k" in f and "eo_elite" in f
    assert f["eo_elite"]["provider"] == "livefpl"
    row = next(r for r in res["rows"] if r["code"] == 100)
    assert row["fields"]["eo_top10k"]["eo"] == 110.0
    assert row["fields"]["eo_elite"]["eo"] == 130.0
    # ...and the legacy single-metric key still means what it always meant
    assert row["eo_pred_pct"] == 95.0


def test_ownership_and_eo_are_never_substituted_for_each_other(seeded_db):
    """The units guard. `own` is a head count, `eo` is a sum of multipliers;
    a field that publishes one must leave the other absent, because the rank
    identity subtracts a multiplier from an EO and from nothing else."""
    _plant_picks(seeded_db, [_manager(1, "elite_list"), _manager(2, "snowball:1")],
                 ELITE_PICKS)
    res = run(seeded_db)
    row = next(r for r in res["rows"] if r["code"] == 100)

    assert row["fields"]["global"] == {"own": 60.0}, "an EO was invented for FPL's own %"
    assert "own" not in row["fields"]["eo_predicted"], "a head count was invented for a modelled EO"
    assert row["fields"]["eo_predicted"]["eo"] == 95.0
    # the crawled cohort is the one field that genuinely measures both
    coh = row["fields"]["cohort:elite"]
    assert coh["own"] == 100.0 and coh["eo"] == 150.0
    assert coh["owned_by"] == 2 and coh["captained_by"] == 1


def test_a_restamped_feed_is_reported_as_identical_not_as_a_fresh_gameweek(seeded_db):
    """LiveFPL republishes the settled top10k/elite series under the upcoming
    gw with byte-identical values. Captioning that as this week's forecast is
    a fabrication by labelling, so the duplication is MEASURED."""
    _external(seeded_db, [
        # identical across gw 1 and 2 -> a re-stamp
        (SEASON, 1, 100, "eo_top10k", 1.10), (SEASON, 2, 100, "eo_top10k", 1.10),
        (SEASON, 1, 200, "eo_top10k", 0.20), (SEASON, 2, 200, "eo_top10k", 0.20),
        # genuinely re-forecast -> not a re-stamp
        (SEASON, 1, 100, "eo_elite", 1.10), (SEASON, 2, 100, "eo_elite", 1.25),
    ])
    res = run(seeded_db)
    f = _fields(res)
    assert f["eo_top10k"]["gw"] == 2
    assert f["eo_top10k"]["same_values_as_gw"] == 1, (
        "a re-stamped gameweek was presented as a new one"
    )
    assert f["eo_elite"]["same_values_as_gw"] is None
    assert f["eo_predicted"]["same_values_as_gw"] is None


def test_cohort_composition_discloses_the_conflicted_members(seeded_db):
    """A cohort that is partly the owner's own mini-league is still usable;
    an undisclosed one is not. Tags overlap by construction, and the panel
    says so rather than letting the numbers look like a partition."""
    _plant_picks(
        seeded_db,
        [_manager(1, "elite_list"), {**_manager(2, "mini_league:76109"),
                                     "as_of": T + pd.Timedelta(hours=1)},
         _manager(2, "elite_list")],
        ELITE_PICKS,
    )
    res = run(seeded_db)
    comp = {c["tag"]: c for c in _fields(res)["cohort:elite"]["composition"]}
    assert comp["elite_list"]["n"] == 2
    assert comp["mini_league"]["n"] == 1
    assert "mini-league" in comp["mini_league"]["label"]
    # 2 + 1 tags over 2 managers: overlapping, and flagged as such
    assert _fields(res)["cohort:elite"]["n"] == 2
    assert _fields(res)["cohort:elite"]["overlaps"] is True


def test_your_multiplier_is_read_from_the_squad_never_inferred(seeded_db, monkeypatch):
    """`your_mult` is my side of the rank identity. It is reported only when
    the squad read actually carried one — a manually entered 15 has no armband,
    and a silent 1x there would be a fabricated number in a subtraction."""
    class RichRouter:
        def __init__(self, wh, *, season, entry_id):
            pass

        def _team_state(self):
            return SimpleNamespace(
                gw=7,
                picks=[SimpleNamespace(code=100, multiplier=2, is_captain=True,
                                       is_starter=True)],
                provenance=SimpleNamespace(name="PRIVATE_API"),
            )

    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", RichRouter)
    res = run(seeded_db, coverage=True)
    row = next(r for r in res["rows"] if r["code"] == 100)
    assert row["your_mult"] == 2 and row["your_role"] == "captain"
    assert res["squad"]["has_multipliers"] is True
    assert res["squad"]["gw"] == 7 and res["squad"]["captain"] == "Premium"
    # a player I do not own is a measured 0x, not an unknown
    assert next(r for r in res["rows"] if r["code"] == 200)["your_mult"] is None
    assert next(r for r in res["rows"] if r["code"] == 200)["in_squad"] is False


def test_a_squad_read_without_multipliers_says_so(seeded_db, monkeypatch):
    class BareRouter:
        def __init__(self, wh, *, season, entry_id):
            pass

        def _team_state(self):
            return SimpleNamespace(
                picks=[SimpleNamespace(code=100)],
                provenance=SimpleNamespace(name="MANUAL"),
            )

    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", BareRouter)
    res = run(seeded_db, coverage=True)
    row = next(r for r in res["rows"] if r["code"] == 100)
    assert row["in_squad"] is True
    assert row["your_mult"] is None and row["your_role"] is None, (
        "a multiplier was invented for a squad read that carries none"
    )
    assert res["squad"]["readable"] is True
    assert res["squad"]["has_multipliers"] is False


def test_an_unreadable_squad_leaves_the_whole_identity_unknown(seeded_db, monkeypatch):
    class ExplodingRouter:
        def __init__(self, *a, **k):
            raise ConnectionError("FPL API down")

    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", ExplodingRouter)
    res = run(seeded_db, coverage=True)
    assert res["squad"]["readable"] is False
    assert res["squad"]["has_multipliers"] is False
    assert all(r["your_mult"] is None and r["your_role"] is None
               for r in res["rows"])


# ---------------------------------------------------------------------------
# Selectable sub-cohorts.
#
# `cohort:elite` is ONE aggregate of managers found by six different crawls.
# Which of those crawls a reader is willing to be measured against is a
# judgement only the reader can make, so the sets are served separately and the
# field is the UNION of the chosen ones. The three ways that goes wrong, pinned
# here: a denominator that sums overlapping set sizes, a set whose provenance
# is known bad offered as if it were evidence, and a default that exists only
# in a schema where no reader can see it.
# ---------------------------------------------------------------------------

# entry 2 carries TWO tags (elite_list and winner) and is ONE manager: the
# overlap the union denominator must not double-count.
SEGMENT_MANAGERS = [
    _manager(1, "elite_list"),
    _manager(2, "elite_list"),
    # dim_manager's key is (entry_id, as_of), so a second tag for the same
    # entry needs a later stamp -- which is also how the crawl records it.
    {**_manager(2, "winner:2020/21"), "as_of": T + pd.Timedelta(hours=1)},
    _manager(3, "winner:2019/20"),
    _manager(4, "mini_league:76109"),
    _manager(5, "snowball:999"),
    _manager(6, "expert"),            # in the pool, no stored squad
]

# Premium (element 10) is the curated elite's captain; Diff (element 20) is
# held only by the mini-league entry and the snowball entry.
SEGMENT_PICKS = [
    {"entry_id": 1, "season": SEASON, "gw": 1, "element_id": 10, "slot": 1,
     "multiplier": 2, "is_captain": True, "is_vice_captain": False, "as_of": T},
    {"entry_id": 2, "season": SEASON, "gw": 1, "element_id": 10, "slot": 1,
     "multiplier": 1, "is_captain": False, "is_vice_captain": False, "as_of": T},
    {"entry_id": 3, "season": SEASON, "gw": 1, "element_id": 10, "slot": 1,
     "multiplier": 1, "is_captain": False, "is_vice_captain": False, "as_of": T},
    {"entry_id": 4, "season": SEASON, "gw": 1, "element_id": 20, "slot": 1,
     "multiplier": 1, "is_captain": False, "is_vice_captain": False, "as_of": T},
    {"entry_id": 5, "season": SEASON, "gw": 1, "element_id": 20, "slot": 1,
     "multiplier": 3, "is_captain": True, "is_vice_captain": False, "as_of": T},
]


@pytest.fixture()
def segmented_db(seeded_db):
    _plant_picks(seeded_db, SEGMENT_MANAGERS, SEGMENT_PICKS)
    return seeded_db


def _segments(res):
    return {s["key"]: s for s in res["segments"]}


def test_the_default_field_is_the_curated_elite_and_says_so(segmented_db):
    """The default must be visible in the payload, not implied by silence.

    A reader who cannot see which managers he is being compared against cannot
    judge the comparison, and "whatever the schema's default happens to be" is
    not visible to a reader.
    """
    res = run(segmented_db)
    sel = res["selection"]
    assert sel["default"] == ["elite_list", "winner", "elite_named"]
    assert sel["segments"] == ["elite_list", "winner", "elite_named"]
    assert sel["is_default"] is True
    # the owner's own mini-league opponents are NOT the default field
    assert "mini_league" not in sel["segments"]
    assert "snowball" not in sel["segments"]
    # entries 1, 2, 3 -- and entry 2 only once
    assert sel["n"] == 3
    assert res["fields"][-1]["key"] == "selected"


def test_the_union_denominator_is_distinct_managers_not_a_sum_of_set_sizes(
    segmented_db,
):
    """elite_list(2) + winner(2) + elite_named(0) is 4 memberships over 3
    managers. A denominator of 4 is a denominator nobody is in."""
    res = run(segmented_db)
    sel = res["selection"]
    assert sel["n"] == 3, "the double-tagged manager was counted twice"
    assert sel["n_sum_of_sets"] == 4
    assert sel["overlap"] == 1 and sel["overlaps"] is True
    assert "not 4" in sel["denominator"] and "262" not in sel["denominator"]

    row = next(r for r in res["rows"] if r["code"] == 100)
    # (2 + 1 + 1) / 3 managers = 133.3%, not / 4 = 100%
    assert row["fields"]["selected"]["eo"] == 133.3
    assert row["fields"]["selected"]["own"] == 100.0
    assert row["fields"]["selected"]["owned_by"] == 3


def test_selecting_segments_recomputes_the_field_over_their_union(segmented_db):
    """Different sets, different managers, different numbers -- and the
    denominator moves with them."""
    curated = run(segmented_db)
    mini = run(segmented_db, segments=["mini_league"])
    both = run(segmented_db, segments=["elite_list", "winner", "mini_league"])

    assert curated["selection"]["n"] == 3
    assert mini["selection"]["n"] == 1
    assert both["selection"]["n"] == 4

    # Premium is the curated elite's captain and no part of the mini-league
    assert next(r for r in curated["rows"] if r["code"] == 100)["fields"][
        "selected"]["eo"] == 133.3
    assert "selected" not in next(
        r for r in mini["rows"] if r["code"] == 100)["fields"]
    # Diff is the mini-league's only holding
    assert next(r for r in mini["rows"] if r["code"] == 200)["fields"][
        "selected"]["own"] == 100.0
    assert next(r for r in both["rows"] if r["code"] == 200)["fields"][
        "selected"]["own"] == 25.0        # 1 of 4


def test_the_snowball_set_is_offered_only_with_its_untrustworthy_flag(
    segmented_db,
):
    """PANEL_LEDGER 2026-08-27: the snowball pool is league-mates of stale seed
    IDs and 'must not be treated as an elite cohort in any skill, copying or EO
    analysis'. Hiding the checkbox teaches nobody; offering it unlabelled is
    worse. It is present, flagged, reasoned, and never in a default."""
    res = run(segmented_db)
    segs = _segments(res)
    snow = segs["snowball"]
    assert snow["trusted"] is False
    assert snow["in_default"] is False and snow["selected"] is False
    assert snow["untrusted_reason"] and "PANEL_LEDGER" in snow["untrusted_reason"]
    assert "unreproducible" in snow["untrusted_reason"]
    # every other set on offer is trustworthy, so the flag means something
    assert [k for k, s in segs.items() if not s["trusted"]] == ["snowball"]

    # selecting it anyway is allowed -- and disclosed at the top level
    used = run(segmented_db, segments=["elite_list", "snowball"])
    assert used["selection"]["untrusted_selected"] == ["snowball"]
    assert "UNTRUSTWORTHY" in used["selection"]["note"]
    assert "untrustworthy" in _fields(used)["selected"]["note"].lower()


def test_a_set_with_no_stored_squad_shows_its_pool_beside_its_zero(segmented_db):
    """The expert pool is real managers whose squads were never crawled. A
    checkbox reading 'experts (1)' that measures nobody is a lie of omission;
    n is what can be measured, n_pool is what exists."""
    segs = _segments(run(segmented_db))
    assert segs["expert"]["n"] == 0 and segs["expert"]["n_pool"] == 1
    res = run(segmented_db, segments=["expert"])
    assert res["selection"]["n"] is None
    assert "no denominator" in res["selection"]["denominator"]
    assert "selected" not in _fields(res)
    assert all("selected" not in r["fields"] for r in res["rows"])


def test_an_unknown_segment_is_reported_not_silently_dropped(segmented_db):
    """A typo that quietly narrows the field is how a reader ends up comparing
    himself against the wrong people and never finds out."""
    res = run(segmented_db, segments=["elite_list", "elite-list"])
    assert res["selection"]["segments"] == ["elite_list"]
    assert res["selection"]["unknown"] == ["elite-list"]
    assert "elite-list" in res["selection"]["note"]


def test_selecting_the_set_that_contains_you_is_disclosed(segmented_db,
                                                          monkeypatch):
    """If the owner is inside the field, his own transfer moves the
    denominator, and the what-if simulator's central assumption is false."""
    monkeypatch.setattr("fpl_edge.config.USER", SimpleNamespace(entry_id=4))
    res = run(segmented_db, segments=["mini_league"])
    assert res["selection"]["includes_you"] is True
    assert "inside this field" in res["selection"]["note"]
    assert any("your entry is inside" in s.lower()
               for s in res["whatif"]["not_safe_to_recompute"])

    outside = run(segmented_db)
    assert outside["selection"]["includes_you"] is False


def test_the_panels_segment_union_equals_the_semantic_layer_macro(segmented_db):
    """The panel computes the union inline (read copies are opened read-only,
    so a warehouse file older than the macro would not carry it). That is a
    second implementation of the ONE effective-ownership definition, so it is
    pinned to `sem_segment_ownership` column by column rather than trusted."""
    wh = Warehouse(segmented_db)          # writable open reapplies views.sql
    try:
        macro = wh.sql(
            "SELECT code, n_managers, owned_by, started_by, benched_by, "
            "captained_by, own_pct, eo_pct, captain_pct "
            "FROM sem_segment_ownership(now(), ['elite_list', 'winner', "
            "'elite_named']) WHERE season = ? AND gw = 1",
            [SEASON],
        )
    finally:
        wh.close()
    assert not macro.empty

    res = run(segmented_db)
    assert res["selection"]["n"] == int(macro.iloc[0]["n_managers"])
    for _, m in macro.iterrows():
        row = next(r for r in res["rows"] if r["code"] == int(m["code"]))
        got = row["fields"]["selected"]
        assert got["own"] == round(float(m["own_pct"]), 1)
        assert got["eo"] == round(float(m["eo_pct"]), 1)
        assert got["cap"] == round(float(m["captain_pct"]), 1)
        assert got["owned_by"] == int(m["owned_by"])
        assert got["started_by"] == int(m["started_by"])
        assert got["benched_by"] == int(m["benched_by"])
        assert got["captained_by"] == int(m["captained_by"])


def test_the_whole_pool_selected_equals_the_elite_cohort(segmented_db):
    """The segment vocabulary and the cohort vocabulary must describe the same
    managers. With no top1k crawl seeded, every tagged entry is 'elite', so
    selecting every segment must reproduce `cohort:elite` exactly."""
    every = [s["key"] for s in run(segmented_db)["segments"]]
    res = run(segmented_db, segments=every)
    assert res["selection"]["n"] == res["cohort_n"] == 5
    for row in res["rows"]:
        assert row["fields"].get("selected") == row["fields"].get("cohort:elite")


# ---------------------------------------------------------------------------
# Tool 1: the squad-vs-field diff.
# ---------------------------------------------------------------------------


class _RichRouter:
    """A squad read that carries real FPL multipliers."""

    def __init__(self, wh, *, season, entry_id):
        pass

    def _team_state(self):
        return SimpleNamespace(
            gw=1,
            picks=[SimpleNamespace(code=200, multiplier=2, is_captain=True,
                                   is_starter=True)],
            provenance=SimpleNamespace(name="PRIVATE_API"),
        )


def _diff(res):
    return {d["code"]: d for d in res["diff"]}


def test_a_player_i_own_that_the_field_does_not_is_still_a_diff_row(
    segmented_db, monkeypatch,
):
    """THE row this tool exists for. Diff (code 200) is captained by me and
    held by nobody in the curated elite. Dropping him because he is absent from
    the field's rows would hide the largest differential on the page."""
    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", _RichRouter)
    res = run(segmented_db, coverage=True)
    d = _diff(res)[200]
    assert d["in_squad"] is True and d["in_field_top"] is False
    # 0 of 3 curated managers is a MEASURED zero over a known denominator,
    # not a missing value -- so the edge is real and fully signed.
    assert d["field_own_pct"] == 0.0 and d["field_eo_pct"] == 0.0
    assert d["field_owned_by"] == 0
    assert d["your_mult"] == 2 and d["your_eo_pct"] == 200.0
    assert d["edge_eo_pct"] == 200.0
    assert d["note"] is None
    # and he sorts to the top: the biggest overweight the owner is running
    assert res["diff"][0]["code"] == 200


def test_the_diff_never_subtracts_a_head_count_from_an_eo(segmented_db,
                                                          monkeypatch):
    """`own` is a head count share, `eo` is a sum of multipliers. The rank
    identity subtracts a multiplier from an EO and from nothing else, and a
    previous version of this page mixed the two."""
    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", _RichRouter)
    res = run(segmented_db, coverage=True)
    prem = _diff(res)[100]
    # the field measures Premium at 100% owned and 133.3% EO -- different
    # numbers, so a substitution cannot hide
    assert prem["field_own_pct"] == 100.0 and prem["field_eo_pct"] == 133.3
    assert prem["in_squad"] is False
    assert prem["your_own_pct"] == 0.0 and prem["your_eo_pct"] == 0.0
    assert prem["edge_eo_pct"] == -133.3       # 0 - 133.3, multipliers only
    assert prem["edge_own_pct"] == -100.0      # 0 - 100, head counts only
    for d in res["diff"]:
        if d["edge_eo_pct"] is not None:
            assert d["edge_eo_pct"] == round(
                d["your_eo_pct"] - d["field_eo_pct"], 1)
        if d["edge_own_pct"] is not None:
            assert d["edge_own_pct"] == round(
                d["your_own_pct"] - d["field_own_pct"], 1)


def test_a_squad_read_without_multipliers_keeps_the_own_gap_and_nulls_the_eo(
    segmented_db, monkeypatch,
):
    """A manually entered 15 has no armband. Ownership can still be compared;
    EO cannot, and a silent 1x there would be a fabricated number inside a
    subtraction."""
    class BareRouter:
        def __init__(self, wh, *, season, entry_id):
            pass

        def _team_state(self):
            return SimpleNamespace(
                picks=[SimpleNamespace(code=200)],
                provenance=SimpleNamespace(name="MANUAL"))

    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", BareRouter)
    d = _diff(run(segmented_db, coverage=True))[200]
    assert d["in_squad"] is True
    assert d["your_own_pct"] == 100.0 and d["edge_own_pct"] == 100.0
    assert d["your_eo_pct"] is None and d["edge_eo_pct"] is None
    assert "no multipliers" in d["note"]


def test_an_unreadable_squad_leaves_the_diff_unknown_never_zero(segmented_db,
                                                                monkeypatch):
    class ExplodingRouter:
        def __init__(self, *a, **k):
            raise ConnectionError("FPL API down")

    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", ExplodingRouter)
    res = run(segmented_db, coverage=True)
    for d in res["diff"]:
        assert d["in_squad"] is None
        assert d["your_eo_pct"] is None and d["your_own_pct"] is None
        assert d["edge_eo_pct"] is None and d["edge_own_pct"] is None
        assert "unknown" in d["note"]
        # the FIELD is still measured -- only my side is missing
        assert d["field_eo_pct"] is not None


def test_a_gameweek_mismatch_between_my_squad_and_the_field_is_disclosed(
    segmented_db, monkeypatch,
):
    """My GW2 squad against the field's GW1 squads is not purely a difference
    of opinion, and the panel must not let that read as one."""
    class NextWeekRouter(_RichRouter):
        def _team_state(self):
            return SimpleNamespace(
                gw=2,
                picks=[SimpleNamespace(code=200, multiplier=1,
                                       is_captain=False, is_starter=True)],
                provenance=SimpleNamespace(name="PUBLIC_PICKS"))

    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", NextWeekRouter)
    res = run(segmented_db, coverage=True)
    assert "GW2" in res["selection"]["note"] and "GW1" in res["selection"]["note"]
    assert "two different" in res["selection"]["note"]


# ---------------------------------------------------------------------------
# Tool 2: the what-if exposure simulator.
# ---------------------------------------------------------------------------


def test_the_whatif_block_covers_every_player_so_a_swap_needs_no_round_trip(
    segmented_db, monkeypatch,
):
    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", _RichRouter)
    res = run(segmented_db, coverage=True)
    w = res["whatif"]
    codes = {p["code"] for p in w["players"]}
    assert codes == {100, 200}, "a swap-in candidate would need a refetch"
    assert w["n"] == 3 and w["gw"] == 1 and w["field"] == "selected"
    # a player the field owns 0 times still carries measured zeroes
    diff_player = next(p for p in w["players"] if p["code"] == 200)
    assert diff_player["field_eo_pct"] == 0.0
    assert diff_player["field_own_pct"] == 0.0
    assert diff_player["your_mult"] == 2 and diff_player["in_squad"] is True


def test_the_whatif_block_names_what_cannot_be_recomputed_client_side(
    segmented_db,
):
    """The honest half. A UI that recomputes the FIELD after a swap is
    inventing a measurement over managers it cannot see."""
    w = run(segmented_db)["whatif"]
    assert w["safe_to_recompute"] and w["not_safe_to_recompute"]
    joined = " ".join(w["not_safe_to_recompute"]).lower()
    assert "segments" in joined, "a re-selection must be named as a refetch"
    assert "gameweek" in joined
    safe = " ".join(w["safe_to_recompute"]).lower()
    assert "multiplier" in safe and "net exposure" in safe


def test_no_selection_means_null_field_values_not_zeroes(segmented_db):
    """Nothing selected is not 'a field where nobody owns anybody'."""
    res = run(segmented_db, segments=[])
    assert res["selection"]["n"] is None
    assert "no field" in res["selection"]["note"].lower()
    for p in res["whatif"]["players"]:
        assert p["field_eo_pct"] is None and p["field_own_pct"] is None
    for d in res["diff"]:
        assert d["field_eo_pct"] is None and d["edge_eo_pct"] is None
        assert "nothing to diff against" in d["note"]


# ---------------------------------------------------------------------------
# Tool 3: ownership momentum.
# ---------------------------------------------------------------------------


def test_one_gameweek_is_not_a_trend(segmented_db):
    """Only GW1 squads exist -- a squad becomes public at its deadline. The
    shape ships so the UI can bind to it; the series does not, because a
    single point rendered as a line reads as 'flat', which is a claim."""
    m = run(segmented_db)["momentum"]
    assert m["available"] is False
    assert m["series"] == []
    assert m["gws"] == [1]
    assert m["min_gws_for_a_trend"] == 2
    assert "not a trend" in m["reason"]
    assert m["next_gw"] == 1 or m["next_gw"] is None


def test_momentum_lights_up_when_a_second_gameweek_lands(segmented_db):
    """The same shape, populated, the moment GW2 squads are stored."""
    _plant_picks(segmented_db, [], [
        {"entry_id": 1, "season": SEASON, "gw": 2, "element_id": 10, "slot": 1,
         "multiplier": 1, "is_captain": False, "is_vice_captain": False,
         "as_of": T},
        {"entry_id": 2, "season": SEASON, "gw": 2, "element_id": 20, "slot": 1,
         "multiplier": 2, "is_captain": True, "is_vice_captain": False,
         "as_of": T},
        {"entry_id": 3, "season": SEASON, "gw": 2, "element_id": 20, "slot": 1,
         "multiplier": 1, "is_captain": False, "is_vice_captain": False,
         "as_of": T},
    ])
    m = run(segmented_db)["momentum"]
    assert m["available"] is True
    assert m["gws"] == [1, 2]
    series = {s["code"]: s for s in m["series"]}
    prem = [p for p in series[100]["points"]]
    assert [p["gw"] for p in prem] == [1, 2]
    assert prem[0]["eo_pct"] == 133.3            # 3 managers, one captain
    assert prem[1]["eo_pct"] == 33.3             # 1 of 3 starts him
    # every point states the denominator it is a share of
    assert all(p["n_managers"] == 3 for p in prem)
    # a player nobody held in a gameweek is a measured 0, not a gap
    diff_pts = series[200]["points"]
    assert diff_pts[0]["eo_pct"] == 0.0 and diff_pts[1]["eo_pct"] == 100.0


# ---------------------------------------------------------------------------
# Cohort-vs-cohort compare: no second path needed.
# ---------------------------------------------------------------------------


def test_two_fields_for_the_same_player_come_back_in_one_call(segmented_db):
    """The `fields` ladder already answers 'compare cohort A with cohort B':
    every row carries every field's measurement of that player at one instant.
    A second endpoint per cohort would let the two halves of a comparison drift
    to different as_of instants, which is the bug it would exist to cause."""
    _plant_picks(segmented_db, [_manager(7, "top1k:2026-27:gw1:rank7")], [
        {"entry_id": 7, "season": SEASON, "gw": 1, "element_id": 20, "slot": 1,
         "multiplier": 1, "is_captain": False, "is_vice_captain": False,
         "as_of": T},
    ])
    res = run(segmented_db)
    keys = {f["key"] for f in res["fields"]}
    assert {"global", "eo_predicted", "cohort:elite", "cohort:top1k",
            "selected"} <= keys
    prem = next(r for r in res["rows"] if r["code"] == 100)
    assert prem["fields"]["cohort:elite"]["eo"] is not None
    assert prem["fields"]["selected"]["eo"] is not None
    assert prem["fields"]["global"]["own"] is not None
    diff = next(r for r in res["rows"] if r["code"] == 200)
    assert diff["fields"]["cohort:top1k"]["own"] == 100.0
    assert diff["fields"]["cohort:elite"]["own"] == 40.0     # 2 of 5

class TestAPreDeadlineSquadStillHasAMultiplier:
    """Before a deadline the picks payload carries no multiplier at all.

    That used to null the EO side of the rank identity for every row on the
    page -- the tab's headline measure went blank on precisely the day it is
    most wanted. The multiplier is not guessed: it is derived from the role the
    read DID carry, by the scoring rule (bench 0, start 1, captain 2, or 3 when
    chips_used reports a triple captain for this gameweek).
    """

    @staticmethod
    def _roles(picks, chips=(), gw=2):
        from fpl_edge.platform.scripts import ownership as own

        class _P:
            def __init__(self, code, cap, starter):
                self.code, self.is_captain, self.is_starter = code, cap, starter

        class _S:
            picks = None
            chips_used = ()
            gw = 2
            provenance = "PUBLIC_PICKS"

        st = _S()
        st.picks = [_P(*x) for x in picks]
        st.chips_used = chips
        st.gw = gw

        class _R:
            def __init__(self, *a, **k): pass
            def _team_state(self): return st

        import fpl_edge.interfaces.qa as qa
        old = qa.QuestionRouter
        qa.QuestionRouter = _R
        try:
            return own._squad_state(None, "2026-27")
        finally:
            qa.QuestionRouter = old

    def test_a_role_without_a_multiplier_still_yields_one(self):
        roles, meta = self._roles([(1, True, True), (2, False, True), (3, False, False)])
        assert roles[1]["mult"] == 2, "the captain scores double"
        assert roles[2]["mult"] == 1, "a starter scores once"
        assert roles[3]["mult"] == 0, "a benched player scores nothing"
        assert meta["has_multipliers"] is True
        assert meta["multipliers_read"] is False, "nothing was read; it was derived"
        assert meta["multipliers_derived"] is True

    def test_a_triple_captain_is_read_not_assumed(self):
        class _Chip:
            value = "3xc"
        roles, meta = self._roles(
            [(1, True, True), (2, False, True)], chips=((_Chip(), 2),), gw=2)
        assert roles[1]["mult"] == 3, "triple captain scores treble, and it is readable"
        assert meta["captain_multiplier_certain"] is True

    def test_a_chip_from_another_gameweek_does_not_triple_this_captain(self):
        class _Chip:
            value = "3xc"
        roles, _ = self._roles(
            [(1, True, True)], chips=((_Chip(), 1),), gw=2)
        assert roles[1]["mult"] == 2, "the chip was played in GW1, not this week"

    def test_a_squad_with_no_roles_at_all_derives_nothing(self):
        # A manually entered 15 has no armband and no bench order. Derived from
        # nothing is nothing -- never a silent 1x.
        roles, meta = self._roles([(1, False, None), (2, False, None)])
        assert all(r["mult"] is None for r in roles.values())
        assert meta["has_multipliers"] is False
