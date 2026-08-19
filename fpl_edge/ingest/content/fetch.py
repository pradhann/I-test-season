"""Polite HTTP for content, with the archive and the honesty this package needs.

:class:`fpl_edge.ingest.http.Fetcher` is JSON-only and raises on any non-2xx,
which is the right shape for the FPL API and the wrong shape here. Content
sources fail constantly and *how* they fail is part of the deliverable: a 404
from a YouTube feed is a fact to record, not an exception to swallow. So this
fetcher returns the status instead of raising, archives every body under
``data/raw/content/`` exactly as the JSON fetcher does, and additionally
consults ``robots.txt`` before it touches a host.

The robots check is cached per host and applied to every request this module
makes. It is not a formality: it is the mechanism that keeps r/FantasyPL out of
the corpus, and it will keep other hosts out too if their policy changes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path

import httpx

from fpl_edge.ingest.http import USER_AGENT

RAW_ROOT = Path("data/raw/content")

#: Minimum seconds between requests to the same host. YouTube's feeds endpoint
#: starts returning 404 and 500 for ids that are demonstrably correct once it is
#: hit in a tight loop, so this is a correctness measure and not just courtesy.
DEFAULT_DELAY_S = 1.0


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)[:80] or "root"


@dataclass(frozen=True, slots=True)
class Response:
    """A fetch attempt. ``status`` is None only when no response was received."""

    url: str
    status: int | None
    body: bytes
    fetched_at: dt.datetime
    sha256: str
    body_path: Path | None
    error: str | None = None
    robots_blocked: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class RobotsCache:
    """Per-host robots.txt, fetched once.

    A host that fails to serve robots.txt is treated as permitting access, which
    matches the RFC and every mainstream crawler. A host that serves a
    disallowing rule is obeyed even when the target URL answers 200 to a plain
    request -- which is precisely the reddit.com case this exists for.
    """

    def __init__(self, client: httpx.Client, *, user_agent: str = USER_AGENT) -> None:
        self._client = client
        self._ua = user_agent
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._cache:
            self._cache[host] = self._load(host)
        parser = self._cache[host]
        if parser is None:
            return True
        return parser.can_fetch(self._ua, url)

    def _load(self, host: str) -> urllib.robotparser.RobotFileParser | None:
        try:
            resp = self._client.get(f"{host}/robots.txt", timeout=15.0)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200 or not resp.text.strip():
            return None
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(resp.text.splitlines())
        return parser


class ContentFetcher:
    """Fetch arbitrary bodies, archive them, never raise on HTTP status."""

    def __init__(
        self,
        source: str = "content",
        *,
        timeout: float = 45.0,
        delay_s: float = DEFAULT_DELAY_S,
        archive: bool = True,
        respect_robots: bool = True,
    ) -> None:
        self.source = source
        self.delay_s = delay_s
        self.archive = archive
        self.respect_robots = respect_robots
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            follow_redirects=True,
        )
        self._robots = RobotsCache(self._client)
        self._last_hit: dict[str, float] = {}

    # -- internals -----------------------------------------------------------

    def _throttle(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).netloc
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.delay_s - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _archive(self, url: str, body: bytes, fetched_at: dt.datetime, digest: str) -> Path | None:
        if not self.archive or not body:
            return None
        out_dir = RAW_ROOT / self.source
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
        name = _slug(urllib.parse.urlsplit(url).path or urllib.parse.urlsplit(url).netloc)
        path = out_dir / f"{name}_{stamp}_{digest[:8]}.bin"
        if not path.exists():
            path.write_bytes(body)
        return path

    # -- api -----------------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        retries: int = 2,
        retry_on: tuple[int, ...] = (429, 500, 502, 503, 504),
    ) -> Response:
        """GET a URL. Retries transient statuses; returns the last one either way."""
        if self.respect_robots and not self._robots.allowed(url):
            return Response(
                url=url, status=None, body=b"", fetched_at=_now(),
                sha256="", body_path=None,
                error="robots_disallow", robots_blocked=True,
            )

        last: Response | None = None
        for attempt in range(retries + 1):
            self._throttle(url)
            fetched_at = _now()
            try:
                resp = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                last = Response(
                    url=url, status=None, body=b"", fetched_at=fetched_at,
                    sha256="", body_path=None, error=type(exc).__name__,
                )
            else:
                body = resp.content
                digest = hashlib.sha256(body).hexdigest()
                last = Response(
                    url=str(resp.url), status=resp.status_code, body=body,
                    fetched_at=fetched_at, sha256=digest,
                    body_path=self._archive(url, body, fetched_at, digest)
                    if resp.status_code == 200 else None,
                )
                if resp.status_code not in retry_on:
                    return last
            if attempt < retries:
                time.sleep(min(2.0 * (attempt + 1), 6.0))
        assert last is not None
        return last

    def post_json(self, url: str, payload: dict[str, object]) -> Response:
        if self.respect_robots and not self._robots.allowed(url):
            return Response(
                url=url, status=None, body=b"", fetched_at=_now(), sha256="",
                body_path=None, error="robots_disallow", robots_blocked=True,
            )
        self._throttle(url)
        fetched_at = _now()
        try:
            resp = self._client.post(url, json=payload)
        except httpx.HTTPError as exc:
            return Response(
                url=url, status=None, body=b"", fetched_at=fetched_at, sha256="",
                body_path=None, error=type(exc).__name__,
            )
        body = resp.content
        digest = hashlib.sha256(body).hexdigest()
        return Response(
            url=str(resp.url), status=resp.status_code, body=body,
            fetched_at=fetched_at, sha256=digest, body_path=None,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ContentFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
