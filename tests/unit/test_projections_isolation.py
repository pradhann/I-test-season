"""Per-provider failure isolation in the ingest CLI.

The run that matters happens in the ninety minutes before a Friday deadline. On
that run, a provider that has changed its HTML, let a certificate lapse or
simply gone dark must cost us THAT PROVIDER'S rows and nothing else. A bare
loop turns one site's bad afternoon into a blind transfer.

Everything here is offline: each step is replaced with a stub, so the test
measures the runner's behaviour rather than any site's uptime.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.ingest.projections import cli
from fpl_edge.ingest.projections.store import ProjectionStore
from fpl_edge.store import Warehouse

AS_OF = dt.datetime(2026, 8, 20, 6, 30, tzinfo=dt.timezone.utc)


def _row(provider: str, code: int) -> pd.DataFrame:
    frame = pd.DataFrame([{
        "provider": provider, "season": "2026-27", "gw": 1, "code": code,
        "xp": 4.2, "xp_if_appears": None, "p_appear": None, "xmins": None,
        "as_of": AS_OF,
    }])
    frame["as_of"] = pd.to_datetime(frame["as_of"], utc=True)
    for col in ("xp", "xp_if_appears", "p_appear", "xmins"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    return frame


def _good(provider: str, code: int):
    def step(warehouse, store, season, *, first_gw, last_gw):
        n = store.append("fact_projection", _row(provider, code))
        return cli.StepResult(provider=provider, ok=True, rows=n, parsed=1,
                              unresolved=0, detail="stub")
    return step


def _explodes(provider: str, exc: Exception):
    def step(warehouse, store, season, *, first_gw, last_gw):
        raise exc
    return step


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "iso.duckdb"
    with Warehouse(path) as warehouse:
        ProjectionStore(warehouse)
    return path


def _run(monkeypatch, db, steps):
    monkeypatch.setattr(cli, "_ingest_fplform", steps[0])
    monkeypatch.setattr(cli, "_ingest_livefpl", steps[1])
    monkeypatch.setattr(cli, "_ingest_fpl_ep", steps[2])
    monkeypatch.setattr(cli, "_ingest_rotowire", steps[3])
    monkeypatch.setattr(cli, "_ingest_premierinjuries", steps[4])
    monkeypatch.setattr(cli.github_csv, "FEEDS", ())
    return cli.ingest("2026-27", db=str(db))


def test_one_dead_provider_never_costs_the_others_their_rows(monkeypatch, db):
    results = _run(monkeypatch, db, [
        _good("fplform", 1),
        _explodes("livefpl", ConnectionError("connection reset by peer")),
        _good("fpl_ep", 3),
        _explodes("rotowire", ValueError("9 starters parsed, expected 11")),
        _good("premierinjuries", 5),
    ])
    assert [k for k, r in results.items() if r.ok] == [
        "fplform", "fpl_ep", "premierinjuries"
    ]
    assert not results["livefpl"].ok
    assert not results["rotowire"].ok
    with Warehouse(db) as warehouse:
        landed = warehouse.sql("SELECT provider FROM fact_projection ORDER BY provider")
    assert list(landed["provider"]) == ["fpl_ep", "fplform", "premierinjuries"]


def test_a_failure_records_its_real_error_and_never_fabricates_rows(monkeypatch, db):
    results = _run(monkeypatch, db, [
        _explodes("fplform", TimeoutError("timed out after 90s")),
        _good("livefpl", 2), _good("fpl_ep", 3),
        _good("rotowire", 4), _good("premierinjuries", 5),
    ])
    failed = results["fplform"]
    assert failed.ok is False
    assert "TimeoutError" in failed.error
    assert "timed out after 90s" in failed.error
    # A failure is 0 rows, not a zero, not a stale copy, not an interpolation.
    assert failed.rows == 0
    with Warehouse(db) as warehouse:
        n = warehouse.sql(
            "SELECT count(*) c FROM fact_projection WHERE provider = 'fplform'"
        ).iloc[0]["c"]
    assert n == 0


def test_every_provider_failing_is_the_only_non_zero_exit(monkeypatch, db):
    steps = [_explodes(name, RuntimeError("down"))
             for name in ("fplform", "livefpl", "fpl_ep", "rotowire",
                          "premierinjuries")]
    monkeypatch.setattr(cli, "_ingest_fplform", steps[0])
    monkeypatch.setattr(cli, "_ingest_livefpl", steps[1])
    monkeypatch.setattr(cli, "_ingest_fpl_ep", steps[2])
    monkeypatch.setattr(cli, "_ingest_rotowire", steps[3])
    monkeypatch.setattr(cli, "_ingest_premierinjuries", steps[4])
    monkeypatch.setattr(cli.github_csv, "FEEDS", ())
    assert cli.main(["ingest", "--db", str(db)]) == 1


def test_a_partial_run_still_exits_zero(monkeypatch, db):
    """Four of five sources is a successful deadline run, not a failed job.

    Exiting non-zero here would page someone every time one site had a wobble,
    and the alert would be ignored by November.
    """
    monkeypatch.setattr(cli, "_ingest_fplform", _good("fplform", 1))
    monkeypatch.setattr(cli, "_ingest_livefpl", _explodes("livefpl", OSError("nope")))
    monkeypatch.setattr(cli, "_ingest_fpl_ep", _good("fpl_ep", 3))
    monkeypatch.setattr(cli, "_ingest_rotowire", _good("rotowire", 4))
    monkeypatch.setattr(cli, "_ingest_premierinjuries", _good("premierinjuries", 5))
    monkeypatch.setattr(cli.github_csv, "FEEDS", ())
    assert cli.main(["ingest", "--db", str(db)]) == 0


def test_the_run_is_idempotent_across_invocations(monkeypatch, db):
    steps = [_good("fplform", 1), _good("livefpl", 2), _good("fpl_ep", 3),
             _good("rotowire", 4), _good("premierinjuries", 5)]
    first = _run(monkeypatch, db, steps)
    assert sum(r.rows for r in first.values()) == 5
    second = _run(monkeypatch, db, steps)
    assert sum(r.rows for r in second.values()) == 0
    assert all(r.ok for r in second.values()), "0 appended is success, not failure"
    with Warehouse(db) as warehouse:
        n = warehouse.sql("SELECT count(*) c FROM fact_projection").iloc[0]["c"]
    assert n == 5


def test_only_filters_to_named_providers(monkeypatch, db):
    monkeypatch.setattr(cli, "_ingest_fplform", _good("fplform", 1))
    monkeypatch.setattr(cli, "_ingest_livefpl",
                        _explodes("livefpl", AssertionError("must not run")))
    monkeypatch.setattr(cli.github_csv, "FEEDS", ())
    results = cli.ingest("2026-27", db=str(db), only=("fplform",))
    assert set(results) == {"fplform"}


def test_an_unknown_provider_name_is_refused_not_ignored(monkeypatch, db):
    monkeypatch.setattr(cli.github_csv, "FEEDS", ())
    with pytest.raises(SystemExit, match="nosuchsource"):
        cli.ingest("2026-27", db=str(db), only=("nosuchsource",))


def test_every_github_feed_gets_its_own_isolated_step():
    """Feeds are wired one step each, so one repo 404ing does not take the rest."""
    from fpl_edge.ingest.projections import github_csv

    keys = [f.key for f in github_csv.FEEDS]
    assert len(keys) == len(set(keys)), "feed keys must be unique"
    for key in keys:
        assert callable(cli._github_step(key))
