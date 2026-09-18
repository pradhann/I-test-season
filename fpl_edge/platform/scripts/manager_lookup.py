"""``manager_lookup``: one tracked manager, resolved only through a verifier.

The panel behind MCP tools 28 (``get_manager_by_name``), 32
(``get_expert_transfers``) and 33 (``get_manager_history``). The three of them
answered one question between them and two reached the live FPL API for rows
the warehouse already holds, so they are one warehouse-only surface here.

WHY THERE IS NO NAME-TO-ID MAP IN THIS FILE

There used to be one in the toolbelt: twenty ``name: entry_id`` pairs mirrored
from a previous season and never checked against anything.

FPL entry ids are assigned per season, in registration order, so a curated map
rots every August, and it rots silently. A stale id does not answer with a 404,
it answers with a different real person. All twenty of those ids were checked
against the live API on 2026-08-24 and every one now belongs to somebody else,
so ``get_manager_history("Holly Shand")`` printed a stranger's ranks under
Holly Shand's name, as fact.

Names are resolved here only against sources that verify:

* ``fpl_edge.ingest.rivals.elite.ELITE_NAMED``, a short curated list every
  member of which ``elite.verify()`` confirmed against ``/api/entry/{id}/`` by
  comparing the account holder's name to the name written beside the id;
* ``dim_manager``, which holds the account-holder name the crawl actually read
  back from the API for every entry it has ever fetched.

A name neither source knows produces a refusal that says so. It never produces
a lookup against an unverified id. A caller who passes a number gets that entry
read, because the id is then the caller's own claim rather than this panel's,
and the payload says the account holder is unverified unless the crawl has read
a name for it.

The stale twenty survive in one place, ``rivals.roster.EXPERT_SEEDS``, where
they are the provenance record of an already-recorded crawl. This module reads
only the keys of that map, and only to explain why one of those names cannot be
resolved. It never reads an id out of it.

WHAT THE WAREHOUSE CANNOT ANSWER, AND SAYS SO

The two deleted tools read the live API, so they could answer for an entry the
crawl has never fetched. This panel cannot, and the refusal names that rather
than returning silence: for a long-tail entry id the honest answer is that the
crawl holds nothing, not that the manager had no transfers.

A NOTE ON SQL PLACEHOLDERS

Queries bind ``$1``-style numbered parameters. The house prose gate reads a
question mark followed by whitespace as a rhetorical question, so a positional
placeholder in a SQL string fails a lint that exists for the payload's English.
Numbered parameters bind identically through ``guarded_query``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import SEASON_DEFAULT, q

UTC = dt.UTC

#: The shortest query this panel will match by name. Two characters match too
#: many people to be safe, and a wrong manager is worse than no manager.
MIN_QUERY_CHARS = 3

#: How the crawl's own name column is filtered before it is trusted as an
#: identity: a blank or a two-letter handle is not an account holder's name.
MIN_CRAWLED_NAME_CHARS = 4


def _norm(text: Any) -> str:
    """The engine's one name-folding function.

    Deliberately not given a local fallback. Two name matchers that disagree is
    how one cohort trusts an id another rejects.
    """
    from fpl_edge.ingest.rivals.names import norm

    return norm(str(text))


def _curated() -> tuple[Any, ...]:
    """The curated, individually verified managers."""
    from fpl_edge.ingest.rivals.elite import ELITE_NAMED

    return tuple(ELITE_NAMED)


def _stale_seed_names() -> tuple[str, ...]:
    """The names of the rotted seeds. Keys only, never their ids."""
    try:
        from fpl_edge.ingest.rivals.roster import EXPERT_SEEDS
    except Exception:  # noqa: BLE001 - the refusal is still correct without it
        return ()
    return tuple(EXPERT_SEEDS.keys())


def _crawled(wh) -> Any:
    """Every entry whose account-holder name the crawl read back from the API.

    Returns None rather than an empty frame when the table is absent, so the
    caller can say "the crawl could not be checked" instead of the much
    stronger "no such manager".
    """
    try:
        rows = q(
            wh,
            "SELECT DISTINCT entry_id, player_name, entry_name, source "
            "FROM dim_manager WHERE player_name IS NOT NULL "
            "AND length(player_name) >= " + str(MIN_CRAWLED_NAME_CHARS),
        )
    except Exception:  # noqa: BLE001 - no rival tables ingested yet
        return None
    return rows


def _refusal(text: str, *, crawl_unread: bool, crawl_rows: int) -> dict[str, Any]:
    """The honest answer to a name no verifier can tie to an entry."""
    curated = _curated()
    reasons = [
        f"no verified source ties {text!r} to an FPL entry, so this panel "
        f"serves nobody. Answering with somebody else's squad under that name "
        f"would be worse than answering with nothing."
    ]
    folded = _norm(text)
    stale = [
        name for name in _stale_seed_names()
        if folded == _norm(name) or folded in _norm(name) or _norm(name) in folded
    ]
    if stale:
        reasons.append(
            f"{text!r} matches {', '.join(stale)} in the stale seed map "
            f"(fpl_edge.ingest.rivals.roster.EXPERT_SEEDS), mirrored from a "
            f"previous season. Every id in that map was checked against the "
            f"live API on 2026-08-24 and every one now belongs to a different "
            f"person, so it records what an old crawl was seeded from rather "
            f"than being a lookup table. This engine does not hold their "
            f"current entry id."
        )
    reasons.append(
        f"Verified named managers: "
        f"{', '.join(e.name for e in curated) or 'none importable'}. Crawled "
        f"managers are searchable too, by the name the FPL API reports."
    )
    if crawl_unread:
        reasons.append(
            "dim_manager could not be read, so only the curated list was "
            "searched. Run an ingest to search the crawled cohorts as well."
        )
    elif crawl_rows == 0:
        reasons.append(
            "dim_manager holds no crawled manager in this warehouse, so only "
            "the curated list was searched. Run the rivals crawl to widen it."
        )
    reasons.append(
        "Passing a numeric entry id reads that entry as given, and the "
        "payload then says the account holder is unverified."
    )
    return {
        "kind": "unverified" if not stale else "stale_seed",
        "stale_seed_names": stale,
        "reason": " ".join(reasons),
    }


def _resolve(wh, text: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """A name or an entry id to one manager this panel can honestly name.

    Returns ``(manager, None)`` or ``(None, refusal)``. A name is paired with
    an id only where a verifying source put the two together.
    """
    text = str(text).strip()
    if not text:
        return None, {"kind": "empty", "stale_seed_names": [],
                      "reason": "no manager was named."}

    crawled = _crawled(wh)

    if text.isdigit():
        entry_id = int(text)
        if entry_id <= 0:
            return None, {
                "kind": "bad_id", "stale_seed_names": [],
                "reason": f"{text!r} is not a usable FPL entry id.",
            }
        name = None
        if crawled is not None and not crawled.empty:
            hit = crawled[crawled["entry_id"].astype(int) == entry_id]
            if not hit.empty:
                name = str(hit.iloc[0]["player_name"]).strip() or None
        return {
            "entry_id": entry_id,
            "player_name": name,
            "verified": name is not None,
            "origin": (
                "name read back from the API by the crawl" if name
                else "caller-supplied id, account holder not verified"
            ),
        }, None

    folded = _norm(text)
    if len(folded) < MIN_QUERY_CHARS:
        return None, {
            "kind": "too_short", "stale_seed_names": [],
            "reason": f"{text!r} is too short to match safely; give at least "
                      f"{MIN_QUERY_CHARS} characters.",
        }

    matches: dict[int, dict[str, Any]] = {}
    for entry in _curated():
        name = _norm(entry.name)
        if name and (folded in name or name in folded):
            matches[int(entry.entry_id)] = {
                "entry_id": int(entry.entry_id),
                "player_name": entry.name,
                "verified": True,
                "origin": "curated elite list, verified against the entry endpoint",
            }
    if crawled is not None:
        for row in crawled.itertuples(index=False):
            name = _norm(row.player_name)
            if name and (folded in name or name in folded):
                matches.setdefault(int(row.entry_id), {
                    "entry_id": int(row.entry_id),
                    "player_name": str(row.player_name),
                    "verified": True,
                    "origin": f"crawled from the API ({row.source})",
                })

    if len(matches) == 1:
        return next(iter(matches.values())), None
    if len(matches) > 1:
        listing = "; ".join(
            f"{m['player_name']} (entry {m['entry_id']}, {m['origin']})"
            for m in matches.values()
        )
        return None, {
            "kind": "ambiguous",
            "stale_seed_names": [],
            "reason": f"{text!r} matches {len(matches)} verified managers, so "
                      f"say which one: {listing}.",
        }
    return None, _refusal(
        text,
        crawl_unread=crawled is None,
        crawl_rows=0 if crawled is None else len(crawled),
    )


def _identity(wh, entry_id: int) -> dict[str, Any] | None:
    rows = q(
        wh,
        "SELECT entry_name, region, years_active FROM dim_manager "
        "WHERE entry_id = $1 ORDER BY as_of DESC LIMIT 1",
        (int(entry_id),),
    )
    if rows.empty:
        return None
    row = rows.iloc[0]
    years = row.get("years_active")
    return {
        "entry_name": None if row.get("entry_name") is None else str(row["entry_name"]),
        "region": None if row.get("region") is None else str(row["region"]),
        "years_active": None if years is None or years != years else int(years),
    }


def _season_record(wh, entry_id: int, season: str) -> dict[str, Any] | None:
    rows = q(
        wh,
        "SELECT gw, total_points, overall_rank, points AS gw_points "
        "FROM fact_manager_gw WHERE entry_id = $1 AND season = $2 "
        "QUALIFY row_number() OVER (ORDER BY gw DESC, as_of DESC) = 1",
        (int(entry_id), season),
    )
    if rows.empty:
        return None
    row = rows.iloc[0].to_dict()

    def _num(key: str) -> int | None:
        value = row.get(key)
        if value is None or value != value:
            return None
        return int(value)

    return {
        "gw": _num("gw"),
        "total_points": _num("total_points"),
        "overall_rank": _num("overall_rank"),
        "gw_points": _num("gw_points"),
    }


def _past_record(wh, entry_id: int) -> list[dict[str, Any]]:
    rows = q(
        wh,
        "SELECT season, overall_rank, total_points FROM ("
        "  SELECT *, row_number() OVER ("
        "    PARTITION BY season ORDER BY as_of DESC) rn "
        "  FROM fact_manager_season WHERE entry_id = $1"
        ") WHERE rn = 1 ORDER BY season DESC",
        (int(entry_id),),
    )
    out = []
    for row in rows.to_dict("records"):
        rank = row.get("overall_rank")
        points = row.get("total_points")
        out.append({
            "season": str(row.get("season") or ""),
            "overall_rank": None if rank is None or rank != rank else int(rank),
            "total_points": None if points is None or points != points else int(points),
        })
    return out


def _transfers(wh, entry_id: int, season: str, when: dt.datetime,
               limit: int) -> list[dict[str, Any]]:
    rows = q(
        wh,
        "SELECT gw, player_in, player_out, code_in, code_out, price_in, "
        "price_out, time_utc FROM sem_manager_transfers($1::TIMESTAMPTZ) "
        "WHERE entry_id = $2 AND season = $3 "
        "ORDER BY gw DESC, time_utc DESC LIMIT " + str(int(limit)),
        (when, int(entry_id), season),
    )
    out = []
    for row in rows.to_dict("records"):
        def _num(key: str) -> Any:
            value = row.get(key)
            if value is None or value != value:
                return None
            return value

        moved = _num("time_utc")
        out.append({
            "gw": None if _num("gw") is None else int(_num("gw")),
            "player_in": None if row.get("player_in") is None else str(row["player_in"]),
            "player_out": None if row.get("player_out") is None else str(row["player_out"]),
            "code_in": None if _num("code_in") is None else int(_num("code_in")),
            "code_out": None if _num("code_out") is None else int(_num("code_out")),
            "price_in": None if _num("price_in") is None else float(_num("price_in")),
            "price_out": None if _num("price_out") is None else float(_num("price_out")),
            "time_utc": None if moved is None else str(moved),
        })
    return out


def _as_of(value: str | None) -> dt.datetime:
    """Parse the optional instant. Timezone-aware UTC, always."""
    if not value:
        return dt.datetime.now(UTC)
    parsed = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(
            "as_of must carry a timezone, for example 2026-08-18T22:50:00Z"
        )
    return parsed.astimezone(UTC)


def manager_lookup(
    wh,
    *,
    manager: str,
    season: str = SEASON_DEFAULT,
    transfers_limit: int = 20,
    as_of: str | None = None,
) -> dict[str, Any]:
    """One tracked manager: identity, standing, past record and transfers.

    The name is resolved only through a source that verified it. A name no
    verifier knows is refused with the reason rather than being paired with an
    id that has rotted.
    """
    when = _as_of(as_of)
    resolved, refusal = _resolve(wh, manager)
    if resolved is None:
        return {
            "query": str(manager),
            "as_of": when.isoformat(),
            "season": season,
            "manager": None,
            "refusal": refusal,
            "identity": None,
            "season_record": None,
            "past_record": [],
            "transfers": [],
            "transfers_reason": (
                "no manager was resolved, so nothing was read for one."
            ),
        }

    entry_id = int(resolved["entry_id"])
    identity = _identity(wh, entry_id)
    season_record = _season_record(wh, entry_id, season)
    past = _past_record(wh, entry_id)
    transfers = _transfers(wh, entry_id, season, when, int(transfers_limit))
    reason = None
    if not transfers:
        reason = (
            f"the crawl holds no {season} transfer for entry {entry_id} at "
            f"{when:%Y-%m-%d %H:%M}Z. This panel reads the warehouse only, so "
            f"for an entry the crawl has never fetched the answer is that "
            f"nothing was fetched, not that no transfer was made."
        )
    return {
        "query": str(manager),
        "as_of": when.isoformat(),
        "season": season,
        "manager": resolved,
        "refusal": None,
        "identity": identity,
        "season_record": season_record,
        "past_record": past,
        "transfers": transfers,
        "transfers_reason": reason,
    }


PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["manager"],
    "properties": {
        "manager": {"type": "string", "minLength": 1, "description":
                    "The manager's real name, accent and case insensitive, "
                    "partial accepted. A numeric FPL entry id is read as "
                    "given and reported as unverified unless the crawl has "
                    "read a name for it."},
        "season": {"type": "string", "default": SEASON_DEFAULT, "minLength": 4,
                   "description":
                   "The season whose standing and transfers are read."},
        "transfers_limit": {"type": "integer", "minimum": 1, "maximum": 200,
                            "default": 20, "description":
                            "How many transfers to return, newest first."},
        "as_of": {"type": ["string", "null"], "default": None, "description":
                  "ISO-8601 instant carrying a timezone. Transfers become "
                  "visible at the deadline of the gameweek they applied to. "
                  "Null means now."},
    },
}

_MANAGER: dict[str, Any] = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["entry_id", "player_name", "verified", "origin"],
    "description": "The entry this run read, or null when nothing was resolved.",
    "properties": {
        "entry_id": {"type": "integer", "description": "The FPL entry id."},
        "player_name": {"type": ["string", "null"], "description":
                        "The account holder's name, or null when no source "
                        "has verified who holds this entry."},
        "verified": {"type": "boolean", "description":
                     "True only when a verifying source put this name and "
                     "this id together."},
        "origin": {"type": "string", "description":
                   "Which source verified the pairing, or that the id came "
                   "from the caller."},
    },
}

_REFUSAL: dict[str, Any] = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["kind", "stale_seed_names", "reason"],
    "description": "Why no manager was served, or null when one was.",
    "properties": {
        "kind": {"enum": ["empty", "bad_id", "too_short", "ambiguous",
                          "unverified", "stale_seed"],
                 "description": "Which refusal this is."},
        "stale_seed_names": {"type": "array", "items": {"type": "string"},
                             "description":
                             "The rotted seed names this query matched, if "
                             "any. Their ids are never read or printed."},
        "reason": {"type": "string", "description":
                   "The refusal in full, for quoting rather than summarising."},
    },
}

_TRANSFER: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["gw", "player_in", "player_out", "code_in", "code_out",
                 "price_in", "price_out", "time_utc"],
    "properties": {
        "gw": {"type": ["integer", "null"], "description":
               "The gameweek the transfer applied to."},
        "player_in": {"type": ["string", "null"], "description":
                      "Who came in, or null when the player list has no row "
                      "for that element id."},
        "player_out": {"type": ["string", "null"], "description":
                       "Who went out, or null for the same reason."},
        "code_in": {"type": ["integer", "null"], "description":
                    "The stable player code of the incoming player."},
        "code_out": {"type": ["integer", "null"], "description":
                     "The stable player code of the outgoing player."},
        "price_in": {"type": ["number", "null"], "description":
                     "What the incoming player cost, in millions."},
        "price_out": {"type": ["number", "null"], "description":
                      "What the outgoing player sold for, in millions."},
        "time_utc": {"type": ["string", "null"], "description":
                     "When the transfer was made, which is the private click "
                     "instant rather than the deadline."},
    },
}

RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query", "as_of", "season", "manager", "refusal", "identity",
                 "season_record", "past_record", "transfers",
                 "transfers_reason"],
    "properties": {
        "query": {"type": "string", "description":
                  "What was asked for, echoed verbatim."},
        "as_of": {"type": "string", "description":
                  "The instant this run read as of, ISO-8601 UTC."},
        "season": {"type": "string", "description": "The season, echoed."},
        "manager": _MANAGER,
        "refusal": _REFUSAL,
        "identity": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["entry_name", "region", "years_active"],
            "description": "What dim_manager holds about the entry, or null "
                           "when the crawl has never read it.",
            "properties": {
                "entry_name": {"type": ["string", "null"], "description":
                               "The team name the manager chose."},
                "region": {"type": ["string", "null"], "description":
                           "The region on the FPL profile."},
                "years_active": {"type": ["integer", "null"], "description":
                                 "Seasons played, as FPL reports it."},
            },
        },
        "season_record": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["gw", "total_points", "overall_rank", "gw_points"],
            "description": "The latest gameweek row the crawl holds for this "
                           "season, or null when it holds none.",
            "properties": {
                "gw": {"type": ["integer", "null"], "description":
                       "The gameweek this standing is as of."},
                "total_points": {"type": ["integer", "null"], "description":
                                 "Season points through that gameweek."},
                "overall_rank": {"type": ["integer", "null"], "description":
                                 "Overall rank at that gameweek."},
                "gw_points": {"type": ["integer", "null"], "description":
                              "Points scored in that gameweek."},
            },
        },
        "past_record": {
            "type": "array",
            "description": "One row per past season the crawl holds, newest "
                           "first.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["season", "overall_rank", "total_points"],
                "properties": {
                    "season": {"type": "string", "description": "The season."},
                    "overall_rank": {"type": ["integer", "null"],
                                     "description": "Final overall rank."},
                    "total_points": {"type": ["integer", "null"],
                                     "description": "Final points."},
                },
            },
        },
        "transfers": {"type": "array", "items": _TRANSFER, "description":
                      "Transfers in this season, newest first."},
        "transfers_reason": {"type": ["string", "null"], "description":
                             "Why there are no transfers, or null when there "
                             "are. It names the crawl as the limit rather "
                             "than implying the manager made none."},
    },
}

register_script(
    name="manager_lookup",
    fn=manager_lookup,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Manager lookup",
    description="One tracked manager, resolved only through a source that "
                "verified the name against the entry id: identity, this "
                "season's standing, the past record on file and the stored "
                "transfers.",
)
