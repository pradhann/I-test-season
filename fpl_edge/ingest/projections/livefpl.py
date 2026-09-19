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
from dataclasses import dataclass
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
    fetched_at = dt.datetime.now(dt.UTC)
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


#: Largest share of the ids IN PLAY -- the file's and the season's together --
#: that may disagree before we refuse to call a file that season.
MAX_SEASON_MISMATCH = 0.10

#: How many times worse the runner-up season must fit. Measured in mismatched
#: ids, not in a ratio: 0.999 against 0.969 sounds like a photo finish and is
#: actually 1 wrong id against 27.
MIN_SEASON_MISMATCH_RATIO = 3.0


@dataclass(frozen=True, slots=True)
class SeasonFit:
    """Which season a keyless file belongs to, and how well it fits.

    The two mismatch counts are kept apart because they mean different things.
    ``unknown_to_season`` ids are the dangerous ones: each is an id the file
    published that this season's ``dim_player`` cannot name, so mapping it
    would either drop it or land it on the wrong player. ``absent_from_file``
    ids are the season's own players that the file left out, which costs
    coverage and nothing else.
    """

    season: str
    file_ids: int
    unknown_to_season: int
    absent_from_file: int

    def line(self) -> str:
        """One line for a fetch_run note."""
        return (f"{self.file_ids} ids -> {self.season}, "
                f"{self.unknown_to_season} unknown to the season, "
                f"{self.absent_from_file} of the season's ids not in the file")


def fit_season(element_ids: set[int], catalogs: dict[str, set[int]]) -> SeasonFit:
    """Which season's element_id table this file's ids belong to, with the counts.

    ``catalogs`` maps season -> the set of element_ids ``dim_player`` holds for
    it. Ranked by MISMATCHED IDS, the size of the symmetric difference, rather
    than by containment and rather than by a similarity ratio.

    Containment was the first implementation and it is wrong in the one way
    that matters. On 2026-08-20 ``predictedEOs/1.json`` carried 595 element_ids
    while ``dim_player`` held 592 for 2026-27 -- LiveFPL had picked up three
    players signed since our last squad refresh. The current season was
    therefore NOT a superset, so containment fell through to 2022-23, whose 778
    ids happen to contain all 595, and the file was written under
    ``season='2022-23', gw=38``. Every id was then remapped onto whichever
    player held it four years ago. Nothing raised. Those rows had to be deleted
    by hand.

    Symmetric difference is immune to that, because being three hundred ids too
    BIG counts against a season exactly as much as being three ids too small.
    2026-27 mismatches by 3; 2022-23 mismatches by 183. That ranking is kept.

    Counting rather than ratio-ing matters for the margin too. Against
    ``top10k.json`` (840 ids) the two best seasons score Jaccard 0.999 and
    0.969, which reads like a photo finish and would fail any sane margin on a
    ratio. In mismatched ids it is 1 against 27, which is not close at all.

    Two things changed on 2026-09-18, and both are about WHICH IDS THE SHARE IS
    TAKEN OF rather than about how seasons are ranked.

    The acceptance threshold counts the mismatch against every id in play, the
    file's and the season's together, instead of against the file alone.
    ``predictedEOs/1.json`` had shrunk to element_ids 1 to 599 exactly, a
    contiguous prefix, while FPL's element space had grown to 659: LiveFPL
    builds that file from a squad snapshot it has not refreshed since the
    season's new signings were added. All 599 ids it publishes are 2026-27 ids
    and no other season's; the 60 mismatches are 600 to 659, players the file
    omits. Measured against the file, 60 of 599 is 10.02% and the ingest
    refused a file it had identified correctly. Measured against the 659 ids in
    play it is 9.1%, and the rival seasons are still 23% to 31% away.

    A season that fails that threshold is no longer counted as a rival in the
    margin test. "A file that could be either season identifies neither" is
    about seasons the file could actually be, and 2022-23 at 179 mismatched ids
    was never one of them; leaving it in the margin test refused the file a
    second time, on a 179-to-60 gap that the 3x rule missed by one id.

    Both failure modes the guard exists for are unchanged. A file keyed on
    another season still carries hundreds of ids this season cannot name and
    still fails the threshold, and two seasons that both fit still refuse.
    """
    if not element_ids:
        raise AmbiguousSeasonError("empty id set identifies no season")
    scored = sorted(
        (len(element_ids ^ ids), len(element_ids | ids), season)
        for season, ids in catalogs.items() if ids
    )
    if not scored:
        raise AmbiguousSeasonError("no season catalogues to compare against")
    detail = ", ".join(f"{s}:{d} of {u} wrong" for d, u, s in scored[:4])
    candidates = [(d, u, s) for d, u, s in scored
                  if d <= MAX_SEASON_MISMATCH * u]
    if not candidates:
        raise AmbiguousSeasonError(
            f"no season's element_id set is within {MAX_SEASON_MISMATCH:.0%} of "
            f"these {len(element_ids)} ids ({detail}). Refusing to map ids onto "
            f"players they do not identify."
        )
    best_diff, _, best = candidates[0]
    runner_diff = candidates[1][0] if len(candidates) > 1 else float("inf")
    # `max(best_diff, 1)` keeps a perfect match from being blocked by a
    # runner-up that is merely also good: 0 * anything is 0.
    if runner_diff < MIN_SEASON_MISMATCH_RATIO * max(best_diff, 1):
        raise AmbiguousSeasonError(
            f"{best} and {candidates[1][2]} fit these ids comparably well "
            f"({detail}). A file that could be either season identifies neither."
        )
    return SeasonFit(
        season=best,
        file_ids=len(element_ids),
        unknown_to_season=len(element_ids - catalogs[best]),
        absent_from_file=len(catalogs[best] - element_ids),
    )


def infer_season(element_ids: set[int], catalogs: dict[str, set[int]]) -> str:
    """The season name alone, for callers that do not need the counts."""
    return fit_season(element_ids, catalogs).season


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
