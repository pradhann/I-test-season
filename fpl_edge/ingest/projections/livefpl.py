"""LiveFPL: predicted effective ownership, and top-10k / elite ownership.

Not a points projection. It answers the other half of a rank-utility question --
what the field will own and captain -- which our own ownership model also
predicts, so it is a genuine second opinion on a quantity we already estimate.

Endpoints, all static JSON on ``livefpl.us``, all HTTP 200 on 2026-08-19::

    /predictedEOs/{gw}.json      element_id -> predicted effective ownership
    /top10k.json                 element_id -> ownership share in the top 10k
    /elite.json                  element_id -> ownership share in the elite cohort
    /planner/all_player_info.json  element_id -> FPL code, plus last-season stats

**All three files are EFFECTIVE ownership, not ownership.** They include the
captain multiplier, so exactly one player in each exceeds 1.0 and each file sums
to ~12 rather than ~11. Clipping to 1 would destroy the information the files
exist to carry. The parser range-checks rather than normalises.

**The three files are not keyed on the same season, and nothing on the wire says
so.** On 2026-08-19, ``predictedEOs/1.json`` carried 592 element_ids -- exactly
the 2026-27 squad. ``top10k.json`` and ``elite.json`` carried 840, which is
exactly the 2025-26 squad: they still describe last season's finished cohort.
Both files are bare ``{"id": value}`` objects with no season field.

Mapping them through the current season's element_id table would have been
silent and catastrophic. Element 449 tops ``top10k.json`` at 1.372 -- Bruno
Fernandes in 2025-26. In 2026-27 element 449 is Lewis Hall. A naive ingest
credits a fringe defender with the highest effective ownership in the game and
nothing anywhere raises. This was caught by the range assertion below, and only
by luck: the offending value happened to be the one above 1.0.

So :func:`infer_season` does not trust a caller's opinion about which season a
file belongs to. It asks which season's element_id set actually *contains* the
ids in the file, and refuses when the answer is not unique. A season that gains
players next January will change these files' id sets, and this will notice.

The host that matters is ``livefpl.us``, not ``www.livefpl.net``. The .net host
redirects to ``plan.livefpl.net`` and serves a 404 for ``/robots.txt``; the .us
host serves ``User-agent: * / Allow: /``. The policy that governs a fetch is the
policy of the host being fetched.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from typing import Any

import pandas as pd

from fpl_edge.ingest.http import RAW_ROOT, USER_AGENT, Fetched
from fpl_edge.ingest.projections.robots import require_allowed

BASE = "https://livefpl.us"
POLITE_DELAY_S = 1.5

#: ``metric`` value written to ``fact_external_ownership`` for each endpoint.
#: All three are effective ownership -- ownership times the captain multiplier --
#: which is why none of them is called ``own_*``.
ENDPOINT_METRIC = {
    "predicted_eo": "eo_predicted",
    "top10k": "eo_top10k",
    "elite": "eo_elite",
}

#: Effective ownership above this is not a data point, it is a parse error.
#: A triple-captained template player tops out near 3.0.
MAX_PLAUSIBLE_EO = 3.5


class LiveFplError(RuntimeError):
    """LiveFPL returned something that is not the shape we parse."""


def _path(kind: str, gw: int) -> str:
    if kind == "predicted_eo":
        return f"/predictedEOs/{gw}.json"
    if kind == "top10k":
        return "/top10k.json"
    if kind == "elite":
        return "/elite.json"
    if kind == "player_info":
        return "/planner/all_player_info.json"
    raise ValueError(f"unknown LiveFPL endpoint {kind!r}")


def fetch(kind: str, *, gw: int = 1, client: object | None = None,
          delay_s: float = POLITE_DELAY_S) -> Fetched:
    """Fetch one endpoint, checking the live robots policy first."""
    import httpx

    path = _path(kind, gw)
    url = f"{BASE}{path}"
    require_allowed(url)
    time.sleep(delay_s)
    owned = client is None
    client = client or httpx.Client(
        timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    fetched_at = dt.datetime.now(dt.timezone.utc)
    try:
        resp = client.get(url)  # type: ignore[union-attr]
    finally:
        if owned:
            client.close()  # type: ignore[union-attr]
    if resp.status_code != 200:
        raise LiveFplError(f"{url} returned HTTP {resp.status_code}")
    payload = resp.content
    digest = hashlib.sha256(payload).hexdigest()
    out_dir = RAW_ROOT / "projections_livefpl"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = path.strip("/").replace("/", "_").replace(".json", "")
    dest = out_dir / f"{slug}_{fetched_at:%Y%m%dT%H%M%SZ}_{digest[:8]}.json"
    if not dest.exists():
        dest.write_bytes(payload)
    return Fetched(body=resp.json(), fetched_at=fetched_at, sha256=digest,
                   body_path=dest, http_status=resp.status_code, from_cache=False)


def parse_ownership(body: Any, kind: str) -> pd.DataFrame:
    """``{element_id: value}`` -> ``(element_id, value)``, range-checked."""
    if isinstance(body, str):
        body = json.loads(body)
    if not isinstance(body, dict) or not body:
        raise LiveFplError(f"{kind}: expected a non-empty object, got {type(body).__name__}")
    rows = []
    for key, value in body.items():
        try:
            rows.append((int(key), float(value)))
        except (TypeError, ValueError) as exc:
            raise LiveFplError(f"{kind}: bad entry {key!r} -> {value!r}") from exc
    frame = pd.DataFrame(rows, columns=["element_id", "value"])
    bad = frame[(frame["value"] < 0) | (frame["value"] > MAX_PLAUSIBLE_EO)]
    if not bad.empty:
        raise LiveFplError(
            f"{kind}: {len(bad)} value(s) outside [0, {MAX_PLAUSIBLE_EO}], worst "
            f"{bad['value'].abs().max():.3f}. Effective ownership legitimately "
            f"exceeds 1.0 because it includes captaincy, but not by this much -- "
            f"this is a units or format change, not a data point."
        )
    # An EO file sums to roughly the 11 starters plus the captain's extra share.
    # Well under 11 means we are reading plain ownership or a partial file; well
    # over means the multiplier has been applied twice.
    total = float(frame["value"].sum())
    if not 8.0 <= total <= 18.0:
        raise LiveFplError(
            f"{kind}: values sum to {total:.2f}. An effective-ownership file "
            f"should sum to ~12 (11 starters + the captain's second share). "
            f"This is not the quantity we think it is."
        )
    return frame.sort_values("element_id").reset_index(drop=True)


class AmbiguousSeasonError(LiveFplError):
    """The element_ids in a LiveFPL file do not identify one season."""


def infer_season(element_ids: set[int], catalogs: dict[str, set[int]]) -> str:
    """Which season's element_id table contains every id in the file.

    ``catalogs`` maps season -> the set of element_ids ``dim_player`` holds for
    it. Raises when no season is a superset, or when more than one is: guessing
    here reassigns a projection to a different human, and doing that silently is
    worse than failing.
    """
    supersets = [s for s, ids in catalogs.items() if element_ids <= ids]
    if not supersets:
        best = max(catalogs.items(), key=lambda kv: len(element_ids & kv[1]),
                   default=("none", set()))
        raise AmbiguousSeasonError(
            f"no season's element_id table contains all {len(element_ids)} ids in "
            f"this file. Closest is {best[0]} with {len(element_ids & best[1])} of "
            f"them. Refusing to map ids onto players they do not identify."
        )
    if len(supersets) > 1:
        # Prefer the season whose catalogue is the tightest fit; only tie -> raise.
        sized = sorted(supersets, key=lambda s: len(catalogs[s]))
        if len(catalogs[sized[0]]) == len(catalogs[sized[1]]):
            raise AmbiguousSeasonError(
                f"{len(supersets)} seasons ({', '.join(sorted(supersets))}) each "
                f"contain all these element_ids and are the same size. Cannot tell "
                f"which season this file describes."
            )
        return sized[0]
    return supersets[0]


def parse_code_map(body: Any) -> dict[int, int]:
    """``all_player_info.json`` -> ``{element_id: FPL code}``.

    Provides an independent check on the warehouse's own mapping. Where the two
    disagree the warehouse wins -- it comes from the FPL API itself -- but a
    disagreement is worth knowing about, so callers compare rather than replace.
    """
    if isinstance(body, str):
        body = json.loads(body)
    codes = body.get("codes") if isinstance(body, dict) else None
    if not isinstance(codes, dict) or not codes:
        raise LiveFplError("all_player_info.json has no 'codes' object")
    return {int(k): int(v) for k, v in codes.items()}


def to_ownership_rows(parsed: pd.DataFrame, *, kind: str, season: str, gw: int,
                      as_of: dt.datetime,
                      id_to_code: dict[int, int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Shape rows for ``fact_external_ownership``. Returns ``(rows, unresolved)``."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware UTC")
    resolved = parsed.assign(code=parsed["element_id"].map(id_to_code))
    unresolved = resolved[resolved["code"].isna()].copy()
    keep = resolved[resolved["code"].notna()]
    rows = pd.DataFrame({
        "provider": "livefpl",
        "season": season,
        "gw": int(gw),
        "code": keep["code"].astype(int),
        "metric": ENDPOINT_METRIC[kind],
        "value": keep["value"].astype(float),
        "as_of": as_of,
    })
    rows["as_of"] = pd.to_datetime(rows["as_of"], utc=True)
    return rows.reset_index(drop=True), unresolved.reset_index(drop=True)
