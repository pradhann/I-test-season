"""The ``idea_review`` panel: the probes survive, and zero ideas says so.

``tests/unit/test_ideas_bias.py`` pins the statistics. This file pins the panel
that serves them, which can break in two ways the statistics test would not
notice.

``test_every_probe_survives_with_its_statistics_and_its_own_verdict`` is the
first. A payload that carried only the significant probes, or only the
observed rate without n and the adjusted p-value, would read as a stronger
claim than the review makes. Each probe writes its own verdict sentence, which
says "not enough evidence" and "no test possible" where those apply, and it
travels whole.

``test_the_caveats_travel_verbatim`` is the second. A caveat saying the Brier
score below measures a price-rank prior rather than a points model cannot be
summarised without becoming the claim it exists to prevent.

``test_a_season_with_no_ideas_is_an_empty_with_the_reason`` is rule 9.

The history is planted through the real inbox by
``fpl_edge.interfaces.testing``, so parsing, context capture and the verdict on
every idea are exercised alongside the panel.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.interfaces.bias import MIN_OBSERVATIONS
from fpl_edge.interfaces.inbox import IdeaInbox
from fpl_edge.interfaces.testing import SEASON, plant_ideas, seed_warehouse
from fpl_edge.interfaces.tracking import track
from fpl_edge.platform import scripts  # noqa: F401 - registers the scripts
from fpl_edge.platform.registry import ParamsInvalid, registered, run_script
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC

#: Mid-September 2026: four gameweeks are in the books, so form, venue and
#: haul context all exist, matching test_ideas_bias.py.
PLANT_AT = dt.datetime(2026, 9, 15, 20, 0, tzinfo=UTC)
SETTLE_AT = dt.datetime(2026, 12, 20, 12, 0, tzinfo=UTC)

#: The four probes the review runs, named here so a probe that disappears from
#: the payload fails this file rather than going quiet.
PROBES = ("form_chasing", "home_bias", "recency", "club_affinity")


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("idea_review") / "planted.duckdb"
    wh = seed_warehouse(path, n_gws=16, finished_gws=14)
    inbox = IdeaInbox(wh)
    planted = plant_ideas(inbox, n=40, when=PLANT_AT, biased=True)
    assert planted.submitted >= MIN_OBSERVATIONS
    track(wh, season=SEASON, now=SETTLE_AT)
    wh.close()
    return path


@pytest.fixture(scope="module")
def result(db):
    run = run_script("idea_review", {"season": SEASON}, db=db)
    assert "empty" not in run.result, run.result
    return run.result


def test_the_panel_is_registered():
    assert "idea_review" in registered()


def test_every_probe_survives_with_its_statistics_and_its_own_verdict(result):
    """THE test. Four probes, each carrying what makes it readable."""
    names = [f["name"] for f in result["findings"]]
    for probe in PROBES:
        assert probe in names, names
    for finding in result["findings"]:
        assert finding["question"]
        assert isinstance(finding["n"], int)
        assert finding["verdict"]
        assert isinstance(finding["has_evidence"], bool)
        assert isinstance(finding["significant"], bool)
        # Significance is never claimed without the evidence that supports it.
        if finding["significant"]:
            assert finding["has_evidence"]
            assert finding["p_adjusted"] is not None


def test_a_probe_below_the_floor_says_so_rather_than_reporting_a_number(result):
    assert result["min_observations"] == MIN_OBSERVATIONS
    for finding in result["findings"]:
        if finding["n"] < MIN_OBSERVATIONS and finding["observed"] is not None:
            assert "NOT ENOUGH EVIDENCE" in finding["verdict"]
            assert finding["has_evidence"] is False


def test_the_scoreboard_splits_acted_from_skipped(result):
    """The split the whole apparatus exists for."""
    board = result["scoreboard"]
    assert board["n_total"] >= MIN_OBSERVATIONS
    assert board["acted_n"] + board["unacted_n"] <= board["n_resolved"]
    for key in ("hit_rate", "acted_hit_rate", "unacted_hit_rate", "brier",
                "baseline_brier"):
        assert key in board


def test_the_caveats_travel_verbatim(result):
    """A caveat is carried, not compressed, and it is a list of whole strings."""
    assert isinstance(result["caveats"], list)
    for caveat in result["caveats"]:
        assert isinstance(caveat, str) and caveat.strip()


def test_the_ideas_are_capped_and_the_cap_is_explained(db):
    result = run_script(
        "idea_review", {"season": SEASON, "limit": 5}, db=db,
    ).result
    assert len(result["ideas"]) <= 5
    if result["scoreboard"]["n_total"] > 5:
        assert "of" in result["ideas_reason"]
        assert "computed over all of them" in result["ideas_reason"]


def test_the_ideas_can_be_left_out_and_the_reason_says_so(db):
    result = run_script(
        "idea_review", {"season": SEASON, "include_ideas": False}, db=db,
    ).result
    assert result["ideas"] == []
    assert "not asked for" in result["ideas_reason"]
    assert result["findings"]


def test_a_season_with_no_ideas_is_an_empty_with_the_reason(db):
    result = run_script("idea_review", {"season": "1999-00"}, db=db).result
    assert result["empty"] is True
    assert "1999-00" in result["reason"]
    assert "bias probes" in result["reason"]


def test_a_warehouse_with_no_registry_says_that_instead(tmp_path):
    """A read copy is read-only, so the registry must not try to migrate."""
    path = tmp_path / "bare.duckdb"
    Warehouse(path).close()
    result = run_script("idea_review", {}, db=path).result
    assert result["empty"] is True
    assert "never been created" in result["reason"]


def test_the_params_schema_rejects_an_entry_id(db):
    with pytest.raises(ParamsInvalid):
        run_script("idea_review", {"entry_id": 4490171}, db=db)
