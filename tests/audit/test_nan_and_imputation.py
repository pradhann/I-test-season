"""Silent NaN filling and leaky imputation.

Hunt list item 5. The bug is not ``fillna``. The bug is a fabricated value that
is indistinguishable from an observed one downstream. Three shapes:

* a rate statistic filled with 0, so "we do not know his ownership" becomes
  "nobody owns him", which is a strong and wrong signal;
* an imputation computed over the whole frame, which carries the evaluation
  period's mean backwards into training;
* ``dropna`` removing the rows that are hard, which are the rows that matter.
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd

from .conftest import UTC, frame, state_row

AS_OF = dt.datetime(2026, 8, 18, tzinfo=UTC)


def test_missing_ownership_is_not_ingested_as_zero_percent() -> None:
    """GUARDS: fpl_edge/ingest/fpl_api.py:92 turning unknown ownership into 0.0%.

    ``float(el.get("selected_by_percent") or 0.0)`` collapses three distinct
    states into one number: absent field, empty string, and a genuine 0.0%.

    Ownership is the input to the entire rank-utility objective. A player whose
    ownership failed to parse is scored as a 0%-owned differential -- the most
    aggressive possible reading -- rather than as unknown. And ``or 0.0`` is
    doubly unsafe because it also fires on the string ``"0.0"``... which is
    fine, and on ``"0"``, and on any falsy value the API ever starts sending.
    """
    from fpl_edge.ingest.fpl_api import ingest_bootstrap
    from fpl_edge.store import Warehouse

    payload = _bootstrap_with(selected_by_percent=None)
    wh = Warehouse(":memory:")
    ingest_bootstrap(wh, _StubFetcher(payload))

    got = wh.sql("SELECT selected_by_pct FROM fact_player_state")
    value = got.iloc[0]["selected_by_pct"]
    assert value is None or (isinstance(value, float) and np.isnan(value)), (
        f"a missing selected_by_percent was stored as {value!r}. NULL means "
        "'unknown'; 0.0 means 'nobody owns him', and the objective treats "
        "those very differently"
    )


def test_zero_ownership_and_unknown_ownership_are_distinguishable() -> None:
    """GUARDS: the same collapse from the other side.

    A genuine 0.0% and a missing value must not produce identical rows, or no
    downstream code can ever tell them apart.
    """
    from fpl_edge.ingest.fpl_api import ingest_bootstrap
    from fpl_edge.store import Warehouse

    wh_zero = Warehouse(":memory:")
    ingest_bootstrap(wh_zero, _StubFetcher(_bootstrap_with(selected_by_percent="0.0")))
    wh_missing = Warehouse(":memory:")
    ingest_bootstrap(wh_missing, _StubFetcher(_bootstrap_with(selected_by_percent=None)))

    a = wh_zero.sql("SELECT selected_by_pct FROM fact_player_state").iloc[0]["selected_by_pct"]
    b = wh_missing.sql("SELECT selected_by_pct FROM fact_player_state").iloc[0]["selected_by_pct"]
    assert not (a == b == 0.0), (
        "an observed 0.0% and an unobservable ownership are stored identically "
        "as 0.0; the distinction is destroyed at ingest and cannot be recovered"
    )


def test_null_rate_survives_a_snapshot_round_trip(wh) -> None:
    """GUARDS: the warehouse itself coercing a NULL rate to zero.

    Regression guard, currently correct: the store preserves NULL. If this ever
    fails, no amount of care at ingest can help.
    """
    wh.append("fact_player_state", frame([
        state_row(season="2026-27", code=1, element_id=1, as_of=AS_OF,
                  selected_by_pct=None),
    ]))
    got = wh.snapshot_at(AS_OF + dt.timedelta(days=1)).table("fact_player_state")
    assert pd.isna(got.iloc[0]["selected_by_pct"])


def test_whole_panel_imputation_is_measurably_leaky() -> None:
    """QUANTIFIES the hazard that scripts/audit_leakage.py LEAKY_IMPUTE guards.

    A demonstration, not an accusation: no current module does this. It is here
    so the size of the effect is on record and the static rule is not mistaken
    for pedantry. Filling an early-season blank with the mean of a panel that
    includes the second half injects the breakout the model was supposed to
    predict.
    """
    rng = np.random.default_rng(0)
    early = rng.normal(2.0, 0.5, 100)   # first half of the season
    late = rng.normal(8.0, 0.5, 100)    # a breakout second half
    panel = np.concatenate([early, late])

    leaky = float(panel.mean())         # imputer fitted on everything
    honest = float(early.mean())        # imputer fitted on what GW1 could see

    assert leaky - honest > 2.5, (
        "expected whole-panel imputation to visibly overstate an early-season "
        f"blank; got {leaky:.2f} vs {honest:.2f}"
    )
    assert abs(honest - 2.0) < 0.2, "training-fold mean should track the early regime"


def test_dropna_on_fpl_data_selects_a_biased_subsample() -> None:
    """QUANTIFIES the hazard that scripts/audit_leakage.py SILENT_DROPNA guards.

    Missingness in FPL data is emphatically not random.
    ``chance_of_playing_next_round`` is NULL for every fit player and populated
    only for the flagged ones, so a bare ``dropna()`` over a feature frame keeps
    ONLY the injury-doubt rows. The surviving sample is not the population the
    model is scored on, and every metric computed on it is measuring a
    different, much harder problem than the one being solved.
    """
    df = pd.DataFrame({
        "code": [1, 2, 3, 4],
        "status": ["a", "a", "d", "i"],
        "chance_of_playing_next_round": [None, None, 50.0, 0.0],
        "minutes": [90, 88, 20, 0],
    })
    kept = df.dropna()
    assert set(kept["status"]) == {"d", "i"}, (
        "dropna() should retain exactly the flagged players here; if the shape "
        "of FPL missingness has changed, the SILENT_DROPNA rule needs revisiting"
    )
    assert len(kept) == 2 and kept["minutes"].mean() < df["minutes"].mean() / 2, (
        "the surviving subsample has half the rows and a quarter of the minutes"
    )


def test_minutes_features_do_not_assume_an_unknown_player_is_fit() -> None:
    """GUARDS: fpl_edge/models/minutes/features.py filling status with 'a'.

    ``out["status"].fillna("a")`` reads an absent availability as AVAILABLE.
    Every derived flag -- ``status_flagged``, ``status_injured``,
    ``status_doubtful`` -- then reports the optimistic answer for a player the
    model knows nothing about, which is the direction that loses points: the
    optimizer buys him.

    ``days_rest.fillna(7.0)`` in the same file invents a full week of rest for a
    player whose previous fixture is unknown.
    """
    import inspect

    from fpl_edge.models.minutes import features

    source = inspect.getsource(features)
    assert 'fillna("a")' not in source and "fillna('a')" not in source, (
        "status.fillna('a') treats an unknown availability as fit; an unknown "
        "should propagate as unknown so the caller decides"
    )


def test_bare_dropna_is_absent_from_model_training_paths() -> None:
    """GUARDS: a silent row filter inside a model.

    Delegates to the static audit so this stays true as new model modules land.
    Interfaces-layer dropna over summary columns is reviewed and baselined in
    tests/audit/test_static_leakage_audit.py.
    """
    from .conftest import REPO_ROOT, load_audit_script

    audit = load_audit_script()
    offenders = [
        f for f in audit.audit_tree(REPO_ROOT)
        if f.rule in {"SILENT_DROPNA", "LEAKY_IMPUTE"}
        and f.path.startswith("fpl_edge/models/")
        and "/evaluate" not in f.path  # calibration bins, not training rows
    ]
    assert not offenders, (
        "a model training path silently drops or imputes rows:\n  "
        + "\n  ".join(f.render() for f in offenders)
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bootstrap_with(**element_overrides) -> dict:
    """A minimal but structurally real bootstrap-static payload."""
    element = {
        "id": 1, "code": 204480, "web_name": "Rice", "first_name": "Declan",
        "second_name": "Rice", "element_type": 3, "team": 1,
        "now_cost": 65, "selected_by_percent": "12.3", "status": "a",
        "chance_of_playing_next_round": None, "news": "", "news_added": None,
        "transfers_in_event": 0, "transfers_out_event": 0, "cost_change_start": 0,
    }
    element.update(element_overrides)
    return {
        "events": [{"id": 1, "deadline_time": "2026-08-21T17:30:00Z", "finished": False}],
        "teams": [{"id": 1, "code": 3, "name": "Arsenal", "short_name": "ARS"}],
        "elements": [element],
    }


class _StubFetcher:
    """Stands in for ``ingest.http.Fetcher`` without touching the network."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def get_json(self, endpoint: str, params=None):
        from pathlib import Path

        from fpl_edge.ingest.http import Fetched

        return Fetched(
            body=self.payload,
            fetched_at=AS_OF,
            sha256=json.dumps(self.payload, sort_keys=True),
            body_path=Path("/dev/null"),
            http_status=200,
            from_cache=True,
        )

    def close(self) -> None:
        pass
