"""Community projection feeds published as CSV on raw.githubusercontent.com.

Why this shape of source is worth a module of its own
-----------------------------------------------------
The platform's whole thesis is that it copies free xMins/xPts opinions and
blends them by measured track record. A feed committed to a public git
repository is the best possible carrier for that:

* **Free and keyless.** ``raw.githubusercontent.com`` serves the file to
  anyone. No account, no session, no obfuscated bundle to defeat.
* **Versioned.** Every past projection is retrievable *with the timestamp it
  was published at*, from git history. Every other free provider overwrites
  itself, so a track record can only be built forward from the day we started
  fetching. Here it can, in principle, be reconstructed backwards -- and a
  provider whose past claims cannot be checked cannot honestly be weighted.
* **Licensable.** A repo either carries a LICENSE or it does not, and that is a
  fact rather than a reading of a terms page.

``raw.githubusercontent.com`` serves HTTP 404 for ``/robots.txt`` (measured
2026-08-20, 14 bytes, ``404: Not Found``). RFC 9309 §2.3.1.3 makes that
"no policy published", which :mod:`fpl_edge.ingest.projections.robots` treats
as permission -- distinct from an unreadable policy, which it treats as a
refusal. ``api.github.com`` answers the same way.

What is deliberately NOT here
-----------------------------
A repo whose LICENSE says the opposite. ``derekkuang/Fantasy-Premier-League``
publishes a Dixon-Coles projection engine under "All rights reserved... Viewing
this repository does not grant any right to reuse its contents." That is a
clear no and it is recorded as one in ``providers.py`` rather than quietly
skipped, so nobody re-discovers it in three months and wonders why it is
missing.

Key spaces, and why they are not interchangeable
------------------------------------------------
Two of these feeds key on different things and the difference is dangerous:

* ``player_code`` -- FPL's cross-season stable code. Usable as-is, but still
  validated against ``dim_player``: a code we have never seen is a typo or a
  different key space, not a player.
* ``element`` -- the per-season element id, reassigned every August. Mapped
  through ``dim_player`` at the fetch instant. An id that does not map is
  DROPPED and COUNTED. It is never guessed, and it is never fuzzy-matched onto
  a name, because element 449 was Bruno Fernandes last season and Lewis Hall
  this one.

Horizon expansion
-----------------
Some feeds publish a whole horizon in one cell -- blueladd's ``xp_next`` is
``"4.89;4.64;4.71;5.02;5.15;5.06"``, six gameweeks from the file's own
gameweek. Those become six rows, which is the point: a transfer decision is
about the next six gameweeks and a one-gameweek feed cannot inform it.

The expansion is guarded rather than trusted. The first element of the horizon
must equal the file's own single-gameweek ``xp`` column to within a rounding
tolerance; if it does not, the two columns do not mean what we think and the
row is written as a single gameweek instead of six wrong ones. The alignment
assumption -- element *i* is gameweek ``file_gw + i`` -- is exactly the sort of
thing that is right until a blank gameweek, and the check is what turns that
from silent corruption into a visible refusal.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
import time
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from fpl_edge.ingest.http import RAW_ROOT, USER_AGENT, Fetched
from fpl_edge.ingest.projections.robots import require_allowed

RAW_BASE = "https://raw.githubusercontent.com"
API_BASE = "https://api.github.com"

#: Self-imposed floor between requests to GitHub. Unauthenticated
#: ``api.github.com`` allows 60 requests an hour per IP; discovery costs one
#: per feed per run, which is nothing, and this keeps it that way.
POLITE_DELAY_S = 1.0

KeyKind = Literal["player_code", "element_id"]


class GithubFeedError(RuntimeError):
    """The feed is not the shape this module knows how to read."""


@dataclass(frozen=True, slots=True)
class Feed:
    """One community repository publishing machine-readable projections."""

    key: str
    name: str
    repo: str                       # "owner/name"
    ref: str                        # branch or tag
    #: Either a literal path (``{gw}``/``{season}`` substituted) or, when the
    #: publisher stamps the filename with a fetch time, ``None`` plus a
    #: ``discover_dir``/``discover_pattern`` pair.
    path_template: str | None
    discover_dir: str | None
    discover_pattern: str | None
    key_column: str
    key_column_kind: KeyKind
    xp_column: str
    xmins_column: str | None
    p_appear_column: str | None
    xp_if_appears_column: str | None
    gw_column: str | None           # None => the gameweek the run asked for
    horizon_column: str | None      # semicolon-joined per-gameweek xp, or None
    licence: str
    cadence: str
    coverage: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def raw_url(self, path: str) -> str:
        return f"{RAW_BASE}/{self.repo}/{self.ref}/{path}"


FEEDS: tuple[Feed, ...] = (
    Feed(
        key="gh_fplbench",
        name="fplbench (PascalAI2024)",
        repo="PascalAI2024/fplbench",
        ref="main",
        path_template="outputs/predictions/gw{gw}_{season}.csv",
        discover_dir=None,
        discover_pattern=None,
        key_column="player_code",
        key_column_kind="player_code",
        # The repo publishes several points heads. `e_points_final` is the one
        # its own board ranks by and the only one that includes the Defensive
        # Contribution points that are worth real FPL points under the 2026-27
        # rules, so it is the number an ensemble over FPL points must compare.
        # `pred_points_decomposed` is the head they publish a MAE for; it is
        # deliberately not stored, because storing two heads from one publisher
        # under two provider names would let one source vote twice.
        xp_column="e_points_final",
        xmins_column="pred_minutes",
        p_appear_column=None,
        xp_if_appears_column=None,
        gw_column="event",
        horizon_column=None,
        licence=(
            "MIT for the code, with an explicit data note: 'Underlying player "
            "data is owned by the Premier League and its data providers... "
            "Research and personal modelling use only.' Our use is private "
            "research; nothing is republished."
        ),
        cadence=(
            "Per-gameweek file committed before the deadline, plus a self-"
            "scoring job that appends the realised result to RESULTS.md after "
            "the gameweek. CI-driven, so the publish time is in git history."
        ),
        coverage="All ~700 elements, both xMins (pred_minutes) and xPts.",
        notes=(
            "Keys on `player_code` -- the stable cross-season code -- which is "
            "rare and valuable: no element_id remap, no name matching, nothing "
            "to get wrong. Still validated against dim_player.",
            "Also carries the FPL API's own `ep_next`. Not ingested from here: "
            "we read it first-hand under provider `fpl_ep`, and taking it "
            "second-hand would give one number two votes in the ensemble.",
        ),
    ),
    Feed(
        key="gh_blueladd",
        name="fpl-projections (blueladd11)",
        repo="blueladd11-commits-tocode/fpl-projections",
        ref="main",
        path_template=None,
        discover_dir="out",
        discover_pattern=r"^projections_gw(?P<gw>\d+)_(?P<stamp>\d{8}T\d{6}Z)_gw\d+\.csv$",
        key_column="element",
        key_column_kind="element_id",
        xp_column="xp",
        xmins_column="xmins",
        # `p_start` is P(starts), which is NOT P(any minutes). A 60-minute
        # substitute has p_start near 0 and p_appear near 1. Mapping one onto
        # the other would make this source look catastrophically wrong about
        # bench players and would poison the appearance comparison it is
        # otherwise the best evidence for. Left NULL; `xmins` carries the
        # minutes opinion in the units the feed actually publishes.
        p_appear_column=None,
        xp_if_appears_column=None,
        gw_column=None,
        horizon_column="xp_next",
        licence=(
            "NO LICENSE FILE. Public and freely fetchable, and the README's "
            "stated purpose is a publicly auditable accuracy record, but "
            "nothing grants reuse rights. Private-warehouse read only; never "
            "republished, never redistributed. Ranked below the MIT feeds for "
            "exactly this reason."
        ),
        cadence=(
            "Rebuilt hourly by a GitHub Action (.github/workflows/tick.yml), "
            "with every pre-deadline snapshot kept under out/archive/. The "
            "freshest cadence of any free source measured -- hourly right up "
            "to the deadline."
        ),
        coverage="~470 eligible players, xMins and xPts, six-gameweek horizon.",
        notes=(
            "The sidecar `<file>.meta.json` carries `deadline_utc`, "
            "`generated_at_utc` and `hours_before_deadline`, which is the "
            "provenance a track record needs and which almost no free provider "
            "publishes.",
            "Keys on `element` (per-season id), so it is remapped through "
            "dim_player and unresolvable ids are dropped and counted.",
        ),
    ),
    Feed(
        key="gh_apex_airsenal",
        name="AIrsenal via fpl-apex (mcnuggets651)",
        repo="mcnuggets651/fpl-apex",
        ref="main",
        path_template="data/generated/airsenal.csv",
        discover_dir=None,
        discover_pattern=None,
        key_column="player_id",
        key_column_kind="element_id",
        xp_column="xp",
        xmins_column=None,
        p_appear_column=None,
        xp_if_appears_column=None,
        gw_column="gw",
        horizon_column=None,
        licence=(
            "The fpl-apex repo is MIT. The numbers themselves are produced by "
            "the Alan Turing Institute's AIrsenal (also MIT), which fpl-apex "
            "runs as its 'projection_worker' at a commit pinned in "
            "upstreams.lock.json -- so both the carrier and the model are "
            "MIT-licensed, and the file's own source_version column names the "
            "AIrsenal commit that produced each row. Underlying player data "
            "remains the Premier League's; private research use only."
        ),
        cadence=(
            "Regenerated by the repo's Apex workflow runs; every row carries "
            "generated_at (the model run instant) and a prediction_tag "
            "grouping one run's rows. Measured 2026-08-23: one run covering "
            "GW2-GW9, generated 2026-08-23T05:52Z."
        ),
        coverage="604 elements x 8-gameweek horizon, xPts only.",
        notes=(
            "This is the ONLY feed in the inventory whose model family is "
            "known and public: AIrsenal's Bayesian team-strength plus "
            "player-contribution model (bpl-next), a genuinely different "
            "opinion from FPL Form's form-regression or fplbench's heads. "
            "Breadth of independent opinion is why it is here.",
            "Keys on `player_id`, verified to be the official FPL element id "
            "(all 604 ids resolved against dim_player 2026-27 on first "
            "measurement). Remapped through dim_player like every element "
            "feed; unresolvable ids are dropped and counted.",
            "One file carries a multi-gameweek horizon as EXPLICIT gw rows "
            "(no horizon-cell expansion needed), so every gameweek's number "
            "is the publisher's own claim, not a positional assumption.",
            "The repo's own ensemble outputs (apex_latest.json) are "
            "deliberately NOT ingested: they blend AIrsenal with sources we "
            "already read first-hand, and a blend of our own inputs voting "
            "as a new source would double-count them.",
        ),
    ),
)

BY_KEY: dict[str, Feed] = {f.key: f for f in FEEDS}


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


def _get(url: str, *, timeout: float, delay_s: float):
    import httpx

    require_allowed(url)
    time.sleep(delay_s)
    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT},
                      follow_redirects=True) as client:
        return client.get(url)


def discover_path(feed: Feed, *, gw: int, delay_s: float = POLITE_DELAY_S) -> str:
    """The newest file in ``feed.discover_dir`` matching the feed's pattern.

    Publishers that stamp a fetch time into the filename cannot be addressed by
    a template, so the directory is listed and the newest stamp wins. "Newest"
    is lexicographic on a ``YYYYMMDDTHHMMSSZ`` stamp, which sorts correctly by
    construction -- that is why the pattern requires that exact shape rather
    than accepting any filename and hoping.
    """
    if not feed.discover_dir or not feed.discover_pattern:
        raise GithubFeedError(f"{feed.key}: no discovery configured")
    url = f"{API_BASE}/repos/{feed.repo}/contents/{feed.discover_dir}?ref={feed.ref}"
    resp = _get(url, timeout=40.0, delay_s=delay_s)
    if resp.status_code != 200:
        raise GithubFeedError(
            f"{feed.key}: listing {feed.discover_dir}/ returned HTTP "
            f"{resp.status_code}: {resp.text[:200]!r}"
        )
    pattern = re.compile(feed.discover_pattern)
    candidates: list[tuple[str, str]] = []
    for entry in resp.json():
        if entry.get("type") != "file":
            continue
        m = pattern.match(entry["name"])
        if m and int(m.group("gw")) == gw:
            candidates.append((m.group("stamp"), entry["path"]))
    if not candidates:
        raise GithubFeedError(
            f"{feed.key}: no file in {feed.repo}/{feed.discover_dir} matches "
            f"{feed.discover_pattern!r} for gw {gw}. The publisher has renamed "
            f"its output or has not published this gameweek yet."
        )
    return max(candidates)[1]


def fetch(feed: Feed, *, season: str, gw: int, timeout: float = 60.0,
          delay_s: float = POLITE_DELAY_S) -> Fetched:
    """Fetch one feed's file for ``gw`` and archive the exact bytes."""
    path = (feed.path_template.format(gw=gw, season=season)
            if feed.path_template else discover_path(feed, gw=gw, delay_s=delay_s))
    url = feed.raw_url(path)
    fetched_at = dt.datetime.now(dt.timezone.utc)
    resp = _get(url, timeout=timeout, delay_s=delay_s)
    if resp.status_code != 200:
        raise GithubFeedError(f"{url} returned HTTP {resp.status_code}")
    payload = resp.content
    digest = hashlib.sha256(payload).hexdigest()
    out_dir = RAW_ROOT / f"projections_{feed.key}"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    dest = out_dir / f"{stem}_{fetched_at:%Y%m%dT%H%M%SZ}_{digest[:8]}.csv"
    if not dest.exists():
        dest.write_bytes(payload)
    return Fetched(body=resp.text, fetched_at=fetched_at, sha256=digest,
                   body_path=dest, http_status=resp.status_code, from_cache=False)


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


def parse(feed: Feed, text: str) -> pd.DataFrame:
    """The feed's CSV, checked for the columns this feed promised."""
    frame = pd.read_csv(io.StringIO(text))
    needed = {feed.key_column, feed.xp_column}
    for col in (feed.xmins_column, feed.p_appear_column,
                feed.xp_if_appears_column, feed.gw_column, feed.horizon_column):
        if col:
            needed.add(col)
    missing = needed - set(frame.columns)
    if missing:
        raise GithubFeedError(
            f"{feed.key}: file is missing {sorted(missing)}. Columns present: "
            f"{list(frame.columns)}. The publisher has changed schema; the "
            f"mapping in FEEDS must be re-read against the new file rather "
            f"than the missing columns filled with nulls."
        )
    if frame.empty:
        raise GithubFeedError(f"{feed.key}: file parsed to zero rows")
    return frame


def _expand_horizon(feed: Feed, frame: pd.DataFrame, base_gw: int) -> pd.DataFrame:
    """``xp_next = "4.89;4.64;..."`` -> one row per gameweek of the horizon.

    Refuses the expansion (and falls back to the single base gameweek) when the
    horizon's first element disagrees with the file's own single-gameweek xp:
    that disagreement means the two columns are not the quantities this mapping
    assumes, and six confidently wrong rows are worse than one right one.
    """
    horizons = frame[feed.horizon_column].astype(str).str.split(";")
    lengths = {len(h) for h in horizons}
    if len(lengths) != 1:
        raise GithubFeedError(
            f"{feed.key}: {feed.horizon_column} has {sorted(lengths)} different "
            f"lengths in one file. A ragged horizon cannot be aligned to "
            f"gameweeks by position."
        )
    first = pd.to_numeric(horizons.str[0], errors="coerce")
    base = pd.to_numeric(frame[feed.xp_column], errors="coerce")
    both = first.notna() & base.notna()
    if not both.any() or (first[both] - base[both]).abs().max() > 0.011:
        raise GithubFeedError(
            f"{feed.key}: {feed.horizon_column}[0] does not match "
            f"{feed.xp_column} (max gap "
            f"{(first[both] - base[both]).abs().max() if both.any() else float('nan'):.3f}). "
            f"The horizon is not anchored at this file's gameweek, so its "
            f"positions cannot be read as gw{base_gw}, gw{base_gw + 1}, ..."
        )
    horizon_len = lengths.pop()
    out = frame.loc[frame.index.repeat(horizon_len)].copy()
    out["_h_index"] = list(range(horizon_len)) * len(frame)
    out["gw"] = base_gw + out["_h_index"]
    out[feed.xp_column] = pd.to_numeric(
        [h[i] for h, i in zip(horizons.loc[out.index], out["_h_index"])],
        errors="coerce",
    )
    if feed.xmins_column:
        # The minutes opinion is published for the base gameweek only. Copying
        # it forward would invent a claim the publisher did not make.
        out.loc[out["_h_index"] != 0, feed.xmins_column] = pd.NA
    return out.drop(columns="_h_index").reset_index(drop=True)


def to_projection_rows(
    feed: Feed,
    parsed: pd.DataFrame,
    *,
    season: str,
    as_of: dt.datetime,
    id_to_code: dict[int, int] | None,
    valid_codes: set[int],
    default_gw: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve keys and shape rows for ``fact_projection``.

    Returns ``(rows, unresolved)``. Unresolved keys are never written and never
    silently dropped: they come back so a mapping gap shows up as a number in
    the run log rather than as a quietly shorter table.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware UTC")

    frame = parsed
    if feed.horizon_column:
        try:
            frame = _expand_horizon(feed, parsed, default_gw)
        except GithubFeedError as exc:
            print(f"  {feed.key}: horizon not expanded -- {exc}")
            frame = parsed.assign(gw=default_gw)
    elif feed.gw_column:
        frame = parsed.assign(gw=pd.to_numeric(parsed[feed.gw_column], errors="coerce"))
    else:
        frame = parsed.assign(gw=default_gw)

    raw_key = pd.to_numeric(frame[feed.key_column], errors="coerce")
    if feed.key_column_kind == "element_id":
        if id_to_code is None:
            raise ValueError(f"{feed.key} keys on element_id but no id_to_code given")
        code = raw_key.map(lambda v: id_to_code.get(int(v)) if pd.notna(v) else None)
        why = "element_id not in dim_player for this season"
    else:
        code = raw_key.map(
            lambda v: int(v) if pd.notna(v) and int(v) in valid_codes else None
        )
        why = "player_code not in dim_player for this season"

    frame = frame.assign(code=code)
    bad = frame["code"].isna() | frame["gw"].isna()
    unresolved = frame.loc[bad, [feed.key_column, "gw"]].copy()
    if not unresolved.empty:
        unresolved["reason"] = why
        unresolved.loc[frame.loc[bad, "gw"].isna(), "reason"] = "no usable gameweek"
    keep = frame.loc[~bad]

    def _num(col: str | None) -> pd.Series:
        if col is None:
            return pd.Series([None] * len(keep), index=keep.index, dtype="float64")
        return pd.to_numeric(keep[col], errors="coerce").astype("float64")

    rows = pd.DataFrame({
        "provider": feed.key,
        "season": season,
        "gw": keep["gw"].astype(int),
        "code": keep["code"].astype(int),
        "xp": _num(feed.xp_column),
        "xp_if_appears": _num(feed.xp_if_appears_column),
        "p_appear": _num(feed.p_appear_column),
        "xmins": _num(feed.xmins_column),
        "as_of": as_of,
    })
    rows["as_of"] = pd.to_datetime(rows["as_of"], utc=True)
    rows = rows.reset_index(drop=True)

    # One publisher, one opinion per player per gameweek. A duplicate key with
    # two different numbers is the publisher contradicting itself inside one
    # file; writing either would be a coin flip, so both go to unresolved.
    dup = rows.duplicated(["gw", "code"], keep=False)
    if dup.any():
        clashed = rows.loc[dup, ["gw", "code"]].copy()
        clashed[feed.key_column] = pd.NA
        clashed["reason"] = "duplicate (gw, code) within one file"
        unresolved = pd.concat([unresolved, clashed], ignore_index=True)
        rows = rows.loc[~dup].reset_index(drop=True)

    return rows, unresolved.reset_index(drop=True)
