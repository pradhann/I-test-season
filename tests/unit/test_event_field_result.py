"""The field's own result for a gameweek, on ``dim_event``.

FPL publishes ``average_entry_score`` in every bootstrap-static poll and the
warehouse persisted it nowhere, so "did I beat the field" had no field to beat.
The creator report card reported it as a named gap and fell back on comparing
creators against each other; the dashboard could not say where the season
stood at all.

The one trap worth a test of its own: FPL reports ``average_entry_score: 0``
for a gameweek that has not been played. Storing that zero would tell every
reader the field scored nothing, so an unfinished event stores NULL.
"""

from __future__ import annotations

import datetime as dt

UTC = dt.UTC


AS_OF = dt.datetime(2026, 9, 8, tzinfo=UTC)


class _StubFetcher:
    """Stands in for ingest.http.Fetcher without touching the network."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def get_json(self, endpoint: str, params=None):
        import json
        from pathlib import Path

        from fpl_edge.ingest.http import Fetched

        return Fetched(
            body=self.payload, fetched_at=AS_OF,
            sha256=json.dumps(self.payload, sort_keys=True),
            body_path=Path("/dev/null"), http_status=200, from_cache=True,
        )


def _payload(events: list[dict]) -> dict:
    return {
        "events": events,
        "teams": [{"id": 1, "code": 3, "name": "Arsenal", "short_name": "ARS"}],
        "elements": [{
            "id": 1, "code": 204480, "web_name": "Rice",
            "first_name": "Declan", "second_name": "Rice", "element_type": 3,
            "team": 1, "now_cost": 65, "selected_by_percent": "12.3",
            "status": "a", "chance_of_playing_next_round": None, "news": "",
            "news_added": None, "transfers_in_event": 0,
            "transfers_out_event": 0, "cost_change_start": 0,
        }],
    }


def _events(wh):
    return wh.sql(
        "SELECT gw, is_finished, avg_entry_score, highest_score, ranked_count "
        "FROM dim_event ORDER BY gw"
    )


def test_a_settled_gameweek_stores_the_published_field_result() -> None:
    from fpl_edge.ingest.fpl_api import ingest_bootstrap
    from fpl_edge.store import Warehouse

    wh = Warehouse(":memory:")
    ingest_bootstrap(wh, _StubFetcher(_payload([{
        "id": 1, "deadline_time": "2026-08-14T17:30:00Z", "finished": True,
        "average_entry_score": 50, "highest_score": 131,
        "ranked_count": 8903411,
    }])))
    row = _events(wh).iloc[0]
    assert int(row["avg_entry_score"]) == 50
    assert int(row["highest_score"]) == 131
    assert int(row["ranked_count"]) == 8903411


def test_an_unplayed_gameweek_stores_null_not_the_apis_zero() -> None:
    """FPL sends 0 before a gameweek is played. Persisting it would give every
    "versus the field" comparison a baseline of nothing."""
    import pandas as pd

    from fpl_edge.ingest.fpl_api import ingest_bootstrap
    from fpl_edge.store import Warehouse

    wh = Warehouse(":memory:")
    ingest_bootstrap(wh, _StubFetcher(_payload([{
        "id": 5, "deadline_time": "2026-09-12T12:30:00Z", "finished": False,
        "average_entry_score": 0, "highest_score": None, "ranked_count": 0,
    }])))
    row = _events(wh).iloc[0]
    for col in ("avg_entry_score", "highest_score", "ranked_count"):
        v = row[col]
        assert pd.isna(v), (
            f"{col} stored {v!r} for an unplayed gameweek. NULL means 'not "
            f"published yet'; 0 means 'the field scored nothing'"
        )


def test_the_fresh_schema_and_the_migration_agree_on_column_order() -> None:
    """ALTER TABLE ADD COLUMN appends, so a warehouse that gained these by
    migration has them last. A fresh warehouse must match, or a positional
    INSERT means two different things depending on which machine ran it."""
    from fpl_edge.store import Warehouse

    wh = Warehouse(":memory:")
    cols = list(wh.sql("DESCRIBE dim_event")["column_name"])
    assert cols == ["season", "gw", "deadline_utc", "is_finished", "as_of",
                    "avg_entry_score", "highest_score", "ranked_count"]
