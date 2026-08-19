"""The weekly report's ``transfers`` section, and the bot's squad commands.

The report convention this project was built around is that a section with no
provider says so explicitly. There are three distinct ways this section can have
nothing to recommend and they need three different sentences, because "I do not
know your squad" and "I know your squad but have no points model" have different
fixes and only one of them is the manager's to make.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.interfaces.report import EXPECTED, missing, registered
from fpl_edge.myteam import report as myteam_report
from fpl_edge.myteam.bot import SQUAD_SNIFF_THRESHOLD, MyTeamCommands, install
from fpl_edge.myteam.sources import EntryHistory, EntrySummary
from fpl_edge.myteam.state import PlayerIndex
from fpl_edge.store import Warehouse
from fpl_edge.types import Position

UTC = dt.timezone.utc
SEASON = "2026-27"
T0 = dt.datetime(2026, 8, 1, 12, tzinfo=UTC)
NOW = dt.datetime(2026, 8, 18, 12, tzinfo=UTC)

#: Fifteen real-ish web_names costing exactly £100.0m. Deliberately avoids
#: names that the shared resolver's nickname table rewrites ("Trent", "Bruno"),
#: because those would resolve to players this tiny fixture universe lacks.
SQUAD = [
    ("Raya", Position.GKP, 55, 1), ("Dubravka", Position.GKP, 40, 2),
    ("Gabriel", Position.DEF, 55, 1), ("Timber", Position.DEF, 60, 3),
    ("Munoz", Position.DEF, 55, 4), ("Konate", Position.DEF, 60, 3),
    ("Diop", Position.DEF, 40, 5),
    ("Odegaard", Position.MID, 90, 6), ("Saka", Position.MID, 80, 1),
    ("Rice", Position.MID, 45, 4), ("Semenyo", Position.MID, 70, 7),
    ("Palmer", Position.MID, 85, 11),
    ("Haaland", Position.FWD, 135, 8), ("Wood", Position.FWD, 70, 9),
    ("Evanilson", Position.FWD, 60, 7),
]


@pytest.fixture()
def warehouse(tmp_path) -> Warehouse:
    wh = Warehouse(tmp_path / "t.duckdb")
    players, states = [], []
    for i, (web, pos, price, club) in enumerate(SQUAD):
        code = 4000 + i
        players.append({
            "season": SEASON, "code": code, "element_id": i + 1, "web_name": web,
            "first_name": "F", "second_name": web, "position": int(pos),
            "team_code": club, "as_of": T0,
        })
        states.append({
            "season": SEASON, "code": code, "element_id": i + 1,
            "price_tenths": price, "selected_by_pct": 5.0, "status": "a",
            "chance_of_playing_next_round": None, "news": "", "news_added": None,
            "transfers_in_event": 0, "transfers_out_event": 0,
            "cost_change_start": 0, "as_of": T0,
        })
    wh.append("dim_player", pd.DataFrame(players))
    wh.append("fact_player_state", pd.DataFrame(states))
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": gw,
         "deadline_utc": dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
                         + dt.timedelta(days=7 * (gw - 1)),
         "is_finished": False, "as_of": T0}
        for gw in range(1, 6)
    ]))
    return wh


class OfflineClient:
    """A PublicEntryClient that answers from memory, so tests never hit the API."""

    def __init__(self, *, picks=None, history=None, transfers=()):
        self._picks = picks
        self._history = history or EntryHistory((), (), ())
        self._transfers = transfers
        self.closed = False

    def entry(self, entry_id):
        return EntrySummary(
            entry_id=entry_id, name="i-test", player_name="N P", started_event=1,
            current_event=None, entered_events=(), last_deadline_bank=None,
            last_deadline_value=None, last_deadline_total_transfers=0,
            years_active=10, favourite_team=16, summary_overall_points=None,
            summary_overall_rank=None,
        )

    def history(self, entry_id):
        return self._history

    def transfers(self, entry_id):
        return self._transfers

    def picks(self, entry_id, gw):
        return self._picks

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture(autouse=True)
def clean_providers():
    myteam_report.reset_providers()
    yield
    myteam_report.reset_providers()


# -- registration -------------------------------------------------------------


def test_the_transfers_section_is_registered_on_import() -> None:
    """`make weekly` must show it without knowing this package exists."""
    import fpl_edge.myteam  # noqa: F401  - the import is the registration

    assert "transfers" in registered()
    assert "transfers" not in missing()
    assert "transfers" in EXPECTED


def test_registration_is_idempotent() -> None:
    myteam_report.register()
    myteam_report.register()
    assert registered().count("transfers") == 1


# -- the three gaps -----------------------------------------------------------


def test_an_unknown_squad_is_reported_as_a_gap_with_the_fix(warehouse, tmp_path) -> None:
    body = myteam_report.render_transfers.__wrapped__(  # type: ignore[attr-defined]
        warehouse, SEASON, 1, NOW
    ) if hasattr(myteam_report.render_transfers, "__wrapped__") else _render(
        warehouse, tmp_path, client=OfflineClient()
    )
    assert "No transfer recommendation: your squad is not known" in body
    assert "fpl myteam set" in body
    assert "needs the account password" in body


def _render(warehouse, tmp_path, *, client, gw: int = 1):
    """Render the section with the network stubbed out."""
    from fpl_edge.myteam.store import MyTeamStore

    original = myteam_report.current_state

    def patched(wh, season, as_of, *, entry_id=None, gw=None, client=client, store=None):
        return original(
            wh, season, as_of, entry_id=4490171, gw=gw, client=client,
            store=MyTeamStore(4490171, root=tmp_path),
        )

    myteam_report.current_state = patched
    try:
        return myteam_report.render_transfers(warehouse, SEASON, gw, NOW)
    finally:
        myteam_report.current_state = original


def test_no_points_forecast_is_reported_as_a_gap(warehouse, tmp_path) -> None:
    """Squad known, but nothing to optimise with: a different sentence."""
    from fpl_edge.interfaces.parsing import PlayerResolver
    from fpl_edge.myteam.manual import build_draft
    from fpl_edge.myteam.store import MyTeamStore

    snapshot = warehouse.snapshot_at(NOW)
    index = PlayerIndex.from_snapshot(snapshot, SEASON)
    draft = build_draft(
        "\n".join(name for name, *_ in SQUAD),
        resolver=PlayerResolver(snapshot.players(SEASON)), index=index,
        entry_id=4490171, season=SEASON, gw=1, now=NOW,
    )
    assert draft.ok, draft.problems or draft.questions
    store = MyTeamStore(4490171, root=tmp_path)
    store.confirm(store.stage(draft), now=NOW)

    myteam_report.configure(mode=__import__(
        "fpl_edge.opt", fromlist=["ObjectiveMode"]
    ).ObjectiveMode.EXPECTED_POINTS)
    body = _render(warehouse, tmp_path, client=OfflineClient())
    assert "no points forecast is configured" in body
    assert "SampledPointsForecast" in body


def test_rank_utility_gap_names_the_surrogate_without_using_it(warehouse, tmp_path) -> None:
    from fpl_edge.interfaces.parsing import PlayerResolver
    from fpl_edge.myteam.forecast import TablePointsForecast
    from fpl_edge.myteam.manual import build_draft
    from fpl_edge.myteam.store import MyTeamStore

    snapshot = warehouse.snapshot_at(NOW)
    index = PlayerIndex.from_snapshot(snapshot, SEASON)
    draft = build_draft(
        "\n".join(name for name, *_ in SQUAD),
        resolver=PlayerResolver(snapshot.players(SEASON)), index=index,
        entry_id=4490171, season=SEASON, gw=1, now=NOW,
    )
    store = MyTeamStore(4490171, root=tmp_path)
    store.confirm(store.stage(draft), now=NOW)

    frame = pd.DataFrame([
        {"code": c, "gw": 1, "xpts": 2.0, "p_play": 0.9} for c in index.price_now
    ])
    myteam_report.configure(points_forecast=TablePointsForecast(frame=frame), horizon=1)
    body = _render(warehouse, tmp_path, client=OfflineClient())
    assert "rank-utility objective has no provider" in body
    assert "--mode expected-points" in body
    assert "genuinely different recommendation" in body


def test_a_manual_squad_is_labelled_as_such(warehouse, tmp_path) -> None:
    from fpl_edge.interfaces.parsing import PlayerResolver
    from fpl_edge.myteam.manual import build_draft
    from fpl_edge.myteam.store import MyTeamStore

    snapshot = warehouse.snapshot_at(NOW)
    index = PlayerIndex.from_snapshot(snapshot, SEASON)
    draft = build_draft(
        "\n".join(name for name, *_ in SQUAD),
        resolver=PlayerResolver(snapshot.players(SEASON)), index=index,
        entry_id=4490171, season=SEASON, gw=1, now=NOW,
    )
    store = MyTeamStore(4490171, root=tmp_path)
    store.confirm(store.stage(draft), now=NOW)
    body = _render(warehouse, tmp_path, client=OfflineClient())
    assert "from your own manual entry" in body
    assert "reconciled against the public picks endpoint" in body


def test_a_dead_endpoint_does_not_invent_a_recommendation(warehouse, tmp_path) -> None:
    class Broken(OfflineClient):
        def entry(self, entry_id):
            raise RuntimeError("connection reset")

    body = _render(warehouse, tmp_path, client=Broken())
    assert "Could not reconstruct your squad" in body
    assert "No recommendation is offered" in body


# -- the bot ------------------------------------------------------------------


@pytest.fixture()
def commands(warehouse, tmp_path) -> MyTeamCommands:
    return MyTeamCommands(
        warehouse=warehouse, entry_id=4490171, season=SEASON, store_root=tmp_path,
        client_factory=OfflineClient, now=lambda: NOW,
    )


def test_the_bot_claims_its_own_commands(commands) -> None:
    for text in ("/setsquad", "/confirm abcd1234", "/myteam", "/sync", "/discard"):
        assert commands.claims(text)


def test_the_bot_does_not_claim_an_idea(commands) -> None:
    for text in ("I like Rashford", "Semenyo captain GW12?", "/review", "sell Wood"):
        assert not commands.claims(text)


def test_a_pasted_squad_is_recognised_without_a_command(commands) -> None:
    """The real journey is paste-from-the-app, not remembering a slash command."""
    text = "\n".join(name for name, *_ in SQUAD)
    assert commands.claims(text)
    assert len(SQUAD) >= SQUAD_SNIFF_THRESHOLD


def test_the_bot_shows_the_squad_back_and_saves_nothing_yet(commands) -> None:
    reply = commands.handle("/setsquad\n" + "\n".join(name for name, *_ in SQUAD))
    assert "Check this is your" in reply
    assert "Haaland" in reply
    assert commands.store.confirmed(season=SEASON) is None


def test_confirming_with_the_token_saves_it(commands) -> None:
    commands.handle("/setsquad\n" + "\n".join(name for name, *_ in SQUAD))
    token = commands.store.pending().digest
    reply = commands.handle(f"/confirm {token}")
    assert "Saved your" in reply
    assert commands.store.confirmed(season=SEASON) is not None


def test_confirming_without_a_token_asks_for_one(commands) -> None:
    commands.handle("/setsquad\n" + "\n".join(name for name, *_ in SQUAD))
    reply = commands.handle("/confirm")
    assert "with the token" in reply
    assert commands.store.confirmed(season=SEASON) is None


def test_sync_before_any_gameweek_says_so(commands) -> None:
    assert "publishes no picks yet" in commands.handle("/sync")


def test_installing_leaves_the_idea_path_alone(commands) -> None:
    """Squad commands are added; everything else still reaches the inbox."""
    seen: list[str] = []

    class FakeBot:
        def _dispatch(self, text, *, chat_id, now=None):
            seen.append(text)
            return "idea logged"

    bot = install(FakeBot(), commands)
    assert bot._dispatch("I like Rashford", chat_id=1) == "idea logged"
    assert seen == ["I like Rashford"]

    reply = bot._dispatch("/myteam", chat_id=1)
    assert "Squad source" in reply
    assert seen == ["I like Rashford"], "the squad command must not reach the inbox"


def test_a_squad_command_failure_does_not_eat_the_message(commands) -> None:
    class Exploding(MyTeamCommands):
        def handle(self, text):
            raise RuntimeError("boom")

    broken = Exploding(
        warehouse=commands.warehouse, entry_id=4490171, season=SEASON,
        store_root=commands.store_root, client_factory=OfflineClient, now=lambda: NOW,
    )

    class FakeBot:
        def _dispatch(self, text, *, chat_id, now=None):
            return "idea logged"

    bot = install(FakeBot(), broken)
    reply = bot._dispatch("/myteam", chat_id=1)
    assert "Nothing was saved" in reply
