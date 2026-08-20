"""The authenticated my-team path: cookie in, exact state out, secrets nowhere.

The client never sees a password -- that is a design boundary, not a gap -- and
the session cookie must never appear in output, errors, or repr.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.myteam.tokens import TokenManager
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


def _no_auth(tmp_path) -> TokenManager:
    """A token manager pinned to an empty env: no tokens, no network, ever."""
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return TokenManager(env_path=empty)


def test_missing_cookie_fails_with_setup_steps_not_a_403(tmp_path) -> None:
    client = PrivateTeamClient(cookie="", tokens=_no_auth(tmp_path))
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


def test_stale_cookie_gives_refresh_instructions(monkeypatch, tmp_path) -> None:
    import httpx

    def reject(self, url, headers=None):
        return httpx.Response(403, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", reject)
    client = PrivateTeamClient(cookie=COOKIE, tokens=_no_auth(tmp_path))
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


class _FakeTokens:
    """A TokenManager stand-in with scripted behaviour and zero network."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = list(tokens)
        self.refreshes = 0
        self.configured = True

    def access_token(self) -> str:
        return self._tokens[0]

    def _read(self) -> dict:
        return {}

    def _refresh(self, env: dict) -> str:
        self.refreshes += 1
        return self._tokens[1]


def test_bearer_path_reads_the_squad_without_a_cookie(monkeypatch) -> None:
    import httpx

    sent = {}

    def ok(self, url, headers=None):
        sent["auth"] = (headers or {}).get("X-API-Authorization", "")
        return httpx.Response(200, json=my_team_body(),
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", ok)
    client = PrivateTeamClient(cookie="", tokens=_FakeTokens(["tokA", "tokB"]))
    squad = client.fetch(4490171)
    assert squad.bank.tenths == 15
    assert sent["auth"] == "Bearer tokA"


def test_bearer_401_forces_one_refresh_then_succeeds(monkeypatch) -> None:
    import httpx

    calls = {"n": 0}

    def first_401(self, url, headers=None):
        calls["n"] += 1
        auth = (headers or {}).get("X-API-Authorization", "")
        if auth == "Bearer tokA":
            return httpx.Response(401, request=httpx.Request("GET", url))
        return httpx.Response(200, json=my_team_body(),
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", first_401)
    fake = _FakeTokens(["tokA", "tokB"])
    squad = PrivateTeamClient(cookie="", tokens=fake).fetch(4490171)
    assert fake.refreshes == 1
    assert calls["n"] == 2
    assert squad.squad_value.tenths == 1002


def test_bearer_401_after_refresh_is_a_revoked_session(monkeypatch) -> None:
    import httpx

    def always_401(self, url, headers=None):
        return httpx.Response(401, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", always_401)
    fake = _FakeTokens(["tokA", "tokB"])
    with pytest.raises(StaleSessionError, match="revoked"):
        PrivateTeamClient(cookie="", tokens=fake).fetch(4490171)


def test_a_refused_refresh_reason_survives_the_cookie_fallback(monkeypatch) -> None:
    """The first failure is the true one; the fallback must not overwrite it.

    Observed live: the refresh grant was refused (HTTP 400, revoked) and the
    stale cookie then 403'd. The manager was told only "FPL rejected the session
    cookie", which points at the wrong thing to fix. Both reasons must appear.
    """
    import httpx

    from fpl_edge.myteam.tokens import RefreshRefusedError

    class RefusingTokens(_FakeTokens):
        def access_token(self) -> str:
            raise RefreshRefusedError("the token endpoint refused the refresh grant")

    def cookie_403(self, url, headers=None):
        return httpx.Response(403, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", cookie_403)
    with pytest.raises(StaleSessionError) as exc:
        PrivateTeamClient(
            cookie="pl_profile=stale", tokens=RefusingTokens(["a", "b"])
        ).fetch(4490171)
    message = str(exc.value)
    assert "refused the refresh grant" in message, "the true first cause was lost"
    assert "403" in message, "the fallback's own failure should still be reported"


def test_a_plain_cookie_setup_still_gets_the_simple_cookie_message(monkeypatch) -> None:
    """No bearer configured means no bearer preamble to confuse the reader."""
    import httpx

    class Unconfigured(_FakeTokens):
        def __init__(self) -> None:
            super().__init__(["a", "b"])
            self.configured = False

    monkeypatch.setattr(
        httpx.Client, "get",
        lambda self, url, headers=None: httpx.Response(
            403, request=httpx.Request("GET", url)
        ),
    )
    with pytest.raises(StaleSessionError) as exc:
        PrivateTeamClient(cookie="pl_profile=stale", tokens=Unconfigured()).fetch(4490171)
    message = str(exc.value)
    assert "rejected the session cookie" in message
    assert "Bearer auth failed first" not in message


def test_status_never_calls_an_unexpired_refresh_token_valid() -> None:
    """Rotating tokens can be revoked long before `exp`.

    Saying "valid" from `exp` alone sent a manager chasing the wrong fix while
    the issuer was refusing the grant outright.
    """
    import base64
    import datetime as dt
    import json

    from fpl_edge.myteam.tokens import TokenManager

    def token(days: int) -> str:
        exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)
        payload = base64.urlsafe_b64encode(
            json.dumps({"exp": int(exp.timestamp())}).encode()
        ).decode().rstrip("=")
        return f"x.{payload}.y"

    manager = TokenManager()
    manager._read = lambda: {  # type: ignore[method-assign]
        "FPL_ACCESS_TOKEN": token(1),
        "FPL_REFRESH_TOKEN": token(177),
    }
    status = manager.status()
    assert "refresh token unexpired" in status
    assert "refresh token valid" not in status
    assert "revoke" in status
