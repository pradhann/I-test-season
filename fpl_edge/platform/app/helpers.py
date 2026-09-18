"""Shared by every route group: the request models, the dependency bundle
they are handed, and the three warehouse readers two of them call.

This module names nothing else in the package, which is what keeps ``factory``
above the route modules and the route modules above this one. ``serve`` is the
exception: it reaches for ``create_app`` inside the function body, beside the
uvicorn import that was already there, so the edge never exists at import time.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from fpl_edge.store.warehouse import DEFAULT_DB

UTC = dt.UTC


#: The season the profile-fetch route defaults to; one string, next to the
#: request model that carries it, rather than a fourth copy of the literal.
SEASON_DEFAULT = "2026-27"


class RunRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)

    # Argus's run tool takes params at the top level and so does the JS client
    # that will call this. Accepting both shapes costs one method and removes a
    # class of "why is my panel empty" that is really a wrapper mismatch.
    model_config = {"extra": "allow"}

    def resolved(self) -> dict[str, Any]:
        if self.params:
            return self.params
        extra = dict(self.__pydantic_extra__ or {})
        return extra


class QueryRequest(BaseModel):
    sql: str
    as_of: dt.datetime | None = None
    max_rows: int | None = None


class TurnRequest(BaseModel):
    text: str


class SolveRequest(BaseModel):
    """``mode`` picks the CLI; ``options`` are the Planner rail's settings for
    mode ``transfers`` (horizon, max_hits, chips, must_keep, ban, seconds,
    max_candidates; see ``solve_runner.normalise_options``). Any other mode
    ignores them."""

    mode: str = "both"
    options: dict[str, Any] | None = None


class IngestLinkRequest(BaseModel):
    url: str


class DiscardRequest(BaseModel):
    """Why an item is being hidden. Optional, and recorded when given."""

    reason: str = ""


class GameweekRequest(BaseModel):
    """The owner's gameweek for an item, recorded AS A CORRECTION."""

    gameweek: int
    note: str = ""


class FetchProfileRequest(BaseModel):
    """Which season to fetch a player's Understat profile for."""

    season: str = SEASON_DEFAULT


class PipelineRunRequest(BaseModel):
    """Confirmation for a metered pipeline trigger. Free tasks ignore it."""

    confirm: bool = False


class SourceFetchRequest(BaseModel):
    """How far back one manual per-source fetch should reach.

    Default 1 day, because "fetch latest" means the newest episodes and a
    conditional fetch of one feed is cheap. Clamped server-side: a backfill
    the browser asked for must not be able to re-download a decade of
    Sky Sports FPL because a field was typed wrong.
    """

    backfill_days: int = 1
    #: YouTube only: how many videos deep the channel page is read.
    max_videos: int = 6


@dataclass
class Deps:
    """What a route group needs from the app it is mounted on.

    ``app`` is carried rather than the state dicts themselves because three
    groups keep their in-process state on ``app.state`` and a test replaces
    ``app.state.link_jobs`` after the app is built, so the lookup has to
    happen per request, not at wiring time.
    """

    app: FastAPI
    db_path: Path
    #: The owner's agent, rooted where the operator's transcripts already are.
    chat_agent: Any
    #: user_id -> that user's agent. One root per user, so a conversation id
    #: from another user's directory simply does not exist and the store's own
    #: UnknownConversation is the correct answer without an ownership check.
    _chat_agents: dict[str, Any] = field(default_factory=dict)

    def agent_for(self, user: Any) -> Any:
        """The chat agent whose transcripts belong to ``user``.

        The owner keeps the agent built at app construction, which is the one
        a test points at a temp root. Everybody else gets an agent rooted at
        their own directory, built once and cached for the process.
        """
        if user is None or getattr(user, "is_owner", True):
            return self.chat_agent
        user_id = str(user.user_id)
        agent = self._chat_agents.get(user_id)
        if agent is None:
            from fpl_edge.platform.chat_agent import ChatAgent
            from fpl_edge.platform.users import CHAT_DIR

            agent = ChatAgent(root=user.path(CHAT_DIR))
            self._chat_agents[user_id] = agent
        return agent



def _monitor_definitions(db_path: Path) -> dict[str, Any]:
    """Monitor definitions read from the DAG, which owns the schedule.

    Read-only reflection over :mod:`fpl_edge.jobs.deadline_dag`: the offsets
    ARE the schedule spec (DESIGN §3), so restating them here would create a
    second source of truth that silently disagrees the first time one moves.
    """
    try:
        from fpl_edge.jobs import deadline_dag as dag
    except Exception as exc:  # noqa: BLE001
        return {"monitors": [], "empty": True,
                "reason": f"deadline DAG not importable: {type(exc).__name__}: {exc}"}

    monitors = []
    for name, offset in getattr(dag, "DEADLINE_OFFSETS", {}).items():
        hours = offset.total_seconds() / 3600.0
        monitors.append({
            "name": name,
            "kind": "alert",
            "trigger": "deadline-relative",
            "schedule": f"T-{hours:g}h before each gameweek deadline",
            "offset_hours": hours,
            "doc": (getattr(dag.TASKS.get(name), "__doc__", "") or "").strip().split("\n")[0],
        })
    nightly = getattr(dag, "NIGHTLY_TASK", None)
    if nightly:
        hour = getattr(dag, "NIGHTLY_LOCAL_HOUR", 2)
        monitors.append({
            "name": nightly,
            "kind": "alert",
            "trigger": "wall-clock",
            "schedule": f"nightly {hour:02d}:00 UK",
            "offset_hours": None,
            "doc": (getattr(dag.TASKS.get(nightly), "__doc__", "") or "").strip().split("\n")[0],
        })

    firings: list[dict[str, Any]] = []
    reason = None
    if db_path.exists():
        try:
            from fpl_edge.platform.query import read_copy

            with read_copy(db_path) as wh:
                exists = wh.sql(
                    "SELECT count(*) AS n FROM information_schema.tables "
                    "WHERE table_name = 'dag_firing'"
                ).iloc[0]["n"]
                if exists:
                    df = wh.sql(
                        "SELECT task, due_utc, outcome, detail, ran_utc FROM dag_firing "
                        "ORDER BY due_utc DESC LIMIT 20"
                    )
                    firings = [
                        {k: (None if v is None else str(v)) for k, v in row.items()}
                        for row in df.to_dict(orient="records")
                    ]
                else:
                    reason = "no dag_firing table yet; the DAG has never ticked."
        except Exception as exc:  # noqa: BLE001
            reason = f"could not read firings: {type(exc).__name__}: {exc}"

    return {
        "monitors": monitors,
        "recent_firings": firings,
        "empty": not monitors,
        "reason": reason,
        "note": (
            "Definitions are read from fpl_edge.jobs.deadline_dag, which owns the "
            "schedule. Triggers are deterministic Python; an LLM only polishes copy "
            "after a firing and never decides one."
        ),
    }


_PLAYER_LOOKUP_SQL = """
    SELECT p.code, p.web_name, p.position, p.team_code,
           t.short_name AS team, s.price_tenths
    FROM (
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY season, code
                                         ORDER BY as_of DESC) rn
            FROM dim_player WHERE season = ?
        ) WHERE rn = 1
    ) p
    LEFT JOIN (
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY season, code
                                         ORDER BY as_of DESC) rn
            FROM fact_player_state WHERE season = ?
        ) WHERE rn = 1
    ) s USING (season, code)
    LEFT JOIN (
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY season, team_code
                                         ORDER BY as_of DESC) rn
            FROM dim_team WHERE season = ?
        ) WHERE rn = 1
    ) t ON t.team_code = p.team_code
"""
_POS_NAME = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def _player_lookup(wh, season: str | None, wanted: set[int]) -> dict[str, Any]:
    """``{code: {name, pos, team, team_code, price}}`` for ``wanted`` (all
    players when ``wanted`` is empty), from the latest warehouse rows."""
    df = wh.sql(_PLAYER_LOOKUP_SQL, [season, season, season])
    players: dict[str, Any] = {}
    for row in df.to_dict(orient="records"):
        code = int(row["code"])
        if wanted and code not in wanted:
            continue
        price = row.get("price_tenths")
        team_code = row.get("team_code")
        players[str(code)] = {
            "name": str(row.get("web_name") or code),
            "pos": _POS_NAME.get(int(row["position"]) if row.get("position") is not None else 0, "?"),
            "team": None if row.get("team") is None else str(row["team"]),
            "team_code": None if team_code is None or team_code != team_code else int(team_code),
            "price": None if price is None or price != price else round(float(price) / 10.0, 1),
        }
    return players


def _deadline_calendar(wh, season: str | None, now: dt.datetime) -> dict[str, Any]:
    """Next and last deadlines around ``now`` (one row per gw: dim_event keeps
    an as_of history, so the latest deadline per gw is the one that counts)."""
    df = wh.sql(
        "SELECT gw, max(deadline_utc) AS deadline_utc FROM dim_event "
        + ("WHERE season = ? " if season else "")
        + "GROUP BY gw ORDER BY deadline_utc",
        [season] if season else [],
    )
    out: dict[str, Any] = {"next_gw": None, "next_deadline_utc": None,
                           "last_gw": None, "last_deadline_utc": None}
    for row in df.to_dict(orient="records"):
        d = row["deadline_utc"]
        if d is None or d != d:
            continue
        d = d.to_pydatetime() if hasattr(d, "to_pydatetime") else d
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        if d > now:
            if out["next_deadline_utc"] is None:
                out["next_gw"] = int(row["gw"])
                out["next_deadline_utc"] = d.isoformat()
        else:
            out["last_gw"] = int(row["gw"])
            out["last_deadline_utc"] = d.isoformat()
    return out


def _brief_thresholds() -> dict:
    """The dashboard brief's thresholds, or an empty dict if it cannot
    be imported. The API must serve a plan even when a panel module is
    broken, so a failed import degrades to the literal default."""
    try:
        from fpl_edge.platform.scripts.brief import THRESHOLDS
    except Exception:  # noqa: BLE001 - a panel import must not break the API
        return {}
    return dict(THRESHOLDS)


# The deterministic QuestionRouter route is DELETED (CHAT_ARCHITECTURE §2
# decision 1): one brain. Every message goes to the agent conversations; the
# router's genuinely good answers live on as toolbelt tools the agent calls.


def serve(host: str = "127.0.0.1", port: int = 8321,
          db: Path | str = DEFAULT_DB, reload: bool = False) -> None:
    # create_app is imported here rather than at module level: factory imports
    # every route module and every route module imports this one, so naming
    # factory at the top of this file would close the loop.
    import uvicorn

    from fpl_edge.platform.app.factory import create_app

    uvicorn.run(create_app(db), host=host, port=port, reload=reload)
