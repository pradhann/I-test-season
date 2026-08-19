"""A deliberately slow, cached, budgeted FPL client for scraping other people's teams.

Everything else in this package talks to the API through :class:`RivalsFetcher`.
The reason is blunt: mining managers is the only workload in this repo whose
natural request count is in the thousands rather than the dozens. One
enthusiastic loop over ``/entry/{id}/history/`` is the difference between having
elite ownership data at the deadline and having the user's IP rate-limited for
the weekend. So the politeness is not advisory here -- it is enforced by the
type you are forced to use.

Three enforcement mechanisms, in order of how much they have saved us:

**Hard budget.** Every run declares a :class:`RequestBudget`. When it is spent
the next network call raises :class:`BudgetExhausted` rather than continuing.
A crawl that wants more requests has to say so in code, in a diff, where a human
can see the number. There is no "just this once" path.

**Cache with per-endpoint TTLs.** A manager's past-season history changes once a
year; their picks for a *finished* gameweek never change again. Re-fetching
either is pure waste. The cache is keyed on (endpoint, params) and stores the
path of the body that :class:`~fpl_edge.ingest.http.Fetcher` already archived, so
the cache and the provenance archive cannot disagree.

**Serialised, spaced requests.** One connection, one request at a time, with a
minimum gap. No concurrency, ever. A parallel crawler is exactly what triggers
the block we are avoiding.

Why subclass ``Fetcher`` rather than wrap it
--------------------------------------------
``Fetcher._get`` retries on any ``HTTPStatusError``, which is right for the
endpoints it was written for and actively harmful here: ``/entry/{id}/event/
{gw}/picks/`` returns **404 until that gameweek's deadline passes**, and that is
a normal, expected, information-bearing answer -- not a transient failure.
Routing it through the inherited retry turns one legitimate 404 into four
requests and about seven seconds of backoff. Multiplied across a candidate pool
that is thousands of wasted requests spent learning nothing.

So this class overrides ``_get`` to retry transport errors and 5xx only, and
treats 4xx as a final answer. It inherits the raw-archive and sha256 provenance
from ``Fetcher`` unchanged, because reproducibility is not the thing we are
economising on.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from fpl_edge.ingest.http import RAW_ROOT, Fetched, Fetcher

BASE = "https://fantasy.premierleague.com/api"

#: Where the (endpoint, params) -> archived-body index lives. Deliberately next
#: to the archive it points into, so deleting ``data/raw`` invalidates both.
CACHE_INDEX = RAW_ROOT / "rivals" / "cache_index.json"

#: Minimum seconds between two requests. 1.1s is ~0.9 req/s sustained, which is
#: slower than a human clicking through the site and an order of magnitude below
#: anything that looks like a scraper. Do not lower this to "speed up a run":
#: the correct fix for a slow run is a smaller candidate pool.
MIN_INTERVAL_S = 1.1

#: Per-endpoint cache lifetimes, longest-prefix match on the endpoint string.
#: A gameweek's finished picks are immutable, hence the effectively infinite
#: TTL; a manager's season history changes at most once a year but we re-check
#: daily so a newly-finished season is picked up without a manual cache purge.
TTL_S: dict[str, float] = {
    "entry/": 12 * 3600.0,
    "entry/history": 12 * 3600.0,
    "entry/picks": 3650 * 24 * 3600.0,
    "entry/transfers": 3 * 3600.0,
    "leagues": 6 * 3600.0,
    "bootstrap-static/": 3600.0,
}

#: A 404 is cached too, but briefly: the picks endpoint flips from 404 to 200 the
#: moment a deadline passes, and we want the next run after kickoff to see it.
#: Caching it for zero seconds would mean re-asking the same dead question every
#: loop; caching it for hours would mean missing the gameweek we exist to read.
MISSING_TTL_S = 900.0


class BudgetExhausted(RuntimeError):
    """The run asked for more requests than it declared it would spend.

    Raised instead of silently continuing, because the failure mode we are
    guarding against -- a crawl that quietly grows from 800 requests to 40,000
    after an innocuous refactor -- is invisible unless something stops it.
    """


@dataclass
class RequestBudget:
    """A spend limit for one crawl, with an itemised receipt.

    ``spent`` counts requests that actually hit the network. Cache hits are
    counted separately and cost nothing, which is the whole point: a re-run of
    yesterday's crawl should be nearly free, and if it is not, the counters say
    so out loud.
    """

    limit: int
    spent: int = 0
    cache_hits: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)

    def charge(self, kind: str) -> None:
        if self.spent >= self.limit:
            raise BudgetExhausted(
                f"request budget of {self.limit} exhausted (next call: {kind}). "
                f"Spent so far: {self.receipt()}. Raise the limit explicitly in "
                f"code if the larger crawl is genuinely wanted."
            )
        self.spent += 1
        self.by_kind[kind] = self.by_kind.get(kind, 0) + 1

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.spent)

    def receipt(self) -> str:
        items = ", ".join(f"{k}={v}" for k, v in sorted(self.by_kind.items()))
        return (
            f"{self.spent}/{self.limit} network requests ({items or 'none'}), "
            f"{self.cache_hits} cache hits"
        )


def _kind(endpoint: str) -> str:
    """Classify an endpoint for the budget receipt and TTL lookup."""
    parts = endpoint.strip("/").split("/")
    if parts[0] == "entry":
        if "picks" in parts:
            return "entry/picks"
        if "history" in parts:
            return "entry/history"
        if "transfers" in parts:
            return "entry/transfers"
        return "entry/"
    if parts[0].startswith("leagues"):
        return "leagues"
    return endpoint.strip("/") + "/"


def _ttl(kind: str) -> float:
    return TTL_S.get(kind, 3600.0)


def _cache_key(endpoint: str, params: dict[str, Any] | None) -> str:
    canon = json.dumps(params or {}, sort_keys=True, separators=(",", ":"))
    return f"{endpoint.strip('/')}?{canon}"


class RivalsFetcher(Fetcher):
    """Rate-limited, cached, budgeted access to the public FPL API.

    Not thread-safe by choice as well as by omission: the lock below serialises
    calls so that a caller who *does* reach for threads still gets one request
    at a time rather than a burst.
    """

    def __init__(
        self,
        budget: RequestBudget,
        *,
        source: str = "rivals",
        base_url: str = BASE,
        min_interval_s: float = MIN_INTERVAL_S,
        cache_index: Path = CACHE_INDEX,
        raw_root: Path = RAW_ROOT,
        offline: bool = False,
    ) -> None:
        super().__init__(source, base_url=base_url)
        # Overridable so tests archive into a temporary directory instead of
        # scattering fixture bodies through the real provenance archive.
        self.raw_root = raw_root
        self.budget = budget
        self.min_interval_s = min_interval_s
        self.offline = offline
        self._cache_index_path = cache_index
        self._index: dict[str, dict[str, Any]] = self._load_index()
        self._last_request_at = 0.0
        self._lock = threading.Lock()

    # -- cache ---------------------------------------------------------------

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if not self._cache_index_path.exists():
            return {}
        try:
            return json.loads(self._cache_index_path.read_text())
        except (OSError, json.JSONDecodeError):
            # A corrupt index is a performance problem, not a correctness one:
            # the archive is still on disk and the crawl can refill it. Losing
            # the run to a JSONDecodeError would be the worse outcome.
            return {}

    def _save_index(self) -> None:
        self._cache_index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._index, sort_keys=True, indent=0))
        tmp.replace(self._cache_index_path)

    def _cached(self, key: str, kind: str) -> Fetched | None:
        entry = self._index.get(key)
        if entry is None:
            return None
        fetched_at = dt.datetime.fromisoformat(entry["fetched_at"])
        age = (dt.datetime.now(dt.timezone.utc) - fetched_at).total_seconds()
        status = int(entry["http_status"])
        ttl = MISSING_TTL_S if status == 404 else _ttl(kind)
        if age > ttl and not self.offline:
            return None
        path = Path(entry["body_path"])
        if status == 404:
            body: Any = None
        elif path.exists():
            body = json.loads(path.read_text())
        else:
            # Index says we have it, archive says otherwise. Trust the archive.
            return None
        return Fetched(
            body=body,
            fetched_at=fetched_at,
            sha256=entry["sha256"],
            body_path=path,
            http_status=status,
            from_cache=True,
        )

    # -- transport -----------------------------------------------------------

    @retry(
        # 4xx is deliberately absent. ``/entry/{id}/event/{gw}/picks/`` answers
        # 404 for every entry until that gameweek's deadline passes; retrying it
        # spends four requests to be told the same true thing four times.
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _get(self, url: str, params: dict[str, Any] | None) -> httpx.Response:
        resp = self._client.get(url, params=params)
        if resp.status_code >= 500:
            # Server-side wobble, or the start of a soft block. Either way,
            # backing off is right -- but as a transport error so the retry
            # decorator above sees it.
            raise httpx.TransportError(f"{resp.status_code} from {url}")
        return resp

    def _sleep_to_pace(self) -> None:
        gap = time.monotonic() - self._last_request_at
        if gap < self.min_interval_s:
            time.sleep(self.min_interval_s - gap)
        self._last_request_at = time.monotonic()

    # -- public API ----------------------------------------------------------

    def get_json(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> Fetched:  # type: ignore[override]
        """Fetch, or return the cached body. ``body is None`` means a real 404.

        A 404 is returned rather than raised because for this package it is data:
        it is how the API says "that gameweek has not started" and how it says
        "that entry does not exist", and both answers change what we crawl next.
        """
        kind = _kind(endpoint)
        key = _cache_key(endpoint, params)
        with self._lock:
            hit = self._cached(key, kind)
            if hit is not None:
                self.budget.cache_hits += 1
                return hit
            if self.offline:
                raise BudgetExhausted(
                    f"offline=True and {key} is not cached. Run the crawl with "
                    f"offline=False, or narrow the candidate pool."
                )

            self.budget.charge(kind)
            self._sleep_to_pace()

            url = f"{self.base_url}/{endpoint.lstrip('/')}"
            resp = self._get(url, params)
            fetched_at = dt.datetime.now(dt.timezone.utc)

            if resp.status_code == 404:
                got = Fetched(
                    body=None, fetched_at=fetched_at, sha256="",
                    body_path=Path(""), http_status=404, from_cache=False,
                )
            else:
                resp.raise_for_status()
                got = self._archive(endpoint, resp, fetched_at)

            self._index[key] = {
                "fetched_at": got.fetched_at.isoformat(),
                "sha256": got.sha256,
                "body_path": str(got.body_path),
                "http_status": got.http_status,
                "endpoint": endpoint,
            }
            self._save_index()
            return got

    def _archive(self, endpoint: str, resp: httpx.Response, fetched_at: dt.datetime) -> Fetched:
        """Write the body to the permanent archive, exactly as ``Fetcher`` does.

        Reimplemented rather than delegated only because ``Fetcher.get_json``
        performs the request itself; the on-disk layout and hashing are
        identical so the two sources are interchangeable to a replay.
        """
        import hashlib

        from fpl_edge.ingest.http import _slug

        body = resp.json()
        payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(payload).hexdigest()
        out_dir = self.raw_root / self.source
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"{_slug(endpoint)}_{stamp}_{digest[:8]}.json"
        if not path.exists():
            path.write_bytes(payload)
        return Fetched(
            body=body, fetched_at=fetched_at, sha256=digest,
            body_path=path, http_status=resp.status_code, from_cache=False,
        )
