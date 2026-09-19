"""POST /api/transcripts and GET /api/transcripts/queue. DEPLOYMENT.md §1.4-§1.7.

The property that matters most here is the last one: a transcript the Mac
pushes must leave rows identical to a transcript the local command writes. If
the two paths drift, the corpus acquires two kinds of transcript and
every provenance question after that has two answers. So the last test in this
file writes the same transcription twice, once through
``asr.store_transcription`` directly and once through the route, and compares
the segment and provenance rows column by column.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from fpl_edge.ingest.content import asr
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.platform.app import routes_transcripts as rt
from fpl_edge.platform.app.factory import create_app
from fpl_edge.store import Warehouse

UTC = dt.UTC
TOKEN = "primary-token-value"
NEXT_TOKEN = "rotation-token-value"


def _item(wh, item_id: str, *, kind: str = "podcast", text: str = "show notes",
          creator: str = "FPL Harry", source_key: str = "pod_x") -> None:
    published = dt.datetime.now(UTC) - dt.timedelta(days=1)
    wh.sql(
        "INSERT INTO content_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [item_id, source_key, creator, kind, f"{item_id} title",
         f"https://example.test/{item_id}", published, published,
         "description", text,
         hashlib.sha256(text.encode()).hexdigest()],
    )


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    with Warehouse(path) as wh:
        ContentStore(wh)
        asr.ensure_schema(wh)
        _item(wh, "item_one")
        _item(wh, "item_two")
    return path


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setenv(rt.TOKEN_ENV, TOKEN)
    monkeypatch.delenv(rt.TOKEN_NEXT_ENV, raising=False)
    # config.secret falls back to .env, which must not decide a test's answer.
    monkeypatch.setattr("fpl_edge.config.load_env", lambda *a, **k: {})
    return TestClient(create_app(db))


def _transcription(*, text: str = "first half then second half",
                   engine: str = "mlx-whisper",
                   model: str = "mlx-community/whisper-large-v3-turbo"):
    words = text.split()
    half = max(1, len(words) // 2)
    return asr.Transcription(
        segments=(
            asr.Segment(seq=0, start_s=0.0, end_s=30.0,
                        text=" ".join(words[:half])),
            asr.Segment(seq=1, start_s=30.0, end_s=61.5,
                        text=" ".join(words[half:])),
        ),
        model=model, engine=engine, language="en",
        audio_seconds=62.0, covered_seconds=61.5, wall_seconds=5.25,
        audio_sha256="a" * 64, audio_bytes=123456,
        audio_url="https://example.test/item_one.mp3",
        created_utc=dt.datetime(2026, 9, 17, 12, 0, tzinfo=UTC),
    )


def _body(transcription, *, item_id: str = "item_one",
          derivation: str = "asr", replace: bool = False) -> dict:
    return {
        "item_id": item_id,
        "derivation": derivation,
        "engine": transcription.engine,
        "model": transcription.model,
        "language": transcription.language,
        "audio_url": transcription.audio_url,
        "audio_sha256": transcription.audio_sha256,
        "audio_bytes": transcription.audio_bytes,
        "audio_seconds": transcription.audio_seconds,
        "covered_seconds": transcription.covered_seconds,
        "wall_seconds": transcription.wall_seconds,
        "created_utc": transcription.created_utc.isoformat(),
        "replace": replace,
        "segments": [
            {"seq": s.seq, "start_s": s.start_s, "end_s": s.end_s,
             "text": s.text}
            for s in transcription.segments
        ],
    }


def _auth(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


# -- auth --------------------------------------------------------------------


def test_a_push_with_no_bearer_is_401_and_says_nothing_else(client):
    r = client.post("/api/transcripts", json=_body(_transcription()))
    assert r.status_code == 401
    # The body must not reveal whether the item exists: that would make an
    # unauthenticated caller an existence oracle over the corpus.
    assert r.json() == {"error": "unauthorized"}


def test_a_push_with_the_wrong_bearer_is_401(client):
    r = client.post("/api/transcripts", json=_body(_transcription()),
                    headers=_auth("not-the-token"))
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


def test_an_unknown_item_and_a_bad_token_are_indistinguishable(client):
    """Both answer 401 with the same body, so the 404 leaks nothing."""
    bad_token = client.post("/api/transcripts",
                            json=_body(_transcription(), item_id="nope"),
                            headers=_auth("not-the-token"))
    no_token = client.post("/api/transcripts",
                           json=_body(_transcription(), item_id="item_one"))
    assert bad_token.status_code == no_token.status_code == 401
    assert bad_token.json() == no_token.json()


def test_the_rotation_slot_is_accepted_alongside_the_primary(client, monkeypatch):
    """Two accepted values during the window, so the two sides never have to
    restart together."""
    monkeypatch.setenv(rt.TOKEN_NEXT_ENV, NEXT_TOKEN)
    r = client.post("/api/transcripts", json=_body(_transcription()),
                    headers=_auth(NEXT_TOKEN))
    assert r.status_code == 200, r.text
    assert r.json()["stored"] is True


def test_a_server_with_no_token_configured_accepts_nothing(client, monkeypatch):
    monkeypatch.delenv(rt.TOKEN_ENV, raising=False)
    monkeypatch.delenv(rt.TOKEN_NEXT_ENV, raising=False)
    r = client.post("/api/transcripts", json=_body(_transcription()),
                    headers=_auth())
    assert r.status_code == 401


def test_the_queue_route_takes_the_same_bearer(client):
    assert client.get("/api/transcripts/queue").status_code == 401
    assert client.get("/api/transcripts/queue",
                      headers=_auth()).status_code == 200


# -- size cap ----------------------------------------------------------------


def test_a_body_over_the_cap_is_413(client, monkeypatch):
    monkeypatch.setattr(rt, "MAX_BODY_BYTES", 2048)
    fat = _body(_transcription())
    fat["segments"] = [
        {"seq": i, "start_s": float(i), "end_s": float(i + 1), "text": "x" * 200}
        for i in range(100)
    ]
    r = client.post("/api/transcripts", json=fat, headers=_auth())
    assert r.status_code == 413
    assert "cap" in r.json()["detail"]


def test_a_declared_content_length_over_the_cap_is_refused_before_the_read(
        client, monkeypatch):
    monkeypatch.setattr(rt, "MAX_BODY_BYTES", 100)
    r = client.post("/api/transcripts",
                    content=json.dumps(_body(_transcription())),
                    headers={**_auth(), "Content-Type": "application/json"})
    assert r.status_code == 413


# -- refusals that are not about auth ----------------------------------------


def test_an_orphan_transcript_is_404(client):
    r = client.post("/api/transcripts",
                    json=_body(_transcription(), item_id="not_an_item"),
                    headers=_auth())
    assert r.status_code == 404
    assert "not_an_item" in r.json()["detail"]


def test_zero_segments_is_400_and_writes_nothing(client, db):
    body = _body(_transcription())
    body["segments"] = []
    r = client.post("/api/transcripts", json=body, headers=_auth())
    assert r.status_code == 400
    with Warehouse(db, read_only=True) as wh:
        assert wh.sql("SELECT * FROM transcript_segment").empty


def test_blank_text_is_400(client):
    body = _body(_transcription())
    for seg in body["segments"]:
        seg["text"] = "   "
    r = client.post("/api/transcripts", json=body, headers=_auth())
    assert r.status_code == 400


# -- idempotency -------------------------------------------------------------


def test_a_first_push_stores_and_promotes_the_text(client, db):
    transcription = _transcription()
    r = client.post("/api/transcripts", json=_body(transcription),
                    headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored"] is True
    assert body["segments"] == 2
    with Warehouse(db, read_only=True) as wh:
        row = wh.sql("SELECT text_source, text_sha256 FROM content_item "
                     "WHERE item_id = 'item_one'").iloc[0]
    assert row["text_source"] == "transcript"
    assert row["text_sha256"] == hashlib.sha256(
        transcription.text.encode()).hexdigest()


def test_the_same_push_twice_is_a_no_op(client, db):
    """The key is (item_id, sha256(joined segment text)). A retry after a
    dropped connection, a duplicate worker run and a worker that lost its
    bookkeeping all land here and change nothing."""
    payload = _body(_transcription())
    first = client.post("/api/transcripts", json=payload, headers=_auth())
    assert first.json()["stored"] is True
    with Warehouse(db, read_only=True) as wh:
        before = wh.sql("SELECT * FROM transcript_provenance "
                        "WHERE item_id = 'item_one'").iloc[0].to_dict()

    second = client.post("/api/transcripts", json=payload, headers=_auth())
    assert second.status_code == 200
    assert second.json() == {
        "stored": False,
        "item_id": "item_one",
        "reason": "identical transcript already stored",
        "text_sha256": hashlib.sha256(_transcription().text.encode()).hexdigest(),
    }
    with Warehouse(db, read_only=True) as wh:
        after = wh.sql("SELECT * FROM transcript_provenance "
                       "WHERE item_id = 'item_one'").iloc[0].to_dict()
    assert str(before) == str(after), "the no-op rewrote the provenance row"


def test_a_different_transcript_without_replace_is_409(client, db):
    client.post("/api/transcripts", json=_body(_transcription()),
                headers=_auth())
    other = _transcription(text="a completely different reading of the episode")
    r = client.post("/api/transcripts", json=_body(other), headers=_auth())
    assert r.status_code == 409
    body = r.json()
    assert body["stored"] is False
    assert body["stored_text_sha256"] != body["pushed_text_sha256"]
    with Warehouse(db, read_only=True) as wh:
        stored = wh.sql("SELECT text FROM content_item "
                        "WHERE item_id = 'item_one'").iloc[0]["text"]
    assert stored == _transcription().text, "the 409 overwrote the transcript"


def test_replace_true_overwrites_and_records_the_prior_hash(client, db):
    client.post("/api/transcripts", json=_body(_transcription()),
                headers=_auth())
    first_hash = hashlib.sha256(_transcription().text.encode()).hexdigest()
    other = _transcription(text="a completely different reading of the episode")
    r = client.post("/api/transcripts", json=_body(other, replace=True),
                    headers=_auth())
    assert r.status_code == 200, r.text
    assert r.json()["stored"] is True
    with Warehouse(db, read_only=True) as wh:
        prov = wh.sql("SELECT prior_text_source, prior_text_sha256 "
                      "FROM transcript_provenance "
                      "WHERE item_id = 'item_one'").iloc[0]
        text = wh.sql("SELECT text FROM content_item "
                      "WHERE item_id = 'item_one'").iloc[0]["text"]
    assert text == other.text
    assert prov["prior_text_source"] == "transcript"
    assert prov["prior_text_sha256"] == first_hash


def test_audio_sha256_is_not_the_key(client, db):
    """Empty on the captions path by construction, so a key built on it would
    treat every caption push as identical to every other."""
    first = _transcription(text="the first episode of the week")
    second = _transcription(text="a different episode entirely")
    for t in (first, second):
        object.__setattr__(t, "audio_sha256", "")
    client.post("/api/transcripts", json=_body(first, derivation="captions"),
                headers=_auth())
    r = client.post("/api/transcripts",
                    json=_body(second, item_id="item_two",
                               derivation="captions"),
                    headers=_auth())
    assert r.status_code == 200
    assert r.json()["stored"] is True


# -- the rate limit ----------------------------------------------------------


def test_over_the_rate_limit_is_429(client, db):
    """60 pushes a minute per token. A nightly run pushes a few dozen, so the
    limit is a bound on a runaway loop rather than on real work."""
    payloads = [_body(_transcription(text=f"episode number {i} of the season"),
                      item_id="item_one", replace=True) for i in range(80)]
    codes = [client.post("/api/transcripts", json=p, headers=_auth()).status_code
             for p in payloads]
    assert 429 in codes, f"no push was rate limited: {sorted(set(codes))}"
    assert codes.index(429) >= rt.RATE_LIMIT_PER_MIN


# -- the property this whole split rests on ----------------------------------


def test_the_pushed_rows_are_identical_to_the_local_paths_rows(tmp_path,
                                                               monkeypatch):
    """A pushed transcript and a locally written one leave the same rows.

    Both go through ``asr.store_transcription``, which is the point: the route
    reconstructs the dataclasses and calls it, and contains no second copy of
    the write. This test is what would catch a second copy being introduced.
    """
    monkeypatch.setenv(rt.TOKEN_ENV, TOKEN)
    monkeypatch.setattr("fpl_edge.config.load_env", lambda *a, **k: {})
    transcription = _transcription()

    local_db = tmp_path / "local.duckdb"
    with Warehouse(local_db) as wh:
        ContentStore(wh)
        asr.ensure_schema(wh)
        _item(wh, "item_one")
        asr.store_transcription(wh, "item_one", transcription, derivation="asr")
        local_segments = wh.sql(
            "SELECT * FROM transcript_segment WHERE item_id = 'item_one' "
            "ORDER BY seq").to_dict(orient="records")
        local_prov = wh.sql(
            "SELECT * FROM transcript_provenance "
            "WHERE item_id = 'item_one'").to_dict(orient="records")
        local_item = wh.sql(
            "SELECT text, text_source, text_sha256 FROM content_item "
            "WHERE item_id = 'item_one'").to_dict(orient="records")

    pushed_db = tmp_path / "pushed.duckdb"
    with Warehouse(pushed_db) as wh:
        ContentStore(wh)
        asr.ensure_schema(wh)
        _item(wh, "item_one")
    client = TestClient(create_app(pushed_db))
    r = client.post("/api/transcripts", json=_body(transcription),
                    headers=_auth())
    assert r.status_code == 200, r.text
    with Warehouse(pushed_db, read_only=True) as wh:
        pushed_segments = wh.sql(
            "SELECT * FROM transcript_segment WHERE item_id = 'item_one' "
            "ORDER BY seq").to_dict(orient="records")
        pushed_prov = wh.sql(
            "SELECT * FROM transcript_provenance "
            "WHERE item_id = 'item_one'").to_dict(orient="records")
        pushed_item = wh.sql(
            "SELECT text, text_source, text_sha256 FROM content_item "
            "WHERE item_id = 'item_one'").to_dict(orient="records")

    assert pushed_segments == local_segments
    assert pushed_item == local_item
    assert len(pushed_prov) == len(local_prov) == 1
    # created_utc is carried in the body, so every provenance column including
    # the timestamp must match. A column that differed would mean the route
    # had started deciding something the local path measures.
    assert {k: str(v) for k, v in pushed_prov[0].items()} == \
           {k: str(v) for k, v in local_prov[0].items()}


# -- the queue ---------------------------------------------------------------


def test_the_queue_serves_items_that_need_transcribing(client):
    r = client.get("/api/transcripts/queue", headers=_auth(),
                   params={"resolve_audio": "false"})
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {i["item_id"] for i in body["items"]}
    assert ids == {"item_one", "item_two"}
    for item in body["items"]:
        # What the worker needs to do its job and log what it did.
        assert set(item) >= {"item_id", "creator", "title", "kind",
                             "published_at", "audio_url"}


def test_an_item_with_a_transcript_leaves_the_queue(client):
    client.post("/api/transcripts", json=_body(_transcription()),
                headers=_auth())
    body = client.get("/api/transcripts/queue", headers=_auth(),
                      params={"resolve_audio": "false"}).json()
    assert {i["item_id"] for i in body["items"]} == {"item_two"}


def test_the_queue_records_the_gates_verdicts(tmp_path, monkeypatch):
    """Below-threshold items get a named skip row, exactly as the local
    command records them, so they are not re-scored on every poll."""
    monkeypatch.setenv(rt.TOKEN_ENV, TOKEN)
    monkeypatch.setattr("fpl_edge.config.load_env", lambda *a, **k: {})
    path = tmp_path / "gate.duckdb"
    with Warehouse(path) as wh:
        ContentStore(wh)
        asr.ensure_schema(wh)
        wh.sql(
            "INSERT INTO content_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["dull", "pod_x", "Random Pod", "podcast",
             "Sunday league grassroots special",
             "https://example.test/dull",
             dt.datetime(2020, 1, 1, tzinfo=UTC),
             dt.datetime(2020, 1, 1, tzinfo=UTC), "description",
             "We chat about coaching kids and community pitches.",
             hashlib.sha256(b"x").hexdigest()],
        )
    client = TestClient(create_app(path))
    body = client.get("/api/transcripts/queue", headers=_auth(),
                      params={"since": 0, "resolve_audio": "false"}).json()
    assert body["items"] == []
    assert body["gated"] == 1
    assert body["gate_rows_recorded"] == 1
    with Warehouse(path, read_only=True) as wh:
        row = wh.sql("SELECT item_id, reason FROM content_transcribe_skip").iloc[0]
    assert row["item_id"] == "dull"
    assert row["reason"].startswith("relevance:")
