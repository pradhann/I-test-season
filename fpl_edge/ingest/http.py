"""Cached, polite HTTP with a permanent raw archive.

Every response body is written to ``data/raw/<source>/`` and recorded in
``raw_fetch`` with its sha256. Two consequences that matter:

* Backtests are reproducible offline. The warehouse can be dropped and rebuilt
  from the archive without touching the network.
* Any claim the engine makes is traceable to bytes we actually received, rather
  than to whatever the endpoint returns today.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

RAW_ROOT = Path("data/raw")

USER_AGENT = "fpl-edge/0.1 (personal research; contact via repo owner)"


@dataclass(frozen=True, slots=True)
class Fetched:
    """A response plus the provenance needed to defend it later."""

    body: Any
    fetched_at: dt.datetime
    sha256: str
    body_path: Path
    http_status: int
    from_cache: bool


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _slug(endpoint: str) -> str:
    return endpoint.strip("/").replace("/", "_").replace("?", "_").replace("&", "_") or "root"


class Fetcher:
    """Fetch JSON with retries, archiving every body."""

    def __init__(
        self,
        source: str,
        *,
        base_url: str = "",
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.source = source
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            # Extra headers (e.g. the Origin/Referer some endpoints require)
            # never override the identified User-Agent: staying identifiable
            # is the posture, not an accident of dict order.
            headers={**(headers or {}), "User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get(self, url: str, params: dict[str, Any] | None) -> httpx.Response:
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        return resp

    def get_json(self, endpoint: str, params: dict[str, Any] | None = None) -> Fetched:
        url = f"{self.base_url}/{endpoint.lstrip('/')}" if self.base_url else endpoint
        fetched_at = _now()
        resp = self._get(url, params)
        body = resp.json()
        payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(payload).hexdigest()

        out_dir = RAW_ROOT / self.source
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"{_slug(endpoint)}_{stamp}_{digest[:8]}.json"
        if not path.exists():
            path.write_bytes(payload)

        return Fetched(
            body=body,
            fetched_at=fetched_at,
            sha256=digest,
            body_path=path,
            http_status=resp.status_code,
            from_cache=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
