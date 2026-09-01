"""
Tools for analysing well-known Fantasy Premier League managers' teams.

Three tools live here: an ownership cross-tab across several managers
(:func:`get_expert_teams_summary`), one manager's recent transfers
(:func:`get_expert_transfers`) and one manager's season-by-season record
(:func:`get_manager_history`). All three read only the public, unauthenticated
endpoints of the official FPL API -- and since the fetch unification
(PIPELINES.md §6.5) they read them through the engine's rivals client
(:func:`fpl_mcp.utils.fpl_data.entry_json`): enforced politeness interval,
TTL cache shared with the crawl, provenance archive, and a hard per-process
request budget, instead of bare ``requests.get``.

Why there is no name-to-id map in this file any more
----------------------------------------------------
There used to be one: twenty ``name: entry_id`` pairs mirrored from a previous
season of FPL-MCP and never checked against anything.

FPL entry IDs are assigned per season, in registration order, so a curated map
rots every August -- and it rots **silently**: a stale id does not 404, it
resolves to a different real person. Every one of those twenty ids was checked
against the live API on 2026-08-24 and every one now belongs to somebody else
("Ben Crellin" 6586 is actually Levi Longworth; "Holly Shand" 135 is actually
Caleb Stevens). ``get_manager_history("Holly Shand")`` therefore printed a
stranger's ranks under Holly Shand's name, as fact. The engine's crawl already
gates that map behind :func:`fpl_edge.ingest.rivals.roster.verify_expert_seeds`
and rejects all twenty; this toolbelt had no such gate.

Names are now resolved the way ``chat_tools.get_manager_by_name`` resolves
them -- against sources that verify, and only those:

* :data:`fpl_edge.ingest.rivals.elite.ELITE_NAMED`, a short curated list every
  member of which ``elite.verify()`` confirmed against ``/api/entry/{id}/`` by
  comparing the account holder's name to the name written beside the id;
* ``dim_manager``, which holds the account-holder name the crawl actually read
  back from the API for every entry it has ever fetched.

A name neither source knows produces "I cannot verify who that is", visibly.
It never produces a lookup against an unverified id. A caller who passes a
*number* gets that entry queried -- the id is then the caller's own claim, not
ours -- and the output says the account holder is unverified unless the crawl
has read a name for it.

The stale twenty survive in exactly one place now,
:data:`fpl_edge.ingest.rivals.roster.EXPERT_SEEDS`, where they are the
documented provenance record of an already-recorded crawl. This module reads
only the *keys* of that map, and only to explain why one of those names cannot
be resolved; it never reads an id out of it.

An element id that the bootstrap table does not know is reported as unknown,
with its raw id -- never dropped from a cross-tab, never named, and never
priced £0.0m.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from fpl_mcp.utils import fpl_data  # type: ignore
from fpl_mcp.server import mcp  # type: ignore

# The engine-locating machinery and the read-only warehouse copy, reused rather
# than duplicated. Both degrade to "engine unavailable" instead of raising.
from fpl_mcp.tools import edge_tools as _edge  # type: ignore
from fpl_mcp.tools import semantic_tools as _sem  # type: ignore


def _lookup_element(elements_df: pd.DataFrame, elem_id: Any) -> Optional[pd.Series]:
    """The bootstrap row for ``elem_id``, or None when it is not in the table.

    ``DataFrame.loc`` raises ``KeyError`` on a missing label and, unlike a
    dict, has no ``.get``. This is the tolerant lookup the caller wanted:
    None means "this id is not in the table", and the caller must then SAY
    that rather than invent a player or a price.
    """
    if elem_id is None:
        return None
    try:
        row = elements_df.loc[elem_id]
    except (KeyError, TypeError):
        return None
    if isinstance(row, pd.DataFrame):  # a duplicated id in the index
        row = row.iloc[0]
    return row


def _price_m(player: pd.Series) -> str:
    """``now_cost`` as a £m string, or "?" when the table has no number.

    Returns a string, not a float, because there is no float that honestly
    means "unknown": 0.0 reads as a free player, which is precisely the
    fabrication this module exists to avoid.
    """
    cost = player.get("now_cost")
    if cost is None or (not isinstance(cost, str) and pd.isna(cost)):
        return "?"
    try:
        return f"{float(cost) / 10.0:.1f}"
    except (TypeError, ValueError):
        return "?"


# -----------------------------------------------------------------------------
# Who is this? -- verified identity resolution


@dataclass(frozen=True)
class _Manager:
    """One entry we are willing to query, and what we actually know about it.

    ``name`` is ``None`` when nobody has verified who holds the entry. That is
    a real state and it is rendered as such; it is never filled in with a name
    a caller merely mentioned in the same sentence as a number.
    """

    entry_id: int
    name: Optional[str]
    origin: str

    @property
    def display(self) -> str:
        """Short form for a table cell."""
        return self.name if self.name else f"entry {self.entry_id}"

    @property
    def label(self) -> str:
        """Long form for a heading: never a name we cannot support."""
        if self.name:
            return f"{self.name} (entry {self.entry_id}, {self.origin})"
        return f"entry {self.entry_id} (account holder not verified)"


def _norm(text: Any) -> str:
    """The engine's one name-folding function. Raises if it is not importable.

    Deliberately not given a local fallback: two name matchers that disagree is
    how one cohort ends up trusting an id another rejects
    (see :mod:`fpl_edge.ingest.rivals.names`).
    """
    from fpl_edge.ingest.rivals.names import norm  # deferred: engine may be absent

    return norm(str(text))


def _elite_named() -> Tuple[Any, ...]:
    """The curated, individually verified managers -- or () if unimportable."""
    try:
        from fpl_edge.ingest.rivals.elite import ELITE_NAMED
    except Exception:  # noqa: BLE001 - the toolbelt must not die with the engine
        return ()
    return tuple(ELITE_NAMED)


def _stale_seed_names() -> Tuple[str, ...]:
    """The names of the twenty rotted seeds -- keys only, never their ids."""
    try:
        from fpl_edge.ingest.rivals.roster import EXPERT_SEEDS
    except Exception:  # noqa: BLE001
        return ()
    return tuple(EXPERT_SEEDS.keys())


def _crawled_managers() -> Optional[pd.DataFrame]:
    """Every entry whose account-holder name the crawl read back from the API.

    Returns ``None`` -- not an empty frame -- when the warehouse cannot be
    read, so the caller can say "I could not check the crawl" instead of the
    much stronger "no such manager". Read-only throughout: ``_sem._read()``
    takes a private copy of the DuckDB file, so this never contends with, and
    never blocks, the single writer.
    """
    if _edge._unavailable():
        return None
    try:
        with _sem._read() as wh:
            return wh.sql(
                "SELECT DISTINCT entry_id, player_name, entry_name, source "
                "FROM dim_manager WHERE player_name IS NOT NULL "
                "AND length(player_name) >= 4"
            )
    except Exception:  # noqa: BLE001 - no rival tables ingested yet, or no db
        return None


def _crawled_name(entry_id: int) -> Optional[str]:
    """The account-holder name the crawl read for ``entry_id``, if any."""
    df = _crawled_managers()
    if df is None or df.empty or "entry_id" not in df.columns:
        return None
    for row in df.itertuples(index=False):
        try:
            if int(row.entry_id) == int(entry_id):
                name = str(row.player_name).strip()
                return name or None
        except (TypeError, ValueError):
            continue
    return None


def _unverifiable(text: str, *, warehouse_unread: bool) -> str:
    """The honest answer to a name we cannot tie to a verified entry."""
    curated = ", ".join(e.name for e in _elite_named())
    lines = [(
        f"I cannot verify who {text!r} is, so I will not answer for them. "
        f"Giving you somebody else's squad under that name would be worse "
        f"than giving you nothing."
    )]
    q = _norm(text)
    if any(q == _norm(n) or q in _norm(n) or _norm(n) in q for n in _stale_seed_names()):
        lines.append(
            f"{text!r} is one of the twenty names in the stale seed map "
            f"(fpl_edge.ingest.rivals.roster.EXPERT_SEEDS), mirrored from a "
            f"previous season. Every id in that map was checked against the "
            f"live API on 2026-08-24 and every one now belongs to a different "
            f"person, so it is a record of what an old crawl was seeded from, "
            f"not a lookup table. This engine does not know their current "
            f"entry id."
        )
    known = curated if curated else "(the curated list could not be imported)"
    lines.append(
        f"Verified named managers: {known}. Crawled managers are searchable "
        f"too, by the name the FPL API reports for them."
    )
    if warehouse_unread:
        lines.append(
            "The warehouse could not be read, so only the curated list was "
            "searched. Run `make ingest` in the engine to search crawled "
            "managers as well."
        )
    lines.append(
        "If you know their entry id, pass the number and I will query it as "
        "given -- and say plainly that the account holder is unverified."
    )
    return "\n".join(lines)


def _resolve_manager(name_or_id: Any) -> Tuple[Optional[_Manager], Optional[str]]:
    """Resolve a name or an entry id to a manager we can honestly name.

    Returns ``(manager, None)`` on success and ``(None, reason)`` otherwise.
    A name is never paired with an id unless a verifying source put the two
    together -- the curated elite list (checked against ``/entry/{id}/``) or
    ``dim_manager`` (the name the crawl read back from the API).
    """
    text = str(name_or_id).strip()
    if not text:
        return None, "No manager was named."

    # A bare number is the caller's own claim about which entry to query. We
    # honour it and query that entry -- but we attach a name to it only if a
    # verifying source has one, and say so when it does not.
    if text.isdigit():
        eid = int(text)
        if eid <= 0:
            return None, f"{text!r} is not a usable FPL entry id."
        name = _crawled_name(eid)
        origin = "name read back from the API by the crawl" if name else "caller-supplied id"
        return _Manager(eid, name, origin), None

    try:
        q = _norm(text)
    except Exception as exc:  # noqa: BLE001
        return None, (
            f"Cannot resolve {text!r} by name: the fpl-edge name matcher is "
            f"not importable ({type(exc).__name__}: {exc}). Pass a numeric FPL "
            f"entry id instead."
        )
    if len(q) < 3:
        return None, f"{text!r} is too short to match safely -- give at least 3 characters."

    matches: Dict[int, _Manager] = {}
    for e in _elite_named():
        en = _norm(e.name)
        if en and (q in en or en in q):
            matches[int(e.entry_id)] = _Manager(
                int(e.entry_id), e.name,
                "curated elite list, verified against /entry/{id}/",
            )
    crawled = _crawled_managers()
    if crawled is not None:
        for row in crawled.itertuples(index=False):
            nn = _norm(row.player_name)
            if nn and (q in nn or nn in q):
                matches.setdefault(int(row.entry_id), _Manager(
                    int(row.entry_id), str(row.player_name),
                    f"crawled from the API ({row.source})",
                ))

    if len(matches) == 1:
        return next(iter(matches.values())), None
    if len(matches) > 1:
        # One person can be both curated and crawled under the same id (the
        # dict merged that); different ids are different people.
        listing = "\n".join(
            f"  - {m.name} (entry {m.entry_id}; {m.origin})" for m in matches.values()
        )
        return None, (
            f"{text!r} matches {len(matches)} verified managers -- say which "
            f"one you mean:\n{listing}"
        )
    return None, _unverifiable(text, warehouse_unread=crawled is None)


def _default_managers() -> List[_Manager]:
    """The managers used when a caller names none: the verified curated list."""
    return [
        _Manager(int(e.entry_id), e.name,
                 "curated elite list, verified against /entry/{id}/")
        for e in _elite_named()
    ]


# -----------------------------------------------------------------------------
# Live API fetches -- all through fpl_data.entry_json, which is the engine's
# RivalsFetcher: enforced pacing, TTL cache shared with the crawl, archive,
# transport-only retries and a hard per-process budget. No bare HTTP here.


def _get_current_gameweek() -> int:
    """Return the current gameweek number.

    Delegates to :func:`fpl_mcp.utils.fpl_data.current_gameweek`, which reads
    the deadline calendar the warehouse already holds (``dim_event``) and only
    falls back to a live bootstrap fetch on a warehouse-less checkout.
    """
    return fpl_data.current_gameweek()


def _missing(manager_id: int, what: str) -> LookupError:
    return LookupError(
        f"the FPL API answered 404 for entry {manager_id} ({what}) -- the "
        f"entry does not exist, or that data is not published yet."
    )


def _fetch_team_picks(manager_id: int, gw: int) -> Dict[str, object]:
    """Fetch the picks for a manager in a given gameweek.

    Args:
        manager_id: The FPL entry ID of the manager.
        gw: Gameweek number (1-38).

    Returns:
        A JSON dictionary with the picks and chip usage.
    """
    body = fpl_data.entry_json(f"entry/{manager_id}/event/{gw}/picks/")
    if body is None:
        raise _missing(manager_id, f"GW{gw} picks: the deadline has not "
                                   f"passed, or no such entry")
    return body


def _fetch_transfers(manager_id: int) -> List[Dict[str, object]]:
    """Fetch all transfers made by a manager in the current season.

    Args:
        manager_id: The FPL entry ID of the manager.

    Returns:
        A list of transfer dicts, each containing keys such as
        ``element_in``, ``element_out``, ``event`` and ``time``.
    """
    body = fpl_data.entry_json(f"entry/{manager_id}/transfers/")
    if body is None:
        raise _missing(manager_id, "transfers")
    return body  # type: ignore[return-value]


def _fetch_manager_history(manager_id: int) -> Dict[str, object]:
    """Fetch the historical performance of a manager.

    Args:
        manager_id: The FPL entry ID of the manager.

    Returns:
        A JSON dictionary with keys such as ``current``, ``past`` and ``chips``.
    """
    body = fpl_data.entry_json(f"entry/{manager_id}/history/")
    if body is None:
        raise _missing(manager_id, "history")
    return body  # type: ignore[return-value]


# -----------------------------------------------------------------------------
# Tools


@mcp.tool()
def get_expert_teams_summary(gw: Optional[int] = None, experts: Optional[List[str]] = None) -> str:
    """Summarise which players are owned by several verified managers at once.

    Fetches each named manager's picks and builds a cross-tabulation of players
    to the managers who own them, most-owned first.

    Names are resolved only against verified sources -- the engine's curated
    elite list and the crawled ``dim_manager`` names. A name that cannot be
    verified is listed as unresolved in the output and is NOT queried; there is
    no name-to-id map in this module to fall back on.

    Args:
        gw: Gameweek to fetch picks for. Defaults to the current gameweek.
        experts: Manager names or numeric entry ids to include. Defaults to the
            curated verified list.

    Returns:
        A multi-line ownership summary. Any manager that could not be verified
        or fetched, and any element id absent from the bootstrap table, is
        named explicitly rather than quietly omitted; an unresolvable player is
        listed with its raw id and no price, never at £0.0m.
    """
    problems: List[str] = []
    managers: List[_Manager] = []
    if experts:
        for ex in experts:
            manager, why = _resolve_manager(ex)
            if manager is None:
                problems.append(f"- {ex!r}: {why}")
            else:
                managers.append(manager)
    else:
        managers = _default_managers()
        if not managers:
            return (
                "No verified managers are available: the curated list "
                "(fpl_edge.ingest.rivals.elite.ELITE_NAMED) could not be "
                "imported. Name managers explicitly, or pass entry ids."
            )
    if not managers:
        return (
            "No manager could be verified, so there is nothing to summarise.\n"
            + "\n".join(problems)
        )

    gameweek = gw or _get_current_gameweek()

    # Build a mapping of player id -> the managers who own them.
    ownership: Dict[Any, List[str]] = {}
    fetch_failures: List[str] = []
    for manager in managers:
        try:
            data = _fetch_team_picks(manager.entry_id, gameweek)
        except Exception as exc:  # noqa: BLE001
            # A manager whose picks we could not read is reported, not dropped:
            # "owned by 2 of 3" and "owned by 2 of 2" are different facts.
            fetch_failures.append(f"- {manager.label}: {type(exc).__name__}: {exc}")
            continue
        for pick in data.get("picks", []):
            elem_id = pick.get("element")
            if elem_id is None:
                continue
            ownership.setdefault(elem_id, []).append(manager.display)

    if not ownership:
        lines = [f"No picks found for the named managers in gameweek {gameweek}."]
        if fetch_failures:
            lines.append("Fetches that failed:")
            lines.extend(fetch_failures)
        if problems:
            lines.append("Names that could not be verified:")
            lines.extend(problems)
        return "\n".join(lines)

    elements_df = fpl_data.get_elements_df().set_index("id")

    rows: List[Dict[str, Any]] = []
    unresolved: List[Any] = []
    for pid, owners in ownership.items():
        player = _lookup_element(elements_df, pid)
        if player is None:
            # The id is not in the bootstrap table. It stays in the cross-tab
            # with its raw id: dropping it would understate a manager's squad
            # and hide the gap, and £0.0m would read as a free player.
            unresolved.append(pid)
            rows.append({
                "player_name": f"unknown player (element {pid})",
                "team": "unknown",
                "position": "?",
                "price": "?",
                "owned_by": ", ".join(sorted(owners)),
                "count": len(owners),
            })
            continue
        rows.append({
            "player_name": f"{player['first_name']} {player['second_name']}",
            "team": player.get("team_name", ""),
            "position": player.get("position", ""),
            "price": _price_m(player),
            "owned_by": ", ".join(sorted(owners)),
            "count": len(owners),
        })
    rows.sort(key=lambda r: (-r["count"], r["player_name"]))

    named = ", ".join(m.display for m in managers)
    header = f"Ownership summary for GW{gameweek} across: {named}\n"
    header += f"{'Player':<25} {'Team':<20} {'Pos':<4} {'Price':<5} Owned by\n"
    header += "-" * 80 + "\n"
    out = header + "\n".join(
        f"{r['player_name']:<25} {r['team']:<20} {r['position']:<4} {r['price']:<5} {r['owned_by']}"
        for r in rows
    )
    if unresolved:
        out += (
            f"\n\nNote: {len(unresolved)} element id(s) "
            f"({', '.join(str(u) for u in unresolved)}) are not in the current "
            f"bootstrap data, so their name, club, position and price are "
            f"unknown. They are listed above rather than dropped. Element ids "
            f"are reassigned every season -- a stale cache or a previous "
            f"season's picks is the usual cause."
        )
    if fetch_failures:
        out += "\n\nPicks that could not be fetched (excluded from the counts above):\n"
        out += "\n".join(fetch_failures)
    if problems:
        out += "\n\nNames that could not be verified (not queried):\n"
        out += "\n".join(problems)
    return out


@mcp.tool()
def get_expert_transfers(expert: str, last_n: int = 5) -> str:
    """Retrieve the latest transfers for a verified manager.

    Args:
        expert: The manager's name, or their numeric entry id. A name is
            resolved only against verified sources; an unverifiable name is
            refused rather than looked up against a stale id.
        last_n: Number of most recent transfers to show (default 5).

    Returns:
        A human-readable summary of the manager's recent transfers. An element
        id absent from the bootstrap table is reported as unknown with its raw
        id and no price -- never as a made-up name or a £0.0m price.
    """
    manager, why = _resolve_manager(expert)
    if manager is None:
        return why or f"Could not resolve {expert!r}."
    try:
        transfers = _fetch_transfers(manager.entry_id)
    except Exception as e:  # noqa: BLE001
        return f"Failed to fetch transfers for {manager.label}: {e}"
    if not transfers:
        return f"No transfers recorded for {manager.label} this season."
    # Load elements for name lookup
    elements_df = fpl_data.get_elements_df().set_index("id")
    # Sort transfers by time descending (ISO timestamps) and take last_n
    transfers_sorted = sorted(transfers, key=lambda t: t.get("time", ""), reverse=True)[:last_n]
    lines = [f"Latest {min(last_n, len(transfers_sorted))} transfers for {manager.label}:"]
    unresolved: List[Any] = []

    def _describe(elem_id: Any) -> str:
        """Render one side of a transfer, or say the id is unknown.

        The previous version fell back to a price of 0.0, which reads as a
        free player rather than as missing data. A number we do not have is
        not printed as a number.
        """
        player = _lookup_element(elements_df, elem_id)
        if player is None:
            unresolved.append(elem_id)
            return f"unknown player (element {elem_id}, price unknown)"
        player_name = f"{player['first_name']} {player['second_name']}"
        price = _price_m(player)
        return f"{player_name} (£{price}m)" if price != "?" else f"{player_name} (price unknown)"

    for tr in transfers_sorted:
        gw = tr.get("event")
        lines.append(
            f"GW{gw}: In {_describe(tr.get('element_in'))}, "
            f"Out {_describe(tr.get('element_out'))}"
        )
    if unresolved:
        lines.append(
            f"Note: {len(unresolved)} element id(s) "
            f"({', '.join(str(u) for u in unresolved)}) are not in the current "
            f"bootstrap data, so their name and price are unknown. Element ids "
            f"are reassigned every season -- a stale cache or a transfer from a "
            f"previous season is the usual cause."
        )
    return "\n".join(lines)


@mcp.tool()
def get_manager_history(manager: str) -> str:
    """Summarise the historical performance of a verified manager.

    Returns past-season ranks, chip usage and this season's gameweek scores.

    Args:
        manager: The manager's name, or their numeric entry id. A name is
            resolved only against verified sources -- the curated elite list
            and the crawled ``dim_manager`` names. A name that cannot be tied
            to a verified entry is refused, because the alternative is printing
            a stranger's ranks under the name you asked about.

    Returns:
        A formatted summary, headed by the entry id actually queried and how
        that identity was established.
    """
    resolved, why = _resolve_manager(manager)
    if resolved is None:
        return why or f"Could not resolve {manager!r}."
    try:
        history = _fetch_manager_history(resolved.entry_id)
    except Exception as e:  # noqa: BLE001
        return f"Failed to fetch history for {resolved.label}: {e}"
    output_lines = [f"History for {resolved.label}:"]
    # Past seasons
    past = history.get("past", [])
    if past:
        output_lines.append("Past seasons:")
        for season in past:
            season_name = season.get("season_name")
            rank = season.get("rank")
            points = season.get("total_points")
            output_lines.append(f"- {season_name}: {points} pts, Rank {rank}")
    # Chips used this season
    chips = history.get("chips", [])
    if chips:
        used = [f"GW{c.get('event')}: {str(c.get('name', '')).replace('_', ' ').title()}"
                for c in chips]
        output_lines.append("Chips used this season: " + ", ".join(used))
    # Current season scores
    current = history.get("current", [])
    if current:
        # Compute total points and average score
        total_points = sum(ev.get("points", 0) for ev in current)
        avg_points = total_points / len(current)
        highest = max(current, key=lambda ev: ev.get("points", 0))
        high_gw = highest.get("event")
        high_points = highest.get("points")
        output_lines.append(
            f"Current season: {len(current)} gameweeks, total {total_points} pts, "
            f"average {avg_points:.1f} pts, highest GW{high_gw} with {high_points} pts."
        )
    return "\n".join(output_lines)
