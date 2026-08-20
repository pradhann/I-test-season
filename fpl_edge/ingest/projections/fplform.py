"""FPL Form: free per-player, per-gameweek expected points.

What it publishes
-----------------
``POST https://fplform.com/export-fpl-form-data.php`` with ``firstgw``,
``lastgw`` and ``all=1`` returns ``text/csv`` with three columns per gameweek::

    ID,Name,Team,Pos,Price,1_pts_no_prob,1_prob,1_with_prob,2_pts_no_prob,...

* ``N_pts_no_prob`` -- expected points **if the player appears**.
* ``N_prob``        -- their probability the player appears at all.
* ``N_with_prob``   -- the product, which is what an ensemble over expected
  points must use.

Keeping all three is deliberate. Their minutes opinion and their points opinion
are separately scoreable, and they fail differently: on 2026-08-19 the export
gave Saliba ``1_pts_no_prob = 0.0``, i.e. the *conditional* number was zeroed
for an unavailable player rather than the probability. A source that encodes
"unavailable" in the wrong factor will silently poison a minutes comparison
unless both factors are kept and the disagreement is visible.

Why the ID column cannot be trusted across seasons
--------------------------------------------------
``ID`` is FPL's per-season ``element_id``. It is reassigned every summer, so a
row stored under ``ID=1`` is a different human next August. Ingestion resolves
it to the stable ``code`` via ``dim_player`` at the snapshot instant, and
**refuses to write any row it cannot resolve** rather than guessing. The
unresolved rows are returned so the caller can see them.

Permission
----------
The export page states the data may be exported for personal use, forbids
re-sharing the files or publishing the data, and asks for credit on published
derived analysis. This module reads it into a private warehouse for one
manager's own decisions and republishes nothing. ``robots.txt`` disallows
``/ajax/`` and five other prefixes; ``/export-fpl-form-data.php`` is in none of
them, and :func:`fetch_csv` checks the live policy before every request.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
import time

import pandas as pd

from fpl_edge.ingest.http import RAW_ROOT, USER_AGENT, Fetched
from fpl_edge.ingest.projections.robots import require_allowed

BASE = "https://fplform.com"
EXPORT_PATH = "/export-fpl-form-data.php"
EXPORT_PAGE = "/export-fpl-form-data"

#: Self-imposed floor between requests. FPL Form publishes no Crawl-delay and no
#: rate limit; the export is a full recompute on their side, so we ask once.
POLITE_DELAY_S = 3.0

_GW_COL = re.compile(r"^(?:tba|(\d+))_(pts_no_prob|prob|with_prob)$")

#: What the site calls the columns, mapped to what this repo calls them.
_ROLE_TO_FIELD = {
    "pts_no_prob": "xp_if_appears",
    "prob": "p_appear",
    "with_prob": "xp",
}


class FplFormError(RuntimeError):
    """The export did not return a CSV we recognise."""


def fetch_csv(
    *,
    first_gw: int = 1,
    last_gw: int = 8,
    client: object | None = None,
    delay_s: float = POLITE_DELAY_S,
) -> Fetched:
    """POST the export form and archive the CSV body.

    Checks the live ``robots.txt`` first and raises ``RobotsDisallowed`` if the
    policy has changed or become unreadable. There is no override that forges a
    User-Agent.
    """
    import httpx

    if first_gw < 1 or last_gw < first_gw or last_gw > 38:
        raise ValueError(f"bad gameweek range {first_gw}..{last_gw}")
    url = f"{BASE}{EXPORT_PATH}"
    require_allowed(url)
    time.sleep(delay_s)

    owned = client is None
    client = client or httpx.Client(
        timeout=90.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    fetched_at = dt.datetime.now(dt.timezone.utc)
    try:
        resp = client.post(  # type: ignore[union-attr]
            url,
            data={"firstgw": str(first_gw), "lastgw": str(last_gw),
                  "all": "1", "submit": "submit"},
        )
    finally:
        if owned:
            client.close()  # type: ignore[union-attr]

    ctype = resp.headers.get("content-type", "")
    if resp.status_code != 200 or "csv" not in ctype.lower():
        raise FplFormError(
            f"expected 200 text/csv from {url}, got {resp.status_code} {ctype!r}. "
            f"First 200 bytes: {resp.text[:200]!r}"
        )
    payload = resp.content
    digest = hashlib.sha256(payload).hexdigest()
    out_dir = RAW_ROOT / "projections_fplform"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (
        f"export_gw{first_gw}-{last_gw}_"
        f"{fetched_at:%Y%m%dT%H%M%SZ}_{digest[:8]}.csv"
    )
    if not path.exists():
        path.write_bytes(payload)
    return Fetched(body=resp.text, fetched_at=fetched_at, sha256=digest,
                   body_path=path, http_status=resp.status_code, from_cache=False)


def parse_csv(text: str) -> pd.DataFrame:
    """Wide FPL Form CSV -> long ``(element_id, gw, xp, xp_if_appears, p_appear)``.

    ``tba_*`` columns -- the site's label for fixtures with no gameweek assigned
    yet -- are dropped rather than coerced to a gameweek number. Guessing which
    gameweek a postponed fixture lands in is the provider's job, not ours, and a
    wrong guess would score their projection against the wrong result.
    """
    frame = pd.read_csv(io.StringIO(text))
    required = {"ID", "Name", "Team", "Pos", "Price"}
    missing = required - set(frame.columns)
    if missing:
        raise FplFormError(f"export is missing {sorted(missing)}; got {list(frame.columns)}")

    records: dict[tuple[int, int], dict[str, float]] = {}
    dropped_tba = 0
    for col in frame.columns:
        m = _GW_COL.match(col)
        if not m:
            continue
        if m.group(1) is None:
            dropped_tba += 1
            continue
        gw = int(m.group(1))
        field = _ROLE_TO_FIELD[m.group(2)]
        for element_id, value in zip(frame["ID"], frame[col]):
            records.setdefault((int(element_id), gw), {})[field] = float(value)

    if not records:
        raise FplFormError("export contained no per-gameweek columns")

    rows = [
        {"element_id": eid, "gw": gw,
         "xp": vals.get("xp"), "xp_if_appears": vals.get("xp_if_appears"),
         "p_appear": vals.get("p_appear")}
        for (eid, gw), vals in records.items()
    ]
    out = pd.DataFrame(rows).sort_values(["gw", "element_id"]).reset_index(drop=True)
    meta = frame[["ID", "Name", "Team", "Pos", "Price"]].rename(
        columns={"ID": "element_id", "Name": "provider_name", "Team": "provider_team",
                 "Pos": "provider_pos", "Price": "provider_price"}
    )
    out = out.merge(meta, on="element_id", how="left")
    out.attrs["dropped_tba_columns"] = dropped_tba
    return out


def to_projection_rows(
    parsed: pd.DataFrame,
    *,
    season: str,
    as_of: dt.datetime,
    id_to_code: dict[int, int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve element_id -> stable code and shape rows for ``fact_projection``.

    Returns ``(rows, unresolved)``. Unresolved element_ids are never written and
    never silently dropped -- the caller is handed them so a mapping gap shows up
    as a number in the run log instead of as a quietly shorter table.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware UTC")
    resolved = parsed.assign(code=parsed["element_id"].map(id_to_code))
    unresolved = resolved[resolved["code"].isna()].copy()
    rows = resolved[resolved["code"].notna()].copy()
    rows = pd.DataFrame({
        "provider": "fplform",
        "season": season,
        "gw": rows["gw"].astype(int),
        "code": rows["code"].astype(int),
        "xp": rows["xp"].astype(float),
        "xp_if_appears": rows["xp_if_appears"].astype(float),
        "p_appear": rows["p_appear"].astype(float),
        # FPL Form publishes P(appears), not expected minutes. Deriving one from
        # the other needs a minutes model; this platform copies minutes opinions
        # rather than building one, so the column it does not publish stays NULL.
        #
        # `index=rows.index` is load-bearing. `rows` has been filtered, so its
        # index is non-contiguous, and a bare `pd.Series([...])` carries a fresh
        # RangeIndex that pandas then ALIGNS against it -- silently producing
        # extra all-NaN rows and NULL gameweeks rather than an error. That
        # exact mistake is what made this provider's first live run die on a
        # NOT NULL constraint on `gw`, three columns away from the column
        # actually at fault.
        "xmins": pd.Series([None] * len(rows), index=rows.index, dtype="float64"),
        "as_of": as_of,
    })
    rows["as_of"] = pd.to_datetime(rows["as_of"], utc=True)
    return rows.reset_index(drop=True), unresolved.reset_index(drop=True)
