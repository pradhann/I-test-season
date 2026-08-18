"""The rule registry is the engine's contract with reality. Guard it hard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fpl_edge.rules import UnverifiedRuleError, RuleNotFoundError, rules

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "fpl_api"


def _latest_bootstrap() -> dict:
    snaps = sorted(RAW_DIR.glob("bootstrap_static_*.json"))
    if not snaps:
        pytest.skip("no cached bootstrap snapshot available")
    with snaps[-1].open() as fh:
        return json.load(fh)


def test_season_is_current() -> None:
    assert rules().season == "2026/27"


def test_squad_constraints_match_api() -> None:
    bs = _latest_bootstrap()
    api = bs["game_config"]["rules"]
    r = rules()
    assert r.get("squad.size") == api["squad_squadsize"]
    assert r.get("squad.starting_xi") == api["squad_squadplay"]
    assert r.get("squad.budget_tenths") == api["squad_total_spend"]
    assert r.get("squad.max_per_club") == api["squad_team_limit"]


def test_scoring_matches_api() -> None:
    """Registry scoring must not drift from what the game actually pays out."""
    bs = _latest_bootstrap()
    api = bs["game_config"]["scoring"]
    r = rules()
    assert r.get("scoring.minutes_short") == api["short_play"]
    assert r.get("scoring.minutes_long") == api["long_play"]
    assert r.get("scoring.goal") == api["goals_scored"]
    assert r.get("scoring.assist") == api["assists"]
    assert r.get("scoring.clean_sheet") == api["clean_sheets"]
    assert r.get("scoring.penalty_save") == api["penalties_saved"]
    assert r.get("scoring.penalty_miss") == api["penalties_missed"]
    assert r.get("scoring.yellow_card") == api["yellow_cards"]
    assert r.get("scoring.red_card") == api["red_cards"]
    assert r.get("scoring.own_goal") == api["own_goals"]
    assert r.get("defensive_contribution.points") == api["defensive_contribution"]


def test_free_transfer_cap_derived_from_api() -> None:
    bs = _latest_bootstrap()
    api = bs["game_config"]["rules"]
    assert rules().get("transfers.max_banked") == 1 + api["max_extra_free_transfers"]


def test_chip_windows_match_api() -> None:
    """The GW1 wildcard/free-hit lockout is a classic source of invalid plans."""
    bs = _latest_bootstrap()
    windows: dict[str, list[list[int]]] = {}
    for chip in bs["chips"]:
        windows.setdefault(chip["name"], []).append([chip["start_event"], chip["stop_event"]])
    for name, api_windows in windows.items():
        assert rules().get("chips.windows")[name] == api_windows, name


def test_wildcard_and_freehit_unavailable_in_gw1() -> None:
    w = rules().get("chips.windows")
    assert w["wildcard"][0][0] == 2
    assert w["freehit"][0][0] == 2
    assert w["bboost"][0][0] == 1
    assert w["3xc"][0][0] == 1


def test_manager_scoring_is_gone_this_season() -> None:
    """2025/26 had Manager elements. Backtests must strip them for 2026/27."""
    bs = _latest_bootstrap()
    assert rules().get("misc.manager_scoring_removed") is True
    assert {t["id"] for t in bs["element_types"]} == {1, 2, 3, 4}
    for key, val in bs["game_config"]["scoring"].items():
        if key.startswith("mng_"):
            flat = val.values() if isinstance(val, dict) else [val]
            assert all(v == 0 for v in flat), key


def test_defensive_contribution_thresholds() -> None:
    r = rules()
    assert r.get("defensive_contribution.def_threshold") == 10
    assert r.get("defensive_contribution.mid_fwd_threshold") == 12
    assert r.get("defensive_contribution.stacks") is False
    # Recoveries count for MID/FWD but NOT for DEF -- a very easy bug to write.
    assert "recoveries" not in r.get("defensive_contribution.def_actions")
    assert "recoveries" in r.get("defensive_contribution.mid_fwd_actions")


def test_unverified_rule_raises_rather_than_guessing() -> None:
    r = rules()
    unverified = r.rule("prices.in_season_change_time_utc")
    assert unverified.verified is False
    with pytest.raises(UnverifiedRuleError):
        r.get("prices.in_season_change_time_utc")


def test_missing_rule_raises() -> None:
    with pytest.raises(RuleNotFoundError):
        rules().get("scoring.does_not_exist")


def test_every_rule_has_a_source_unless_unverified() -> None:
    for rule in rules()._flat.values():
        if rule.verified:
            assert rule.source, f"{rule.path} is verified but cites no source"


def test_deadline_authority_is_the_api() -> None:
    """Guards against re-introducing the browser-local-time deadline bug."""
    note = rules().rule("deadlines.authoritative_source").note or ""
    assert "browser-local" in note or "rules page" in note
