"""Firing semantics, task decisions, and the outbox — all offline, all frozen.

Every test here builds its own DuckDB in tmp_path and passes an explicit `now`.
Nothing sleeps, nothing reaches the network, and no test touches the real
warehouse: the DAG's whole job is to be correct about time and about
double-sending, and neither can be tested against a clock that moves.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from fpl_edge.interfaces.telegram import FakeTransport, TelegramConfig
from fpl_edge.jobs import deadline_dag as dag
from fpl_edge.jobs import outbox
from fpl_edge.store import Warehouse

UTC = dt.UTC
SEASON = "2026-27"
GW1 = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)

CONFIG = TelegramConfig(token="test-token", allowed_chat_ids=frozenset({4242}))


# -- fixtures ---------------------------------------------------------------


def seed(db_path, *, players=(), states=(), deadlines=((1, GW1),)):
    """A warehouse with just enough dimension rows for the DAG to be honest."""
    with Warehouse(db_path) as wh:
        for gw, deadline in deadlines:
            wh.sql(
                "INSERT INTO dim_event (season, gw, deadline_utc, is_finished, as_of) "
                "VALUES (?, ?, ?, FALSE, ?)",
                [SEASON, int(gw), deadline, dt.datetime(2026, 8, 1, tzinfo=UTC)],
            )
        for code, name in players:
            wh.sql(
                "INSERT INTO dim_player (season, code, element_id, web_name, "
                "first_name, second_name, position, team_code, as_of) "
                "VALUES (?, ?, ?, ?, '', '', 3, 1, ?)",
                [SEASON, int(code), int(code), name, dt.datetime(2026, 8, 1, tzinfo=UTC)],
            )
        for s in states:
            wh.sql(
                "INSERT INTO fact_player_state (season, code, element_id, price_tenths, "
                "selected_by_pct, status, chance_of_playing_next_round, news, news_added, "
                "transfers_in_event, transfers_out_event, cost_change_start, as_of) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                [
                    SEASON, int(s["code"]), int(s["code"]), int(s.get("price_tenths", 70)),
                    float(s.get("owned", 10.0)), s.get("status", "a"),
                    s.get("chance"), s.get("news"), s.get("news_added"),
                    int(s["ti"]), int(s["to"]), s["as_of"],
                ],
            )
    return db_path


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "dag.duckdb"


def firings(db_path) -> list[tuple]:
    with Warehouse(db_path) as wh:
        df = wh.sql(
            "SELECT task, gw, due_utc, outcome, detail FROM dag_firing "
            "ORDER BY due_utc, task"
        )
    return [tuple(r) for r in df.itertuples(index=False)]


def deliveries(db_path) -> list[tuple]:
    with Warehouse(db_path) as wh:
        outbox.ensure_schema(wh)
        df = wh.sql(
            "SELECT monitor, title, delivered_telegram FROM platform_delivery "
            "ORDER BY created_utc, id"
        )
    return [tuple(r) for r in df.itertuples(index=False)]


def run_tick(db_path, now, **kw):
    kw.setdefault("transport", FakeTransport())
    kw.setdefault("config", CONFIG)
    kw.setdefault("polish", False)
    return dag.tick(now=now, season=SEASON, db_path=db_path, **kw)


# -- price radar: the deterministic trigger ---------------------------------


def radar_states(*, fast_delta: int, hours: float = 2.0):
    """Two snapshots `hours` apart; MOVER's net transfers climb by fast_delta."""
    t1 = dt.datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
    t0 = t1 - dt.timedelta(hours=hours)
    return [
        {"code": 1, "ti": 100_000, "to": 10_000, "as_of": t0, "owned": 40.0},
        {"code": 1, "ti": 100_000 + fast_delta, "to": 10_000, "as_of": t1, "owned": 40.0},
        {"code": 2, "ti": 50_000, "to": 5_000, "as_of": t0, "owned": 20.0},
        {"code": 2, "ti": 50_100, "to": 5_000, "as_of": t1, "owned": 20.0},
    ]


def radar_ctx(db_path, now):
    return dag.TaskContext(
        season=SEASON, gw=1, due_utc=now, deadline_utc=GW1, now=now, db_path=db_path
    )


def test_velocity_is_net_transfers_per_hour_and_fires_above_the_threshold(db):
    seed(db, players=[(1, "MOVER"), (2, "STILL")],
         states=radar_states(fast_delta=30_000, hours=2.0))
    now = dt.datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    res = dag.price_radar(radar_ctx(db, now))

    assert res.outcome == "delivered"
    assert "MOVER" in res.body and "+15,000/h" in res.body
    assert "STILL" not in res.body  # 100 over 2h = 50/h, nowhere near
    assert "RISE risk" in res.body


def test_below_the_threshold_is_quiet_but_still_observed(db):
    """The tuning series is the point: a quiet run must still leave numbers."""
    seed(db, players=[(1, "MOVER"), (2, "STILL")],
         states=radar_states(fast_delta=1_000, hours=2.0))
    now = dt.datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    res = dag.price_radar(radar_ctx(db, now))

    assert res.outcome == "quiet"
    assert res.title == ""
    metrics = {m for _, m, _ in res.observations}
    assert metrics == {"net_transfer_velocity_per_h", "net_transfers_event",
                       "selected_by_pct"}
    velocities = {c: v for c, m, v in res.observations
                  if m == "net_transfer_velocity_per_h"}
    assert velocities == {1: 500.0, 2: 50.0}


def test_threshold_boundary_is_inclusive(db):
    hours = 2.0
    exactly = int(dag.VELOCITY_THRESHOLD * hours)
    seed(db, players=[(1, "MOVER"), (2, "STILL")],
         states=radar_states(fast_delta=exactly, hours=hours))
    now = dt.datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    assert dag.price_radar(radar_ctx(db, now)).outcome == "delivered"


def test_a_gameweek_counter_reset_is_not_a_price_signal(db):
    """transfers_*_event zeroes at every deadline; that is not a mass sell-off."""
    t1 = dt.datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
    t0 = t1 - dt.timedelta(hours=2)
    seed(
        db, players=[(1, "A"), (2, "B")],
        states=[
            {"code": 1, "ti": 900_000, "to": 100_000, "as_of": t0},
            {"code": 2, "ti": 800_000, "to": 100_000, "as_of": t0},
            {"code": 1, "ti": 500, "to": 100, "as_of": t1},
            {"code": 2, "ti": 400, "to": 100, "as_of": t1},
        ],
    )
    now = dt.datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    res = dag.price_radar(radar_ctx(db, now))
    assert res.outcome == "quiet"
    assert "reset" in res.detail


def test_one_snapshot_is_not_a_velocity(db):
    t0 = dt.datetime(2026, 8, 19, 22, 0, tzinfo=UTC)
    seed(db, players=[(1, "A")], states=[{"code": 1, "ti": 1, "to": 0, "as_of": t0}])
    now = dt.datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    res = dag.price_radar(radar_ctx(db, now))
    assert res.outcome == "no_source"
    assert "two player-state snapshots" in res.detail


def test_the_radar_trigger_never_calls_an_llm(db, monkeypatch):
    """Structural, not aspirational: fail loudly if a model creeps in."""
    def boom(*a, **k):
        raise AssertionError("a deterministic trigger called the LLM polish path")

    monkeypatch.setattr(dag, "polish_copy", boom)
    seed(db, players=[(1, "MOVER"), (2, "STILL")],
         states=radar_states(fast_delta=30_000))
    now = dt.datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    assert dag.price_radar(radar_ctx(db, now)).outcome == "delivered"


# -- lineup check: the Pulselive feed ---------------------------------------


def _stub_ingest(monkeypatch):
    """Keep the T-90m task off the network: the refresh subprocess is stubbed
    and the check reads whatever the warehouse already holds."""
    step = dag.Step(name="ingest_lineups", ok=True, seconds=0.0, detail="stubbed")
    monkeypatch.setattr(dag, "_ingest_lineups_step", lambda ctx: step)


def _fixture(db, *, fixture_id=1, home=1, away=2):
    with Warehouse(db) as wh:
        wh.sql(
            "INSERT INTO fact_fixture (season, fixture_id, gw, kickoff_utc, "
            "home_team_code, away_team_code, finished, as_of) "
            "VALUES (?, ?, 1, ?, ?, ?, FALSE, ?)",
            [SEASON, fixture_id, GW1 + dt.timedelta(minutes=90), home, away,
             dt.datetime(2026, 8, 1, tzinfo=UTC)],
        )


def _confirm(db, code, *, started, fixture_id=1):
    with Warehouse(db) as wh:
        wh.sql(
            "INSERT INTO fact_confirmed_lineup (source, season, fixture_id, code, "
            "started, shirt, position_label, formation, as_of) "
            "VALUES ('pulselive', ?, ?, ?, ?, NULL, NULL, '4-4-2', ?)",
            [SEASON, fixture_id, int(code), bool(started),
             dt.datetime(2026, 8, 21, 16, 0, tzinfo=UTC)],
        )


def _plan(monkeypatch, tmp_path, *, captain=7, xi=(7,)):
    (tmp_path / "gw1_plan.json").write_text(json.dumps({
        "generated_at": "2026-08-21T10:00:00+00:00",
        "gw1": {"captain": captain, "vice_captain": captain,
                "squad": list(xi), "starting_xi": list(xi)},
    }))
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)


def test_lineup_check_without_a_plan_is_an_honest_no_source(db, monkeypatch, tmp_path):
    seed(db, players=[(7, "SKIPPER")])
    _stub_ingest(monkeypatch)
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)  # empty: no plan artefact
    res = dag.lineup_captain_check(radar_ctx(db, GW1 - dt.timedelta(minutes=90)))
    assert res.outcome == "no_source"
    assert res.delivers is False and "no plan" in res.detail


def test_lineup_check_is_quiet_while_no_teamsheet_is_published(db, monkeypatch, tmp_path):
    seed(db, players=[(7, "SKIPPER")])
    _fixture(db)
    _plan(monkeypatch, tmp_path)
    _stub_ingest(monkeypatch)
    res = dag.lineup_captain_check(radar_ctx(db, GW1 - dt.timedelta(minutes=90)))
    assert res.outcome == "quiet"
    assert res.delivers is False
    assert "no teamsheet published yet" in res.detail


def test_lineup_check_is_quiet_on_a_blank_gameweek(db, monkeypatch, tmp_path):
    seed(db, players=[(7, "SKIPPER")])  # no fixtures at all for this GW
    _plan(monkeypatch, tmp_path)
    _stub_ingest(monkeypatch)
    res = dag.lineup_captain_check(radar_ctx(db, GW1 - dt.timedelta(minutes=90)))
    assert res.outcome == "quiet"
    assert "blank" in res.detail


def test_lineup_check_wakes_up_when_the_feed_lands(db, monkeypatch, tmp_path):
    """The seam the feed was built for: a benched captain is an ACT alert."""
    seed(db, players=[(7, "SKIPPER"), (8, "WINGER")])
    _fixture(db)
    _confirm(db, 7, started=False)   # captain on the bench
    _confirm(db, 8, started=True)
    _plan(monkeypatch, tmp_path, captain=7, xi=(7, 8))
    _stub_ingest(monkeypatch)

    now = GW1 - dt.timedelta(minutes=90)
    res = dag.lineup_captain_check(radar_ctx(db, now))
    assert res.outcome == "delivered" and res.kind == "alert"
    assert "SKIPPER" in res.title and "not starting" in res.title
    assert "BENCH: SKIPPER" in res.body
    assert "Confirmed starting: WINGER" in res.body


def test_lineup_check_reports_the_xi_when_the_captain_is_fine(db, monkeypatch, tmp_path):
    seed(db, players=[(7, "SKIPPER"), (8, "WINGER"), (9, "GHOST")])
    _fixture(db)
    _confirm(db, 7, started=True)
    # 8 and 9 are in the XI; 8 has no row in a published teamsheet -> ABSENT,
    # while a player whose fixture has no teamsheet yet would be 'awaiting'.
    _plan(monkeypatch, tmp_path, captain=7, xi=(7, 8, 9))
    with Warehouse(db) as wh:  # GHOST's team plays a different, unpublished game
        wh.sql("UPDATE dim_player SET team_code = 3 WHERE code = 9")
    _fixture(db, fixture_id=2, home=3, away=4)
    _stub_ingest(monkeypatch)

    res = dag.lineup_captain_check(radar_ctx(db, GW1 - dt.timedelta(minutes=90)))
    assert res.outcome == "delivered" and res.kind == "report"
    assert "SKIPPER starting" in res.title
    assert "ABSENT from the squad: WINGER" in res.body
    assert "Teamsheet not out yet: GHOST" in res.body


# -- final solve: never solves, never lies about freshness -------------------


def test_a_stale_plan_is_delivered_as_a_stale_plan(db, monkeypatch, tmp_path):
    seed(db, players=[(226597, "HAALAND")])
    (tmp_path / "gw1_plan.json").write_text(json.dumps({
        "generated_at": "2026-08-19T09:00:00+00:00",  # 52h before the T-4h firing
        "gw1": {"captain": 226597, "vice_captain": 226597, "squad": [226597],
                "starting_xi": [226597], "bench": []},
    }))
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)

    now = GW1 - dt.timedelta(hours=4)
    res = dag.final_solve_delivery(radar_ctx(db, now))
    assert res.outcome == "delivered"
    assert "no fresh solve" in res.title
    assert "NOT being presented" in res.body
    assert "HAALAND" in res.body  # honest about what the stale plan said


def test_a_fresh_plan_is_delivered_with_names_not_codes(db, monkeypatch, tmp_path):
    seed(db, players=[(226597, "HAALAND"), (141746, "SALAH")])
    (tmp_path / "gw1_plan.json").write_text(json.dumps({
        "generated_at": "2026-08-21T09:00:00+00:00",
        "objective_mode": "expected_points", "objective": 325.3,
        "horizon_gws": [1, 2], "n_sims": 1000,
        "gw1": {"captain": 226597, "vice_captain": 141746,
                "squad": [226597, 141746], "starting_xi": [226597, 141746],
                "bench": [], "chip": "bboost"},
    }))
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)

    now = GW1 - dt.timedelta(hours=4)
    res = dag.final_solve_delivery(radar_ctx(db, now))
    assert res.outcome == "delivered"
    assert "HAALAND" in res.title
    assert "SALAH" in res.body and "226597" not in res.body
    assert "bboost" in res.body


def test_no_plan_at_all_says_so_rather_than_inventing_one(db, monkeypatch, tmp_path):
    seed(db, players=[(1, "A")])
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)
    res = dag.final_solve_delivery(radar_ctx(db, GW1 - dt.timedelta(hours=4)))
    assert res.outcome == "delivered"
    assert "no solve to deliver" in res.title
    assert "Nothing is being guessed" in res.body


# -- firing semantics: claim, overlap, idempotency, staleness ----------------


def test_the_tick_claims_runs_and_records(db, monkeypatch, tmp_path):
    seed(db, players=[(1, "MOVER"), (2, "STILL")],
         states=radar_states(fast_delta=30_000))
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)

    report = run_tick(db, now)
    rows = firings(db)
    radar = [r for r in rows if r[0] == "price_radar" and r[3] == "delivered"]
    assert len(radar) == 1
    assert [d[0] for d in deliveries(db)] == ["price_radar"]
    assert deliveries(db)[0][2] is not None  # flushed and stamped
    assert report.flush == "outbox: sent 1, failed 0"


def test_re_running_the_same_tick_delivers_nothing_further(db, monkeypatch, tmp_path):
    """The idempotency guarantee: identical input, no second message."""
    seed(db, players=[(1, "MOVER"), (2, "STILL")],
         states=radar_states(fast_delta=30_000))
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)

    run_tick(db, now)
    before_rows, before_deliveries = firings(db), deliveries(db)

    transport = FakeTransport()
    second = run_tick(db, now, transport=transport)

    assert firings(db) == before_rows
    assert deliveries(db) == before_deliveries
    assert second.fired == []
    assert transport.sent == []  # nothing re-sent, not even a duplicate
    assert any("price_radar" in s for s in second.skipped_overlap)


def test_a_row_left_running_by_a_crash_is_skipped_not_retried(db, monkeypatch, tmp_path):
    seed(db, players=[(1, "MOVER"), (2, "STILL")],
         states=radar_states(fast_delta=30_000))
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)
    due = dt.datetime(2026, 8, 20, 1, 0, tzinfo=UTC)

    with Warehouse(db) as wh:
        dag.apply_migrations(wh)
        wh.sql(
            "INSERT INTO dag_firing VALUES ('price_radar', ?, 1, ?, ?, 'running', NULL)",
            [SEASON, due, now],
        )

    report = run_tick(db, now)
    stuck = [r for r in firings(db) if r[0] == "price_radar" and r[2] == due]
    assert [r[3] for r in stuck] == ["running"]
    assert deliveries(db) == []
    assert f"price_radar@{due.isoformat()}" in report.skipped_overlap


def test_a_firing_the_machine_slept_through_is_recorded_not_fired(db, monkeypatch, tmp_path):
    seed(db, players=[(1, "MOVER"), (2, "STILL")],
         states=radar_states(fast_delta=30_000))
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)
    # Ten hours after the 01:00Z radar -- past even its own 8h stale window.
    # (Six hours would now DELIVER: per-task windows deliberately give the
    # radar 8h so a sleeping laptop does not discard an idempotent refresh;
    # see STALE_WINDOWS in deadline_dag.py.)
    now = dt.datetime(2026, 8, 20, 11, 0, tzinfo=UTC)

    report = run_tick(db, now)
    radar = [r for r in firings(db) if r[0] == "price_radar"]
    assert radar and all(r[3] == "skipped_stale" for r in radar)
    assert deliveries(db) == []
    assert all(f.outcome == "skipped_stale" for f in report.fired)
    assert "stale window" in radar[0][4]


def test_a_stale_skip_is_itself_recorded_only_once(db, monkeypatch, tmp_path):
    seed(db, players=[(1, "MOVER"), (2, "STILL")],
         states=radar_states(fast_delta=30_000))
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)
    now = dt.datetime(2026, 8, 20, 7, 0, tzinfo=UTC)
    run_tick(db, now)
    first = firings(db)
    run_tick(db, now)
    assert firings(db) == first


def test_a_task_that_raises_becomes_an_error_row_not_a_dead_process(db, monkeypatch, tmp_path):
    seed(db, players=[(1, "MOVER"), (2, "STILL")],
         states=radar_states(fast_delta=30_000))
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)

    def boom(ctx):
        raise RuntimeError("provider exploded")

    monkeypatch.setitem(dag.TASKS, "price_radar", boom)
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)

    report = run_tick(db, now)
    # The previous night's 01:00Z radar is inside the lookback and correctly
    # recorded stale; the one owed at THIS tick is the one that ran and blew up.
    live = dt.datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    radar = [r for r in firings(db) if r[0] == "price_radar" and r[2] == live]
    assert [r[3] for r in radar] == ["error"]
    assert "provider exploded" in radar[0][4]
    assert [f.outcome for f in report.fired if f.due_utc == live] == ["error"]


def test_observations_are_written_by_the_tick_on_a_quiet_run(db, monkeypatch, tmp_path):
    seed(db, players=[(1, "MOVER"), (2, "STILL")],
         states=radar_states(fast_delta=1_000))
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)

    run_tick(db, now)
    with Warehouse(db) as wh:
        df = wh.sql(
            "SELECT code, value FROM dag_observation "
            "WHERE task = 'price_radar' AND metric = 'net_transfer_velocity_per_h' "
            "ORDER BY code"
        )
    assert {int(r.code): float(r.value) for r in df.itertuples(index=False)} == {
        1: 500.0, 2: 50.0
    }
    live = dt.datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    assert [r[3] for r in firings(db) if r[0] == "price_radar" and r[2] == live] == ["quiet"]


def test_next_due_is_reported_from_real_deadlines(db, monkeypatch, tmp_path):
    seed(db, players=[(1, "A")], deadlines=[(1, GW1)])
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)
    now = dt.datetime(2026, 8, 20, 6, 15, tzinfo=UTC)
    report = run_tick(db, now)
    got = dict(report.next_due)
    assert got["presser_projection_refresh"] == "2026-08-20T11:30:00+00:00"
    assert got["final_solve_delivery"] == "2026-08-21T13:30:00+00:00"
    assert got["lineup_captain_check"] == "2026-08-21T16:00:00+00:00"


# -- outbox -----------------------------------------------------------------


def test_deliver_commits_the_message_and_the_outcome_together(db):
    with Warehouse(db) as wh:
        dag.apply_migrations(wh)
        due = dt.datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
        now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)
        wh.sql(
            "INSERT INTO dag_firing VALUES ('price_radar', ?, 1, ?, ?, 'running', NULL)",
            [SEASON, due, now],
        )
        stmt, params = dag._finish_sql(
            dag.Due("price_radar", SEASON, 1, due, GW1, False), "delivered", "2 movers"
        )
        outbox.deliver(wh, monitor="price_radar", kind="alert", title="t", body="b",
                       now=now, extra_sql=[(stmt, params)])
        assert wh.sql("SELECT outcome FROM dag_firing")["outcome"].iloc[0] == "delivered"
        assert len(wh.sql("SELECT * FROM platform_delivery")) == 1


def test_a_failing_side_statement_rolls_the_message_back_too(db):
    """Neither record may commit without the other (deliver.ts:125-128)."""
    with Warehouse(db) as wh:
        outbox.ensure_schema(wh)
        with pytest.raises(Exception):
            outbox.deliver(wh, monitor="m", kind="alert", title="t", body="b",
                           extra_sql=[("UPDATE no_such_table SET x = 1", [])])
        assert len(wh.sql("SELECT * FROM platform_delivery")) == 0


def test_the_same_occasion_enqueues_once(db):
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)
    with Warehouse(db) as wh:
        outbox.ensure_schema(wh)
        a = outbox.deliver(wh, monitor="m", kind="alert", title="t", body="b", now=now)
        b = outbox.deliver(wh, monitor="m", kind="alert", title="t", body="b", now=now)
        assert a == b
        assert len(wh.sql("SELECT * FROM platform_delivery")) == 1


def test_flush_sends_to_every_allowlisted_chat_then_stamps(db):
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)
    cfg = TelegramConfig(token="t", allowed_chat_ids=frozenset({1, 2}))
    tx = FakeTransport()
    with Warehouse(db) as wh:
        outbox.ensure_schema(wh)
        outbox.deliver(wh, monitor="m", kind="alert", title="Title", body="Body", now=now)
        res = outbox.flush_outbox(wh, transport=tx, config=cfg, now=now)

        assert res.sent == 1 and res.failed == 0
        methods = [m for m, _ in tx.sent]
        assert methods == ["sendMessage", "sendMessage"]
        assert "getUpdates" not in methods  # send-only: the bot owns the poller
        assert {p["chat_id"] for _, p in tx.sent} == {1, 2}
        assert tx.sent[0][1]["text"] == "Title\n\nBody"
        assert "parse_mode" not in tx.sent[0][1]
        assert outbox.pending(wh) == []


def test_a_send_failure_leaves_the_row_pending_for_the_next_tick(db):
    class Broken(FakeTransport):
        def call(self, method, payload, *, timeout=30.0):
            raise RuntimeError("telegram down")

    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)
    with Warehouse(db) as wh:
        outbox.ensure_schema(wh)
        outbox.deliver(wh, monitor="m", kind="alert", title="T", body="B", now=now)
        res = outbox.flush_outbox(wh, transport=Broken(), config=CONFIG, now=now)
        assert res.failed == 1 and res.sent == 0
        assert len(outbox.pending(wh)) == 1  # retried, not silently lost

        ok = FakeTransport()
        assert outbox.flush_outbox(wh, transport=ok, config=CONFIG, now=now).sent == 1
        assert outbox.pending(wh) == []


def test_an_unconfigured_bot_keeps_the_message_rather_than_dropping_it(db):
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)
    with Warehouse(db) as wh:
        outbox.ensure_schema(wh)
        outbox.deliver(wh, monitor="m", kind="alert", title="T", body="B", now=now)
        res = outbox.flush_outbox(
            wh, config=TelegramConfig(token=None, allowed_chat_ids=frozenset()), now=now
        )
        assert res.skipped_no_config
        assert len(outbox.pending(wh)) == 1


# -- LLM polish: after the decision, and never fatal -------------------------


def test_polish_returns_the_deterministic_text_when_the_cli_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(dag, "CLAUDE_BIN", tmp_path / "does-not-exist")
    assert dag.polish_copy("T", "B") == ("T", "B")


def test_polish_scrubs_the_nested_session_markers(monkeypatch, tmp_path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setattr(dag, "CLAUDE_BIN", fake)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")

    seen = {}

    def spy(argv, **kw):
        seen.update(kw.get("env") or {})
        raise RuntimeError("stop here")

    monkeypatch.setattr(dag.subprocess, "run", spy)
    assert dag.polish_copy("T", "B") == ("T", "B")  # failure is never fatal
    assert "CLAUDECODE" not in seen
    assert "CLAUDE_CODE_ENTRYPOINT" not in seen


def test_polish_rejects_an_empty_rewrite(monkeypatch, tmp_path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setattr(dag, "CLAUDE_BIN", fake)

    class Proc:
        returncode = 0
        stdout = '{"title": "", "body": ""}'
        stderr = ""

    monkeypatch.setattr(dag.subprocess, "run", lambda *a, **k: Proc())
    assert dag.polish_copy("T", "B") == ("T", "B")


# -- the missing-provider guard ---------------------------------------------


def test_a_missing_projections_cli_is_a_skipped_step_not_a_failed_task():
    assert dag._module_exists("fpl_edge.jobs.deadline_dag") is True
    assert dag._module_exists("fpl_edge.ingest.projections.no_such_cli") is False
    assert dag._module_exists("not_a_package_at_all.cli") is False
