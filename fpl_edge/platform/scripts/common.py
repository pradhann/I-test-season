"""Shared helpers for panel scripts.

The rule every helper here exists to serve: **a panel with no data says so.**
Not zero rows silently rendered as an empty table, not a plausible-looking
placeholder row -- an explicit ``{empty: true, reason: "..."}`` that names what
is missing and what would fix it. A dashboard that fabricates is worse than no
dashboard, because it is trusted at the deadline.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd

from fpl_edge.platform.query import guarded_query

UTC = dt.timezone.utc
SEASON_DEFAULT = "2026-27"

#: The projection artefact `make solve` writes. Panels read it rather than
#: re-running a simulation: a panel has a 10s budget and a solve is minutes.
PROJECTION_NAME = "gw1_projection.parquet"

POSITION_NAME = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def empty(reason: str) -> dict[str, Any]:
    """The one sanctioned way for a script to return nothing."""
    return {"empty": True, "reason": reason}


def season_param() -> dict[str, Any]:
    return {"type": "string", "default": SEASON_DEFAULT, "minLength": 4}


def source_dir(wh) -> Path:
    """The directory of the *original* warehouse, not the read copy.

    Scripts run against a temp copy of the database, so ``wh.path.parent`` is a
    scratch directory with no artefacts in it. ``run_script`` stamps the real
    path on the handle; without this, every artefact lookup would silently miss.
    """
    src = getattr(wh, "source_path", None)
    return Path(src).parent if src is not None else Path(wh.path).parent


def load_projection(wh) -> pd.DataFrame | None:
    """The cached per-player simulation artefact, or None.

    Mirrors ``QuestionRouter._projection``: the solve script writes ``code`` as
    the index and every consumer wants it as a column, so normalise once here
    rather than in each caller.
    """
    path = source_dir(wh) / PROJECTION_NAME
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if "code" not in df.columns:
        df = df.reset_index()
    return df


def q(wh, sql: str, params: tuple = ()) -> pd.DataFrame:
    """Run one guarded read on an already-open read copy.

    Panel scripts do not get a second data path: this funnels to exactly the
    same guard the ``/api/query`` endpoint and the chat tool use.
    """
    res = guarded_query(sql, params, warehouse=wh)
    return pd.DataFrame(res.rows, columns=res.columns or None)


def latest_as_of(wh, table: str, season: str | None = None) -> str | None:
    where = "WHERE season = ?" if season else ""
    params = (season,) if season else ()
    df = q(wh, f"SELECT max(as_of) AS a FROM {table} {where}", params)
    if df.empty or df.iloc[0]["a"] is None:
        return None
    return str(df.iloc[0]["a"])


def next_gw(wh, season: str, now: dt.datetime | None = None) -> int | None:
    """First gameweek whose deadline has not passed. None if none is known."""
    when = (now or dt.datetime.now(UTC)).astimezone(UTC)
    df = q(
        wh,
        "SELECT gw, deadline_utc FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY season, gw ORDER BY as_of DESC) rn"
        "  FROM dim_event WHERE season = ?"
        ") WHERE rn = 1 AND deadline_utc > ? ORDER BY deadline_utc LIMIT 1",
        (season, when),
    )
    if df.empty:
        return None
    return int(df.iloc[0]["gw"])


# --------------------------------------------------------------------------
# The user's own squad, read once for every panel that needs it.
#
# Moved here from platform/scripts/ownership.py (ARCHITECTURE_REVIEW.md check
# 6, move M4). It was the only panel-to-panel import in the package:
# platform/scripts/creators.py reached into a sibling panel for a private name.
# A helper two panels share is shared code, and shared panel code lives here.
# --------------------------------------------------------------------------


def _squad_state(wh, season: str, ctx=None
                 ) -> tuple[dict[int, dict] | None, dict[str, Any]]:
    """The user's 15 with their FPL multipliers, or (None, why-not).

    Same read path as squad_overview (QuestionRouter._team_state): private API,
    then public picks, then the manually entered squad. Any failure — network
    down, nothing published yet — degrades to unreadable, never to a crash.

    The multiplier is the *my multiplier* term of the rank identity, so it is
    read rather than inferred, and it is reported ONLY when the read path
    actually carried one. A manually entered 15 has no armband and no bench
    order: those rows come back with ``mult: None``, which the UI renders as
    "owned, role unknown" — never as a silent 1×.
    """
    from fpl_edge.platform.users import owner_context

    # ``ctx`` is the requesting user. A caller that passes none is a job, a CLI
    # command or the Telegram bot, all of which run as the operator; a caller
    # inside a request passes the request's context, because the entry id and
    # the FPL login have to come from the same person.
    ctx = ctx if ctx is not None else owner_context()
    try:
        from fpl_edge.interfaces.qa import QuestionRouter

        router = QuestionRouter(wh, season=season, entry_id=int(ctx.entry_id),
                                user=ctx)
        state = router._team_state()
    except Exception as exc:  # noqa: BLE001 — a panel reports, it does not crash
        return None, {
            "readable": False, "has_multipliers": False,
            "note": f"squad unreadable ({type(exc).__name__}); coverage column blank",
        }
    if state is None or state.picks is None:
        return None, {
            "readable": False, "has_multipliers": False,
            "note": "no squad visible for your entry yet; coverage column blank",
        }

    # A pre-deadline squad read carries NO multiplier at all -- the public
    # picks payload publishes multipliers only once the gameweek locks. That
    # used to null `mult` for all fifteen, which nulled the EO side of the rank
    # identity for every row on the page: the tab's headline measure went blank
    # on exactly the day it is most wanted.
    #
    # The multiplier is not guessed here, it is DERIVED from facts the read did
    # carry, using the scoring rule itself: a benched player scores 0x, a
    # starter 1x, the captain 2x -- or 3x when the triple-captain chip is
    # active, which `chips_used` reports for this gameweek. Only the captain
    # row is ever ambiguous, and only when the chip cannot be read; that one
    # row is marked rather than the other fourteen being thrown away.
    #
    # `mult_source` travels with every row so the UI can say which it is, and
    # a squad with no roles at all (a manually entered 15 has no armband and no
    # bench order) still yields mult=None -- derived from nothing is nothing.
    tc_active = False
    chip_read = False
    try:
        used = getattr(state, "chips_used", None) or ()
        this_gw = getattr(state, "gw", None)
        chip_read = True
        for chip, cgw in used:
            name = getattr(chip, "value", chip)
            if str(name) == "3xc" and cgw == this_gw:
                tc_active = True
    except Exception:  # noqa: BLE001 -- an unreadable chip is not a crash
        chip_read = False

    cap_mult = 3 if tc_active else 2
    roles: dict[int, dict] = {}
    for p in state.picks:
        raw = getattr(p, "multiplier", None)
        mult = None
        if isinstance(raw, (int, float)) and raw == raw:
            mult = int(raw)
        cap = bool(getattr(p, "is_captain", False) or False)
        starter = getattr(p, "is_starter", None)
        role = None
        if cap:
            role = "captain"
        elif isinstance(starter, bool):
            role = "start" if starter else "bench"
        elif mult is not None:
            role = "start" if mult >= 1 else "bench"

        src = "read" if mult is not None else None
        if mult is None and role is not None:
            mult = {"captain": cap_mult, "start": 1, "bench": 0}[role]
            src = "derived"
        roles[int(p.code)] = {"mult": mult, "role": role, "mult_source": src}

    source = getattr(state.provenance, "name", str(state.provenance))
    gw = getattr(state, "gw", None)
    cap_code = next((c for c, r in roles.items() if r["role"] == "captain"), None)
    meta = {
        "readable": True,
        "source": str(source),
        "gw": int(gw) if isinstance(gw, (int, float)) and gw == gw else None,
        "n": len(roles),
        "has_multipliers": any(r["mult"] is not None for r in roles.values()),
        "multipliers_read": any(r["mult_source"] == "read" for r in roles.values()),
        "multipliers_derived": any(
            r["mult_source"] == "derived" for r in roles.values()),
        "captain_multiplier_certain": chip_read,
        "captain": None,          # filled in by the caller, which knows names
        "note": f"your squad read via {source}",
    }
    meta["_captain_code"] = cap_code
    return roles, meta
