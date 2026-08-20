"""The warehouse side: idempotency, contradiction refusal, the normalised view.

These tests build a real DuckDB file under ``tmp_path``. They never touch the
project warehouse -- the live Telegram bot holds write leases on it, and a test
that took that lock would either block for a minute or fail a user's message.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.ingest.projections.store import (
    ConflictingProjectionError,
    ProjectionStore,
)
from fpl_edge.store import Warehouse

AS_OF = dt.datetime(2026, 8, 20, 6, 30, tzinfo=dt.timezone.utc)
LATER = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def store(tmp_path):
    with Warehouse(tmp_path / "proj.duckdb") as warehouse:
        yield ProjectionStore(warehouse)


def projection(**over) -> pd.DataFrame:
    row = {"provider": "fplform", "season": "2026-27", "gw": 1, "code": 154561,
           "xp": 4.2, "xp_if_appears": 4.4, "p_appear": 0.95, "xmins": None,
           "as_of": AS_OF}
    row.update(over)
    frame = pd.DataFrame([row])
    frame["as_of"] = pd.to_datetime(frame["as_of"], utc=True)
    for col in ("xp", "xp_if_appears", "p_appear", "xmins"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    return frame


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def test_migrations_run_once_and_report_themselves(tmp_path):
    with Warehouse(tmp_path / "m.duckdb") as warehouse:
        first = ProjectionStore(warehouse)
        assert "001_projections" in first.applied_migrations
        assert "003_xmins" in first.applied_migrations
        # Constructing a second store against the same file applies nothing.
        assert ProjectionStore(warehouse).applied_migrations == []


def test_the_normalised_view_speaks_the_platform_contract(store):
    store.append("fact_projection", projection(xmins=78.4))
    frame = store.wh.sql("SELECT * FROM projection_normalized")
    for column in ("source", "player_code", "gw", "xmins", "xpts", "fetched_at"):
        assert column in frame.columns, column
    row = frame.iloc[0]
    assert row["source"] == "fplform"
    assert row["player_code"] == 154561
    assert row["xpts"] == pytest.approx(4.2)
    assert row["xmins"] == pytest.approx(78.4)
    assert row["fetched_at"] == pd.Timestamp(AS_OF)


def test_xmins_and_p_appear_are_separate_columns(store):
    """A minutes expectation and an appearance probability are not the same.

    Squashing them would erase the difference between "we expect 45 minutes"
    and "there is a 50% chance he features", which are different claims about
    different random variables and fail differently.
    """
    store.append("fact_projection", projection(provider="a", xmins=45.0, p_appear=None))
    store.append("fact_projection", projection(provider="b", xmins=None, p_appear=0.5))
    frame = store.wh.sql(
        "SELECT source, xmins, p_appear FROM projection_normalized ORDER BY source"
    )
    assert frame.iloc[0]["xmins"] == 45.0 and pd.isna(frame.iloc[0]["p_appear"])
    assert pd.isna(frame.iloc[1]["xmins"]) and frame.iloc[1]["p_appear"] == 0.5


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


def test_re_ingesting_the_same_fetch_appends_nothing(store):
    rows = projection()
    assert store.append("fact_projection", rows) == 1
    assert store.append("fact_projection", rows) == 0
    assert store.append("fact_projection", rows) == 0
    assert store.wh.sql("SELECT count(*) c FROM fact_projection").iloc[0]["c"] == 1


def test_replaying_an_archived_body_is_safe(store):
    """The raw archive exists so the warehouse can be rebuilt from it.

    That only works if replaying a body under its ORIGINAL fetch instant is a
    no-op rather than a duplicate. A rebuild that doubled every row would make
    the archive useless for the thing it was archived for.
    """
    batch = pd.concat([projection(code=c) for c in (1, 2, 3)], ignore_index=True)
    assert store.append("fact_projection", batch) == 3
    assert store.append("fact_projection", batch) == 0
    # A partially-overlapping replay adds only the new rows.
    wider = pd.concat([projection(code=c) for c in (2, 3, 4)], ignore_index=True)
    assert store.append("fact_projection", wider) == 1


def test_a_later_fetch_of_a_revised_projection_is_a_new_row(store):
    """as_of is the FETCH instant, so a revision is history, not an overwrite.

    Reading "the projection as it stood at the deadline" has to mean the last
    one we actually fetched before it, and that is only possible if the earlier
    number is still there.
    """
    store.append("fact_projection", projection(xp=4.2))
    store.append("fact_projection", projection(xp=5.8, as_of=LATER))
    assert store.wh.sql("SELECT count(*) c FROM fact_projection").iloc[0]["c"] == 2
    at_first = store.as_of("fact_projection", AS_OF)
    assert at_first.iloc[0]["xp"] == pytest.approx(4.2)
    at_second = store.as_of("fact_projection", LATER)
    assert at_second.iloc[0]["xp"] == pytest.approx(5.8)
    assert len(at_second) == 1, "as_of returns the latest row per entity, not both"


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


def test_two_different_values_at_one_instant_are_refused(store):
    store.append("fact_projection", projection(xp=4.2))
    with pytest.raises(ConflictingProjectionError, match="later as_of"):
        store.append("fact_projection", projection(xp=9.9))


def test_naive_timestamps_are_refused_rather_than_assumed_utc(store):
    rows = projection()
    rows["as_of"] = pd.to_datetime(rows["as_of"]).dt.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        store.append("fact_projection", rows)


def test_an_unknown_table_is_refused(store):
    with pytest.raises(KeyError):
        store.append("fact_not_ours", projection())


def test_as_of_read_refuses_a_naive_instant(store):
    with pytest.raises(ValueError, match="timezone-aware"):
        store.as_of("fact_projection", dt.datetime(2026, 8, 20, 6, 30))


def test_providers_summary_counts_each_source_separately(store):
    store.append("fact_projection", projection(provider="fplform"))
    store.append("fact_projection", projection(provider="gh_fplbench"))
    store.append("fact_projection", projection(provider="fpl_ep", code=1))
    summary = store.providers().set_index("provider")
    assert sorted(summary.index) == ["fpl_ep", "fplform", "gh_fplbench"]
    assert summary.loc["fplform", "rows"] == 1
