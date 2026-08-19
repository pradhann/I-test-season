"""The politeness machinery has to be tested, because nothing else will catch it.

A rate limiter that silently stops limiting, a cache that silently stops hitting
or a budget that silently stops counting all produce a working crawl. The only
symptom is a request count nobody looks at until the API stops answering. So
these are tested directly, with a mock transport rather than the live API.
"""

from __future__ import annotations

import json

import httpx
import pytest

from fpl_edge.ingest.rivals.client import (
    BudgetExhausted,
    RequestBudget,
    RivalsFetcher,
    _kind,
)


def _fetcher(tmp_path, responses, *, budget=50, min_interval_s=0.0):
    """A fetcher wired to a scripted transport and a private cache directory."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        status, body = (200, {"ok": True})
        for suffix, scripted in responses.items():
            if path.endswith(suffix):
                status, body = scripted
                break
        if body is None:
            return httpx.Response(status, json={"detail": "Not found."})
        return httpx.Response(status, json=body)

    f = RivalsFetcher(
        RequestBudget(limit=budget),
        source="rivals_test",
        min_interval_s=min_interval_s,
        cache_index=tmp_path / "cache.json",
        raw_root=tmp_path / "raw",
    )
    f._client = httpx.Client(transport=httpx.MockTransport(handler))
    return f, calls


def test_budget_is_a_hard_stop_not_a_warning(tmp_path):
    f, calls = _fetcher(tmp_path, {}, budget=3)
    for i in range(3):
        f.get_json(f"entry/{i}/history/")
    with pytest.raises(BudgetExhausted):
        f.get_json("entry/99/history/")
    assert len(calls) == 3, "a request was made after the budget was exhausted"


def test_second_identical_request_is_served_from_cache(tmp_path):
    f, calls = _fetcher(tmp_path, {"/entry/7/history/": (200, {"past": []})})
    first = f.get_json("entry/7/history/")
    second = f.get_json("entry/7/history/")
    assert len(calls) == 1, "the cache did not prevent a duplicate network call"
    assert first.from_cache is False and second.from_cache is True
    assert f.budget.spent == 1 and f.budget.cache_hits == 1


def test_cache_distinguishes_params(tmp_path):
    """Two pages of the same league are different requests, not a cache hit."""
    f, calls = _fetcher(tmp_path, {})
    f.get_json("leagues-classic/1/standings/", {"page_standings": 1})
    f.get_json("leagues-classic/1/standings/", {"page_standings": 2})
    assert len(calls) == 2


def test_404_is_data_not_an_exception_and_is_not_retried(tmp_path):
    """The picks endpoint answers 404 until a deadline passes. That is an answer.

    The inherited Fetcher retries any HTTPStatusError, which would turn one
    legitimate 404 into four requests. This is the regression guard.
    """
    f, calls = _fetcher(tmp_path, {"/entry/5/event/9/picks/": (404, None)})
    got = f.get_json("entry/5/event/9/picks/")
    assert got.body is None
    assert got.http_status == 404
    assert len(calls) == 1, f"a 404 was retried {len(calls)} times"


def test_404_is_cached_only_briefly(tmp_path):
    """A 404 must expire fast: the picks endpoint flips to 200 at the deadline."""
    from fpl_edge.ingest.rivals import client as mod

    f, calls = _fetcher(tmp_path, {"/entry/5/event/9/picks/": (404, None)})
    f.get_json("entry/5/event/9/picks/")
    assert mod.MISSING_TTL_S < mod.TTL_S["entry/picks"] / 100, (
        "a 404 is cached for a comparable time to a real body; the gameweek "
        "this package exists to read would be missed"
    )


def test_5xx_is_treated_as_transport_failure_and_backed_off(tmp_path):
    f, calls = _fetcher(tmp_path, {"/entry/1/history/": (503, {"detail": "busy"})})
    with pytest.raises(httpx.TransportError):
        f.get_json("entry/1/history/")
    assert len(calls) >= 2, "a 5xx was not retried at all"
    assert len(calls) <= 3, "a 5xx was retried more than the configured attempts"


def test_rate_limiter_actually_sleeps(tmp_path, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(
        "fpl_edge.ingest.rivals.client.time.sleep", lambda s: slept.append(s)
    )
    f, _ = _fetcher(tmp_path, {}, min_interval_s=1.1)
    f.get_json("entry/1/history/")
    f.get_json("entry/2/history/")
    assert slept, "no pacing sleep occurred between two consecutive requests"
    assert max(slept) <= 1.1


def test_offline_mode_refuses_to_reach_the_network(tmp_path):
    f, calls = _fetcher(tmp_path, {})
    f.get_json("entry/1/history/")
    f.offline = True
    f.get_json("entry/1/history/")           # cached, fine
    with pytest.raises(BudgetExhausted):
        f.get_json("entry/2/history/")       # not cached
    assert len(calls) == 1


def test_cache_survives_a_new_fetcher_instance(tmp_path):
    """A scheduled re-run must not re-pay for what yesterday's run fetched."""
    f1, calls = _fetcher(tmp_path, {"/entry/3/history/": (200, {"past": []})})
    f1.get_json("entry/3/history/")

    f2, calls2 = _fetcher(tmp_path, {"/entry/3/history/": (200, {"past": []})})
    got = f2.get_json("entry/3/history/")
    assert got.from_cache is True
    assert calls2 == [], "a second process re-fetched an already-archived body"


def test_corrupt_cache_index_degrades_to_refetch(tmp_path):
    index = tmp_path / "cache.json"
    index.write_text("{not json")
    f, calls = _fetcher(tmp_path, {})
    f.get_json("entry/1/history/")
    assert len(calls) == 1
    assert json.loads(index.read_text()), "index was not rewritten after corruption"


@pytest.mark.parametrize("endpoint,expected", [
    ("entry/123/history/", "entry/history"),
    ("entry/123/event/4/picks/", "entry/picks"),
    ("entry/123/transfers/", "entry/transfers"),
    ("entry/123/", "entry/"),
    ("leagues-classic/314/standings/", "leagues"),
    ("leagues-h2h/9/standings/", "leagues"),
    ("bootstrap-static/", "bootstrap-static/"),
])
def test_endpoint_classification_drives_ttl_and_receipt(endpoint, expected):
    assert _kind(endpoint) == expected


def test_receipt_reports_cache_hits_separately_from_spend():
    b = RequestBudget(limit=10)
    b.charge("entry/history")
    b.charge("entry/history")
    b.cache_hits += 5
    receipt = b.receipt()
    assert "2/10" in receipt and "5 cache hits" in receipt
    assert b.remaining == 8
