"""The on-disk format and the open/ -> resolved/ lifecycle."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from fpl_edge.theses.model import (
    ClaimType,
    Thesis,
    ThesisOutcome,
    ThesisSource,
    ThesisStatus,
    make_thesis_id,
)
from fpl_edge.theses.store import DuplicateThesisError, ThesesStore

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 8, 18, 23, 5, tzinfo=UTC)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "theses" / "sample.md"


def _thesis(**overrides) -> Thesis:
    base = dict(
        id="2026-08-18-hero-buy",
        created=T0,
        source=ThesisSource.USER_CHAT,
        raw_input="I like Hero",
        player="Hero",
        player_code=101,
        season="2026-27",
        claim_type=ClaimType.BUY,
        gw_start=1,
        horizon_gws=6,
        falsifiable_prediction="outscores positional price-peer median over GW1-GW6",
        comparator_codes=(201, 202, 203, 204),
        comparator_label="4 peers",
        model_verdict_at_creation={"as_of": T0, "price": 7.0, "xpts": 4.2},
        prose="Reasoning goes here.",
    )
    base.update(overrides)
    return Thesis(**base)


def test_the_committed_sample_file_parses():
    thesis = Thesis.from_markdown(FIXTURE.read_text())
    assert thesis.id == "2026-08-18-rashford-minutes"
    assert thesis.player_code == 176297  # the stable cross-season code
    assert thesis.claim_type is ClaimType.MINUTES
    assert thesis.falsifiable_prediction == "starts in 4+ of GW1-GW6"
    assert thesis.gw_end == 6
    assert thesis.model_verdict_at_creation["is_supported_club"] is True
    assert thesis.status is ThesisStatus.OPEN


def test_roundtrip_is_exact_including_nested_verdict():
    t = _thesis()
    again = Thesis.from_markdown(t.to_markdown())
    assert again == t


def test_inconsistent_window_is_refused():
    text = FIXTURE.read_text().replace("gw_end: 6", "gw_end: 9")
    with pytest.raises(ValueError, match="inconsistent window"):
        Thesis.from_markdown(text)


def test_naive_created_is_refused():
    with pytest.raises(ValueError, match="timezone-aware"):
        _thesis(created=dt.datetime(2026, 8, 18, 23, 5))


def test_write_load_move_lifecycle(tmp_path):
    store = ThesesStore(tmp_path / "theses")
    t = _thesis()
    path = store.write_open(t)
    assert path == store.open_dir / "2026-08-18-hero-buy.md"
    assert [x.id for x, _ in store.load_open()] == [t.id]

    with pytest.raises(DuplicateThesisError):
        store.write_open(t)

    done = t.resolved(
        outcome=ThesisOutcome.CORRECT,
        resolved_utc=dt.datetime(2026, 10, 20, 9, tzinfo=UTC),
        subject_points=30.0, comparator_points=12.0, margin=18.0,
        detail="30 vs 12", counterfactual="worth +18.0",
    )
    old, new = store.move_resolved(done)
    assert not old.exists() and new.exists()
    assert store.load_open() == []

    resolved = store.load_resolved()[0][0]
    assert resolved.status is ThesisStatus.RESOLVED
    assert resolved.outcome is ThesisOutcome.CORRECT
    # The creation block survived resolution byte-for-byte in value terms.
    assert resolved.model_verdict_at_creation == t.model_verdict_at_creation
    assert resolved.created == t.created
    assert resolved.falsifiable_prediction == t.falsifiable_prediction


def test_mismatched_filename_is_an_error(tmp_path):
    store = ThesesStore(tmp_path / "theses")
    store.ensure_layout()
    (store.open_dir / "wrong-name.md").write_text(_thesis().to_markdown())
    with pytest.raises(ValueError, match="does not match the"):
        store.load_open()


def test_unique_id_appends_a_counter(tmp_path):
    store = ThesesStore(tmp_path / "theses")
    store.write_open(_thesis())
    wanted = make_thesis_id(T0, "Hero", ClaimType.BUY)
    assert wanted == "2026-08-18-hero-buy"
    assert store.unique_id(wanted) == "2026-08-18-hero-buy-2"


def test_watch_thesis_serialises_without_prediction(tmp_path):
    t = _thesis(
        claim_type=ClaimType.WATCH, falsifiable_prediction=None,
        comparator_codes=(), comparator_label="",
        prose="One to keep an eye on; no falsifiable claim yet.",
    )
    again = Thesis.from_markdown(t.to_markdown())
    assert again.falsifiable_prediction is None
    assert again.claim_type is ClaimType.WATCH
