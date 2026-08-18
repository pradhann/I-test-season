"""Bonus double counting, and 2024-25 manager elements leaking into 2026-27.

Hunt list items 6 and 7.

Bonus: ``fact_player_fixture`` carries BOTH ``bonus`` and ``total_points``, and
``total_points`` already includes the bonus. A BPS model that predicts bonus and
adds it to a points model trained on ``total_points`` counts it twice, which
inflates every premium captain by roughly two points a week -- large enough to
change every captaincy decision, small enough to look plausible.

Managers: ``element_type == 5`` existed for exactly one season (2024-25) and scores zero
under the 2026-27 rules. Any historical row for one must be dropped, not mapped.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from fpl_edge.rules import rules
from fpl_edge.types import Position

from .conftest import UTC, frame, player_row, result_row

AS_OF = dt.datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Bonus
# ---------------------------------------------------------------------------


def test_total_points_already_contains_bonus() -> None:
    """DOCUMENTS the invariant that makes double counting possible.

    Reconstructs a real stat line from the verified scoring rules and shows that
    ``total_points`` is only reproducible if bonus is added exactly once. Any
    pipeline computing ``predicted_points + predicted_bonus`` where the points
    model was fitted on ``total_points`` is adding it twice.
    """
    r = rules()
    # A midfielder: 90 minutes, one goal, one assist, clean sheet, 3 bonus.
    base = (
        r.get("scoring.minutes_long")
        + r.get("scoring.goal")["MID"]
        + r.get("scoring.assist")
        + r.get("scoring.clean_sheet")["MID"]
    )
    bonus = 3
    assert base == 11
    assert base + bonus == 14, "total_points for this line is 14, bonus included once"


def test_bonus_column_is_a_component_not_an_addition(wh) -> None:
    """GUARDS: a stored row where total_points excludes its own bonus.

    If ingestion ever writes ``total_points`` net of bonus, every downstream
    consumer that trusts the FPL convention silently under-counts instead. The
    invariant is checkable on any stored row: total_points must be at least the
    bonus.
    """
    wh.append("fact_player_fixture", frame([
        result_row(season="2026-27", code=1, fixture_id=1, gw=1, as_of=AS_OF,
                   goals_scored=1, assists=1, bonus=3, bps=45, total_points=14),
    ]))
    got = wh.snapshot_at(AS_OF + dt.timedelta(days=1)).results_before("2026-27")
    row = got.iloc[0]
    assert row["total_points"] >= row["bonus"], (
        "total_points is smaller than the bonus it is supposed to contain"
    )


def test_no_module_adds_bonus_to_total_points() -> None:
    """GUARDS: the double count, statically, across the whole tree.

    ``scripts/audit_leakage.py`` BONUS_DOUBLE_COUNT flags
    ``df["total_points"] + df["bonus"]`` wherever it appears. This test wires
    that rule into the suite so a future points model cannot introduce it.
    """
    from .conftest import REPO_ROOT, load_audit_script

    audit = load_audit_script()
    offenders = [f for f in audit.audit_tree(REPO_ROOT) if f.rule == "BONUS_DOUBLE_COUNT"]
    assert not offenders, "\n".join(f.render() for f in offenders)


def test_bonus_allocation_matches_the_official_tie_examples() -> None:
    """GUARDS: the distinct-value ranking bug in bonus allocation.

    The registry transcribes the official tie rules: "tie for 1st -> both get 3,
    third gets 1. tie for 2nd -> 3,2,2. tie for 3rd -> 3,2,1,1."

    The tie-for-first case is the one that separates a POSITIONAL rule
    (count players strictly above) from a DISTINCT-VALUE rule (count distinct
    scores above). A distinct-value implementation awards the third player 2
    instead of 1 and is otherwise indistinguishable. This test pins all three
    official examples.
    """
    from fpl_edge.models.points.bps import allocate_bonus

    def award(values: list[int]) -> list[int]:
        return allocate_bonus(np.array(values, dtype=np.int64).reshape(-1, 1))[:, 0].tolist()

    assert award([40, 40, 30, 20]) == [3, 3, 1, 0], "tie for first: third player gets 1, not 2"
    assert award([40, 30, 30, 20]) == [3, 2, 2, 0], "tie for second"
    assert award([40, 30, 20, 20]) == [3, 2, 1, 1], "tie for third"
    assert award([40, 30, 20, 10]) == [3, 2, 1, 0], "no ties"

    stated = rules().get("bps.tie_rules")
    assert "3,2,1,1" in stated.replace(" ", "")


# ---------------------------------------------------------------------------
# Manager elements
# ---------------------------------------------------------------------------


def test_manager_element_type_is_refused_not_coerced() -> None:
    """GUARDS: element_type 5 being mapped to a playing position.

    Regression guard, currently correct. ``Position.from_api(5)`` raises with an
    explanation rather than returning something plausible.
    """
    with pytest.raises(ValueError, match="(?i)manager"):
        Position.from_api(5)
    for good in (1, 2, 3, 4):
        assert Position.from_api(good).value == good


def test_manager_elements_are_dropped_by_bootstrap_ingest() -> None:
    """GUARDS: a manager element reaching dim_player.

    Regression guard for ``fpl_edge/ingest/fpl_api.py:76-88``, which counts what
    it skipped rather than swallowing it.
    """
    from fpl_edge.ingest.fpl_api import ingest_bootstrap
    from fpl_edge.store import Warehouse

    from .test_nan_and_imputation import _bootstrap_with, _StubFetcher

    payload = _bootstrap_with()
    manager = dict(payload["elements"][0], id=2, code=777777, element_type=5,
                   web_name="Arteta")
    payload["elements"].append(manager)

    wh = Warehouse(":memory:")
    written = ingest_bootstrap(wh, _StubFetcher(payload))

    assert written["skipped_non_player_elements"] == 1
    codes = set(wh.sql("SELECT code FROM dim_player")["code"])
    assert 777777 not in codes, "a manager element reached dim_player"


def test_manager_results_cannot_reach_a_training_set_through_results_before(wh) -> None:
    """GUARDS: 2025-26 manager rows scoring points in a 2026-27 model.

    ``Snapshot.results_before`` returns ``fact_player_fixture`` unfiltered. It
    joins nothing, so a row whose ``code`` has no ``dim_player`` entry -- which
    is precisely what a dropped manager element looks like -- is returned as a
    normal player-fixture result.

    Manager scoring in 2024-25 was substantial (goal and clean-sheet derived
    points for the club's result). Those points cannot be earned in 2026-27
    (``misc.manager_scoring_removed`` is verified true), so any model trained on
    them learns a scoring system that no longer exists, and the codes involved
    are indistinguishable from players without a join the read path does not do.
    """
    wh.append("dim_player", frame([
        player_row(season="2024-25", code=111, element_id=1, as_of=AS_OF, web_name="RealPlayer"),
    ]))
    wh.append("fact_player_fixture", frame([
        result_row(season="2024-25", code=111, fixture_id=1, gw=1, as_of=AS_OF, total_points=6),
        # A 2024-25 manager: has results, has no dim_player row, cannot score in 2026-27.
        result_row(season="2024-25", code=777777, fixture_id=1, gw=1, as_of=AS_OF,
                   total_points=8),
    ]))

    results = wh.snapshot_at(AS_OF + dt.timedelta(days=1)).results_before("2024-25")
    orphans = set(results["code"]) - set(
        wh.snapshot_at(AS_OF + dt.timedelta(days=1)).table("dim_player")["code"]
    )
    assert not orphans, (
        f"results_before() returned result rows for codes {sorted(orphans)} that "
        "have no dim_player row. A dropped manager element leaves exactly this "
        "footprint, and nothing in the read path filters it out"
    )


def test_manager_scoring_removal_is_a_verified_rule() -> None:
    """DOCUMENTS the rule that makes 2025-26 manager rows unusable.

    Regression guard: if this rule is ever flipped or unverified, every backtest
    spanning 2025-26 needs revisiting.
    """
    assert rules().get("misc.manager_scoring_removed") is True
    assert {int(p) for p in Position} == {1, 2, 3, 4}, (
        "Position must contain exactly the four 2026-27 element types"
    )
