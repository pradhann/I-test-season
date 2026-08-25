"""The projection calibration loop's honesty properties.

What is pinned here, in order of how expensive a silent break would be:

* **Point in time** -- a projection fetched after the deadline is never
  scored. (Verified to fail when the filter is handed a post-deadline
  instant: see ``test_the_pit_filter_is_load_bearing``.)
* **The n_obs floor** -- a provider below the floor is measured but not
  weighted, with the reason written into ``holdout``.
* **Idempotency** -- re-scoring a scored gameweek measures nothing twice;
  refitting unchanged scores replaces the fit rather than stacking copies.
* **The earned=false paths** -- p_appear-only providers and providers with no
  pre-deadline claims get explicit zero-weight rows, never silence.
* **Zero-fill** -- a projected player whose team played but who never
  featured counts as an actual 0, so over-projecting fringe players costs.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.eval.projection_scoring import fit_weights, run, score_gameweek
from fpl_edge.ingest.projections.store import ProjectionStore
from fpl_edge.store import Warehouse

UTC = dt.UTC
SEASON = "2026-27"


def T(day: int, hour: int = 12) -> dt.datetime:
    return dt.datetime(2026, 8, day, hour, tzinfo=UTC)


DEADLINE = T(21, 17)      # GW1 deadline
KICKOFF = T(21, 19)
SETTLED = T(22, 8)        # points finalisation
NOW = T(23, 9)            # when the scoring job runs

#: (code, element_id, name, position, team, actual_points, minutes).
#: Player 3 has NO fact_player_fixture row: an unused substitute whose team
#: played -- the zero-fill case.
PLAYERS = [
    (1, 11, "Keeper", 1, 43, 6, 90),
    (2, 12, "Back", 2, 43, 2, 90),
    (3, 13, "Benched", 3, 3, 0, 0),
    (4, 14, "Striker", 4, 3, 13, 90),
]


@pytest.fixture()
def wh(tmp_path) -> Warehouse:
    w = Warehouse(tmp_path / "cal.duckdb")
    w.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 1, "deadline_utc": DEADLINE,
         "is_finished": False, "as_of": T(1)},
    ]))
    w.append("dim_team", pd.DataFrame([
        {"season": SEASON, "team_code": 43, "team_id": 13, "name": "Man City",
         "short_name": "MCI", "as_of": T(1)},
        {"season": SEASON, "team_code": 3, "team_id": 1, "name": "Arsenal",
         "short_name": "ARS", "as_of": T(1)},
    ]))
    w.append("dim_player", pd.DataFrame([
        {"season": SEASON, "code": code, "element_id": el, "web_name": name,
         "first_name": None, "second_name": None, "position": pos,
         "team_code": team, "as_of": T(1)}
        for code, el, name, pos, team, _, _ in PLAYERS
    ]))
    w.append("fact_fixture", pd.DataFrame([
        {"season": SEASON, "fixture_id": 9, "gw": 1, "kickoff_utc": KICKOFF,
         "home_team_code": 43, "away_team_code": 3, "finished": True,
         "home_score": 2, "away_score": 1, "as_of": SETTLED},
    ]))
    w.append("fact_player_fixture", pd.DataFrame([
        {"season": SEASON, "code": code, "fixture_id": 9, "gw": 1,
         "minutes": minutes, "total_points": pts, "as_of": SETTLED}
        for code, _, _, _, _, pts, minutes in PLAYERS if minutes > 0
    ]))
    ProjectionStore(w)
    return w


def _proj(wh: Warehouse, provider: str, *, offset: float | None,
          p_appear: dict[int, float] | None = None,
          as_of: dt.datetime | None = None) -> None:
    """Append one full-coverage projection set fetched at ``as_of``.

    ``offset`` is added to each player's true outcome, so a provider's MAE
    equals |offset| exactly; ``offset=None`` publishes no xp at all.
    ``as_of`` defaults to a pre-deadline fetch (T(20)).
    """
    as_of = as_of or T(20)
    rows = []
    for code, _, _, _, _, pts, _ in PLAYERS:
        rows.append({
            "provider": provider, "season": SEASON, "gw": 1, "code": code,
            "xp": None if offset is None else float(pts) + offset,
            "xp_if_appears": None,
            "p_appear": (p_appear or {}).get(code),
            "as_of": as_of,
        })
    ProjectionStore(wh).append("fact_projection", pd.DataFrame(rows))


def _scores(wh: Warehouse) -> pd.DataFrame:
    return wh.sql("SELECT * FROM fact_projection_score ORDER BY provider, scope, metric")


# -- point-in-time discipline ------------------------------------------------


def test_a_projection_fetched_after_the_deadline_is_never_scored(wh) -> None:
    _proj(wh, "good", offset=1.0)
    _proj(wh, "late", offset=0.0, as_of=T(21, 18))     # post-deadline only
    report = score_gameweek(wh, SEASON, 1, now=NOW)
    assert report["pending"] is None
    providers = set(_scores(wh)["provider"])
    assert "good" in providers
    assert "late" not in providers, (
        "a post-deadline fetch was scored as a pre-deadline claim"
    )
    assert "late" not in {r["provider"] for r in report["scored"]}


def test_the_last_pre_deadline_fetch_wins_not_a_post_deadline_revision(wh) -> None:
    """A provider that revises after the deadline is scored on what it held
    AT the deadline. If the PIT filter ever slipped to 'latest fetch', the
    post-deadline perfect revision would score MAE 0 instead of 2."""
    _proj(wh, "reviser", offset=2.0, as_of=T(20))
    _proj(wh, "reviser", offset=0.0, as_of=T(21, 18))  # perfect, but too late
    score_gameweek(wh, SEASON, 1, now=NOW)
    row = _scores(wh)
    mae = row[(row["provider"] == "reviser") & (row["scope"] == "overall")
              & (row["metric"] == "mae")].iloc[0]
    assert float(mae["value"]) == pytest.approx(2.0)


def test_the_pit_filter_is_load_bearing(wh) -> None:
    """Break the filter (read as-of a post-kickoff instant instead of the
    deadline) and the 'late' provider becomes visible -- proving the deadline
    read in score_gameweek is what excludes it, not coincidence."""
    _proj(wh, "late", offset=0.0, as_of=T(21, 18))
    store = ProjectionStore(wh)
    at_deadline = store.as_of("fact_projection", DEADLINE,
                              where="season = ? AND gw = ?", params=[SEASON, 1])
    broken = store.as_of("fact_projection", T(22),
                         where="season = ? AND gw = ?", params=[SEASON, 1])
    assert at_deadline.empty, "the deadline read leaked a post-deadline fetch"
    assert not broken.empty, (
        "the mutation check is vacuous: nothing was there to leak"
    )


# -- settlement gating and zero-fill -----------------------------------------


def test_an_unsettled_gameweek_reports_pending_and_writes_nothing(tmp_path) -> None:
    w = Warehouse(tmp_path / "pend.duckdb")
    w.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 1, "deadline_utc": DEADLINE,
         "is_finished": False, "as_of": T(1)},
    ]))
    w.append("fact_fixture", pd.DataFrame([
        {"season": SEASON, "fixture_id": 9, "gw": 1, "kickoff_utc": KICKOFF,
         "home_team_code": 43, "away_team_code": 3, "finished": False,
         "home_score": None, "away_score": None, "as_of": T(1)},
    ]))
    ProjectionStore(w)
    report = score_gameweek(w, SEASON, 1, now=NOW)
    assert report["pending"] is not None and "not finished" in report["pending"]
    assert report["rows_written"] == 0
    assert _scores(w).empty


def test_run_is_honest_before_any_settlement(tmp_path) -> None:
    w = Warehouse(tmp_path / "empty.duckdb")
    report = run(w, SEASON, now=NOW)
    assert report["pending"] is not None
    assert report["fit"] is None
    assert w.sql("SELECT count(*) n FROM projection_weight").iloc[0]["n"] == 0


def test_a_projected_player_who_never_featured_counts_as_zero(wh) -> None:
    """Player 3 (0 minutes, no fixture row) must be in n_obs with actual 0.
    'perfect' nails everyone except him (projects 3.0 for a 0) -- MAE 0.75."""
    _proj(wh, "perfect", offset=0.0)
    ProjectionStore(wh).append("fact_projection", pd.DataFrame([{
        "provider": "fringe", "season": SEASON, "gw": 1, "code": 3,
        "xp": 3.0, "xp_if_appears": None, "p_appear": None, "as_of": T(20),
    }]))
    score_gameweek(wh, SEASON, 1, now=NOW)
    rows = _scores(wh)
    fringe = rows[(rows["provider"] == "fringe") & (rows["scope"] == "overall")
                  & (rows["metric"] == "mae")].iloc[0]
    assert int(fringe["n_obs"]) == 1
    assert float(fringe["value"]) == pytest.approx(3.0), (
        "the benched player's actual 0 was not counted against the projection"
    )


def test_per_position_scores_land_beside_overall(wh) -> None:
    _proj(wh, "good", offset=1.0)
    score_gameweek(wh, SEASON, 1, now=NOW)
    rows = _scores(wh)
    scopes = set(rows[rows["provider"] == "good"]["scope"])
    assert {"overall", "pos:GKP", "pos:DEF", "pos:MID", "pos:FWD"} <= scopes
    gkp = rows[(rows["provider"] == "good") & (rows["scope"] == "pos:GKP")
               & (rows["metric"] == "mae")].iloc[0]
    assert int(gkp["n_obs"]) == 1 and float(gkp["value"]) == pytest.approx(1.0)


def test_p_appear_is_brier_scored_against_any_minutes(wh) -> None:
    # Player 3 sat out (outcome 0), the rest played (outcome 1).
    _proj(wh, "minutes_guy", offset=None,
          p_appear={1: 1.0, 2: 1.0, 3: 0.0, 4: 1.0})
    score_gameweek(wh, SEASON, 1, now=NOW)
    rows = _scores(wh)
    brier = rows[(rows["provider"] == "minutes_guy")
                 & (rows["scope"] == "p_appear")].iloc[0]
    assert brier["metric"] == "brier"
    assert float(brier["value"]) == pytest.approx(0.0)
    assert int(brier["n_obs"]) == 4


# -- idempotency -------------------------------------------------------------


def test_rescoring_a_scored_gameweek_measures_nothing_twice(wh) -> None:
    _proj(wh, "good", offset=1.0)
    first = score_gameweek(wh, SEASON, 1, now=NOW)
    n_after_first = len(_scores(wh))
    second = score_gameweek(wh, SEASON, 1, now=T(23, 10))
    assert first["rows_written"] == n_after_first > 0
    assert second["rows_written"] == 0
    assert "good" in second["skipped"]
    assert len(_scores(wh)) == n_after_first


def test_refitting_unchanged_scores_replaces_the_fit(wh) -> None:
    _proj(wh, "good", offset=1.0)
    score_gameweek(wh, SEASON, 1, now=NOW)
    fit1 = fit_weights(wh, SEASON, n_obs_floor=1, now=T(23, 10))
    fit2 = fit_weights(wh, SEASON, n_obs_floor=1, now=T(23, 11))
    assert fit1["fit_id"] == fit2["fit_id"] == f"{SEASON}:invmse:thru-gw1"
    stored = wh.sql("SELECT count(*) n FROM projection_weight WHERE provider = 'good'")
    assert int(stored.iloc[0]["n"]) == 1, "refit stacked duplicate weight rows"


# -- weight fitting ----------------------------------------------------------


def test_weights_are_inverse_mse_and_normalised(wh) -> None:
    _proj(wh, "good", offset=1.0)    # mse 1
    _proj(wh, "bad", offset=3.0)     # mse 9
    score_gameweek(wh, SEASON, 1, now=NOW)
    fit = fit_weights(wh, SEASON, n_obs_floor=1, now=T(23, 10))
    w = {r["provider"]: r for r in fit["weights"]}
    assert w["good"]["earned"] and w["bad"]["earned"]
    assert w["good"]["weight"] == pytest.approx(0.9)
    assert w["bad"]["weight"] == pytest.approx(0.1)
    assert w["good"]["weight"] + w["bad"]["weight"] == pytest.approx(1.0)
    assert w["good"]["loss"] == pytest.approx(1.0)
    assert w["bad"]["loss"] == pytest.approx(9.0)
    # Baseline: consensus mean sits offset 2 from truth -> mse 4, both rows.
    assert w["good"]["baseline_loss"] == pytest.approx(4.0)
    assert w["good"]["n_obs"] == 4


def test_the_n_obs_floor_blocks_earning_and_says_so(wh) -> None:
    _proj(wh, "good", offset=1.0)
    score_gameweek(wh, SEASON, 1, now=NOW)
    fit = fit_weights(wh, SEASON, now=T(23, 10))     # default floor = 200
    stored = wh.sql(
        "SELECT * FROM projection_weight WHERE provider = 'good'"
    ).iloc[0]
    assert not bool(stored["earned"]) and float(stored["weight"]) == 0.0
    assert "floor" in stored["holdout"] and "4 < 200" in stored["holdout"]
    # Measured but unearned: the evidence still travels with the zero.
    assert float(stored["loss"]) == pytest.approx(1.0)
    assert int(stored["n_obs"]) == 4
    assert fit["fit_id"] is not None


def test_a_p_appear_only_provider_gets_a_reason_not_a_weight(wh) -> None:
    _proj(wh, "good", offset=1.0)
    _proj(wh, "injuries", offset=None, p_appear={1: 0.75})
    score_gameweek(wh, SEASON, 1, now=NOW)
    fit_weights(wh, SEASON, n_obs_floor=1, now=T(23, 10))
    stored = wh.sql(
        "SELECT * FROM projection_weight WHERE provider = 'injuries'"
    ).iloc[0]
    assert not bool(stored["earned"]) and float(stored["weight"]) == 0.0
    assert "p_appear only" in stored["holdout"]
    assert pd.isna(stored["loss"])


def test_a_provider_with_no_pre_deadline_claim_is_recorded_as_unmeasured(wh) -> None:
    _proj(wh, "good", offset=1.0)
    _proj(wh, "late", offset=0.0, as_of=T(21, 18))
    score_gameweek(wh, SEASON, 1, now=NOW)
    fit_weights(wh, SEASON, n_obs_floor=1, now=T(23, 10))
    stored = wh.sql(
        "SELECT * FROM projection_weight WHERE provider = 'late'"
    ).iloc[0]
    assert not bool(stored["earned"]) and float(stored["weight"]) == 0.0
    assert "no pre-deadline" in stored["holdout"]


# -- the read surface --------------------------------------------------------


def test_sem_projection_weights_serves_evidence_with_pit_discipline(wh) -> None:
    _proj(wh, "good", offset=1.0)
    _proj(wh, "bad", offset=3.0)
    score_gameweek(wh, SEASON, 1, now=NOW)
    fit_weights(wh, SEASON, n_obs_floor=1, now=T(23, 10))

    before = wh.sql("SELECT * FROM sem_projection_weights(TIMESTAMPTZ '2026-08-23 09:30:00+00')")
    assert before.empty, "a fit was visible before it was written"

    after = wh.sql("SELECT * FROM sem_projection_weights(TIMESTAMPTZ '2026-08-23 11:00:00+00')")
    assert list(after["provider"])[:1] == ["good"], "not ordered by weight"
    top = after.iloc[0]
    assert float(top["weight"]) == pytest.approx(0.9)
    assert float(top["loss"]) == pytest.approx(1.0)
    assert top["loss_metric"] == "mse"
    assert int(top["n_obs"]) == 4 and bool(top["earned"])
    assert int(top["track_record_gws"]) == 1, (
        "the macro must say how deep the track record is"
    )


def test_run_scores_settles_and_fits_in_one_pass(wh) -> None:
    _proj(wh, "good", offset=1.0)
    report = run(wh, SEASON, now=NOW)
    assert report["pending"] is None
    assert report["scoring"][0]["scored"][0]["provider"] == "good"
    assert report["fit"]["fit_id"] == f"{SEASON}:invmse:thru-gw1"
