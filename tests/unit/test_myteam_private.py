"""The authenticated my-team path: cookie in, exact state out, secrets nowhere.

The client never sees a password -- that is a design boundary, not a gap -- and
the session cookie must never appear in output, errors, or repr.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.myteam.private import (
    NoSessionError,
    PrivateTeamClient,
    SETUP_STEPS,
    StaleSessionError,
)

COOKIE = "pl_profile=SECRET_SESSION_VALUE; other=x"


def my_team_body() -> dict:
    picks = []
    for slot in range(1, 16):
        picks.append({
            "element": 100 + slot, "position": slot,
            "selling_price": 50 + slot, "purchase_price": 49 + slot,
            "multiplier": 2 if slot == 3 else (1 if slot <= 11 else 0),
            "is_captain": slot == 3, "is_vice_captain": slot == 4,
        })
    return {
        "picks": picks,
        "chips": [
            {"name": "wildcard", "status_for_entry": "available"},
            {"name": "bboost", "status_for_entry": "available"},
            {"name": "3xc", "status_for_entry": "played"},
        ],
        "transfers": {"bank": 15, "value": 1002, "limit": 2, "made": 1, "cost": 4},
    }


def test_missing_cookie_fails_with_setup_steps_not_a_403() -> None:
    client = PrivateTeamClient(cookie="")
    assert not client.configured
    with pytest.raises(NoSessionError, match="DevTools"):
        client.fetch(4490171)


def test_parse_carries_exact_purchase_prices_bank_and_chips() -> None:
    squad = PrivateTeamClient._parse(my_team_body())
    assert squad.bank.tenths == 15
    assert squad.squad_value.tenths == 1002
    assert squad.free_transfers == 2
    assert squad.transfers_made_this_gw == 1
    assert squad.purchase_by_element[103] == 52
    assert squad.selling_by_element[103] == 53
    assert squad.chips["3xc"] == "played"
    cap = [p for p in squad.picks.picks if p.is_captain]
    assert len(cap) == 1 and cap[0].element == 103


def test_wrong_pick_count_is_refused_loudly() -> None:
    body = my_team_body()
    body["picks"] = body["picks"][:14]
    with pytest.raises(ValueError, match="14 picks"):
        PrivateTeamClient._parse(body)


def test_missing_bank_is_refused_rather_than_guessed() -> None:
    body = my_team_body()
    del body["transfers"]["bank"]
    with pytest.raises(ValueError, match="refusing to guess"):
        PrivateTeamClient._parse(body)


def test_stale_cookie_gives_refresh_instructions(monkeypatch) -> None:
    import httpx

    def reject(self, url, headers=None):
        return httpx.Response(403, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", reject)
    client = PrivateTeamClient(cookie=COOKIE)
    with pytest.raises(StaleSessionError, match="expired") as exc:
        client.fetch(4490171)
    # The error explains recovery without quoting the credential.
    assert "SECRET_SESSION_VALUE" not in str(exc.value)


def test_cookie_never_appears_in_repr_or_errors() -> None:
    client = PrivateTeamClient(cookie=COOKIE)
    assert "SECRET_SESSION_VALUE" not in repr(client)
    assert "SECRET_SESSION_VALUE" not in SETUP_STEPS


def test_no_password_field_exists_anywhere_in_the_module() -> None:
    """The boundary is structural: there is nothing to put a password INTO."""
    import inspect

    import fpl_edge.myteam.private as mod

    src = inspect.getsource(mod)
    assert "FPL_PASSWORD" not in src
    assert "username" not in src.lower().replace("no password", "")


def test_reconstruct_prefers_private_over_manual_and_reports_provenance(tmp_path) -> None:
    import datetime as _dt

    import pandas as pd

    from fpl_edge.myteam.state import Provenance, reconstruct
    from fpl_edge.store import Warehouse
    from tests.unit.test_myteam_state import SEASON, _entry, _universe, T0, UTC

    wh = Warehouse(tmp_path / "t.duckdb")
    players, states = _universe()
    wh.append("dim_player", players)
    wh.append("fact_player_state", states)
    wh.append("dim_event", pd.DataFrame([{
        "season": SEASON, "gw": 1,
        "deadline_utc": _dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC),
        "is_finished": False, "as_of": T0,
    }]))
    snap = wh.snapshot_at(_dt.datetime(2026, 8, 20, tzinfo=UTC))

    body = my_team_body()
    # Align a legal 2/5/5/3 with the seeded universe: elements are code-1000 and
    # the layout is GKP,GKP,DEF*5,MID*5,FWD*3 in code order.
    frame = snap.players(SEASON).sort_values("code")
    by_pos = {p: frame[frame["position"] == p]["element_id"].tolist() for p in (1, 2, 3, 4)}
    chosen = (by_pos[1][:1] + by_pos[2][:4] + by_pos[3][:4] + by_pos[4][:2]   # XI 1-4-4-2
              + by_pos[1][1:2] + by_pos[2][4:5] + by_pos[3][4:5] + by_pos[4][2:3])
    for i, pick in enumerate(body["picks"]):
        pick["element"] = int(chosen[i])

    private = PrivateTeamClient._parse(body)
    state = reconstruct(
        snapshot=snap, entry_id=4490171, season=SEASON,
        entry=_entry(), transfers=(), private=private,
        gw=1, validate=False,
    )
    assert state.provenance is Provenance.PRIVATE_API
    assert state.bank.tenths == 15  # observed from the account, not derived
