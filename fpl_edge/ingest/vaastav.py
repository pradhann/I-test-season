"""Ingest vaastav/Fantasy-Premier-League history into the point-in-time warehouse.

Source
------

https://github.com/vaastav/Fantasy-Premier-League, read as raw CSV from
``raw.githubusercontent.com``. The layout was verified against the live repo
(not from memory -- it drifts between seasons)::

    data/<season>/players_raw.csv       id, code, element_type, team, team_code, names
    data/<season>/teams.csv             id, code, name, short_name
    data/<season>/fixtures.csv          id, event, kickoff_time, team_h, team_a, scores
    data/<season>/gws/merged_gw.csv     one row per player per fixture, keyed on `element`

Column sets drift: 2024-25 adds ``mng_*``; 2025-26 adds ``tackles``,
``clearances_blocks_interceptions``, ``recoveries`` and
``defensive_contribution``; 2018-19 carries a stray ``id``. Anything absent is
written as NULL, never as zero -- "the stat did not exist" and "the player
recorded none of it" are different facts and a model must be able to tell them
apart.

as_of, which is the whole point
-------------------------------

Every row's ``as_of`` is the instant the fact became *publicly observable*, per
``docs/rules.md``. Not kickoff, not the git commit date, not now.

``fact_player_fixture``
    ``as_of`` = **09:00 UK on the day after the gameweek's final match**, which
    is the registry's verified ``deadlines.points_final_at``. Bonus points and
    the final BPS ranking are provisional until then, so a gameweek that has
    kicked off is genuinely unknown at that point. 09:00 UK is 08:00Z under BST
    and 09:00Z under GMT; the conversion goes through ``Europe/London`` rather
    than a fixed offset.

``fact_fixture``
    Two rows per fixture, because a fixture carries two facts with different
    observability. The *schedule* row (teams, gameweek, kickoff, ``finished =
    false``, scores NULL) uses the season's GW1 deadline, by which point the
    full fixture list is unambiguously public. The *result* row (``finished =
    true`` plus the scoreline) uses kickoff + 2h, i.e. approximately full time,
    which is when a scoreline is public -- earlier than points finalisation, and
    correctly so.

``dim_player``
    The gameweek deadline at which that (position, club, name) fact held. A row
    is emitted at a player's first appearance and thereafter only when something
    changes, so a January transfer becomes visible at the January deadline and
    not before.

``dim_team``
    The season's GW1 deadline.

``fact_player_state``
    The gameweek deadline. vaastav's ``value`` column is the player's price for
    that gameweek, and the honest instant for it is the deadline at which that
    price was the price you would have paid.

Historical deadlines are not shipped by vaastav, so they are *derived* as
``first kickoff of the gameweek - 90 minutes``, using the verified rule
``deadlines.offset_before_first_kickoff_minutes``. Spot-checked against known
values: 2022-23 GW1 -> 2022-08-05T17:30Z, 2025-26 GW1 -> 2025-08-15T17:30Z.
Both correct.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from fpl_edge.ingest.player_mapping import (
    POSITION_STRINGS,
    PlayerCodeIndex,
    ResolutionReport,
    SeasonIndexReport,
)
from fpl_edge.store import Warehouse
from fpl_edge.types import Position

RAW_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"

#: Where downloaded CSVs are archived. Mirrors the upstream layout.
CACHE_ROOT = Path("data/raw/vaastav")

UK = ZoneInfo("Europe/London")
UTC = dt.timezone.utc

#: Verified: `deadlines.offset_before_first_kickoff_minutes` in the rule registry.
DEADLINE_OFFSET = dt.timedelta(minutes=90)

#: 90 minutes + half time + stoppage. Used only for the fixture *scoreline* row.
FULL_TIME_DELTA = dt.timedelta(hours=2)

#: Verified: `deadlines.points_final_at` == "09:00 UK the day after the final match".
POINTS_FINAL_HOUR = 9

#: A Premier League season carries 550-900 selectable players. Below this the
#: ownership denominator (see ``build_player_dim_and_state``) cannot be trusted.
MIN_PLAYERS_FOR_OWNERSHIP = 300

#: The three most recent completed seasons plus 2025-26, which is also complete.
DEFAULT_SEASONS: tuple[str, ...] = ("2022-23", "2023-24", "2024-25", "2025-26")

FILE_PLAYERS_RAW = "players_raw.csv"
FILE_TEAMS = "teams.csv"
FILE_FIXTURES = "fixtures.csv"
FILE_MERGED_GW = "gws/merged_gw.csv"

#: merged_gw column -> fact_player_fixture column. Columns absent in a season are
#: written as NULL.
STAT_COLUMNS: Mapping[str, str] = {
    "minutes": "minutes",
    "goals_scored": "goals_scored",
    "assists": "assists",
    "clean_sheets": "clean_sheets",
    "goals_conceded": "goals_conceded",
    "own_goals": "own_goals",
    "penalties_saved": "penalties_saved",
    "penalties_missed": "penalties_missed",
    "yellow_cards": "yellow_cards",
    "red_cards": "red_cards",
    "saves": "saves",
    "bonus": "bonus",
    "bps": "bps",
    "starts": "starts",
    "tackles": "tackles",
    "clearances_blocks_interceptions": "clearances_blocks_interceptions",
    "recoveries": "recoveries",
    "defensive_contribution": "defensive_contribution",
    "total_points": "total_points",
}

FLOAT_STAT_COLUMNS: Mapping[str, str] = {
    "expected_goals": "expected_goals",
    "expected_assists": "expected_assists",
    "expected_goals_conceded": "expected_goals_conceded",
}


class MissingSourceError(FileNotFoundError):
    """A required upstream CSV is not available locally and network is disabled."""


# ---------------------------------------------------------------------------
# source access
# ---------------------------------------------------------------------------


class VaastavRepo:
    """Reads vaastav CSVs from a local mirror, optionally filling it from GitHub.

    Tests point ``root`` at ``tests/fixtures/vaastav`` with ``offline=True`` so
    the unit suite never touches the network. Production points it at
    ``data/raw/vaastav`` with ``offline=False``, which archives every byte it
    downloads so a backtest is replayable from disk afterwards.
    """

    def __init__(
        self,
        root: Path | str = CACHE_ROOT,
        *,
        base_url: str = RAW_BASE,
        offline: bool = True,
        timeout: float = 60.0,
    ) -> None:
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")
        self.offline = offline
        self.timeout = timeout
        self._client: Any = None  # httpx.Client, imported only on the network path
        #: filled as files are read; ``relpath -> (sha256, n_bytes, from_network)``
        self.provenance: dict[str, tuple[str, int, bool]] = {}

    def local_path(self, season: str, name: str) -> Path:
        return self.root / season / name

    def _download(self, season: str, name: str) -> bytes:
        import httpx  # local import so the offline path has no httpx requirement

        from fpl_edge.ingest.http import USER_AGENT

        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
        url = f"{self.base_url}/{season}/{name}"
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp.content

    def read_bytes(self, season: str, name: str) -> bytes:
        path = self.local_path(season, name)
        from_network = False
        if path.exists():
            payload = path.read_bytes()
        elif self.offline:
            raise MissingSourceError(
                f"{path} is absent and this repo is offline. Run "
                f"`uv run python scripts/ingest_history.py --seasons {season}` with "
                "network access to populate the mirror, or point --root at one."
            )
        else:
            payload = self._download(season, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            from_network = True
        self.provenance[f"{season}/{name}"] = (
            hashlib.sha256(payload).hexdigest(), len(payload), from_network,
        )
        return payload

    def read_csv(self, season: str, name: str) -> pd.DataFrame:
        return pd.read_csv(io.BytesIO(self.read_bytes(season, name)), low_memory=False)

    def has(self, season: str, name: str) -> bool:
        return self.local_path(season, name).exists() or not self.offline

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "VaastavRepo":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# as_of derivation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GameweekWindow:
    """The three instants a gameweek's facts become observable at."""

    gw: int
    first_kickoff_utc: dt.datetime
    last_kickoff_utc: dt.datetime

    @property
    def deadline_utc(self) -> dt.datetime:
        """90 minutes before the gameweek's first kickoff (verified rule)."""
        return self.first_kickoff_utc - DEADLINE_OFFSET

    @property
    def points_final_utc(self) -> dt.datetime:
        """09:00 UK on the day after the gameweek's final match (verified rule).

        The UK date of the final kickoff is what "the day after" is relative to,
        so a 20:00 UK Monday kickoff finalises 09:00 UK Tuesday. Going through
        ``Europe/London`` rather than a fixed offset is what makes this correct
        on both sides of the BST boundary.
        """
        uk_date = self.last_kickoff_utc.astimezone(UK).date() + dt.timedelta(days=1)
        local = dt.datetime.combine(uk_date, dt.time(POINTS_FINAL_HOUR), tzinfo=UK)
        return local.astimezone(UTC)


def _to_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, format="ISO8601", errors="coerce")


def _int64(series: pd.Series) -> pd.Series:
    """Coerce to nullable Int64 without exploding on float NaN or stray strings.

    Missing stays missing. A column absent from an older season must arrive at
    the warehouse as NULL, never as 0: "the stat did not exist yet" and "the
    player recorded none of it" are different facts.
    """
    return pd.to_numeric(series, errors="coerce").round().astype("Int64")


def build_calendar(fixtures: pd.DataFrame) -> dict[int, GameweekWindow]:
    """Derive each gameweek's deadline and points-finalisation instant.

    Fixtures with no ``event`` (unscheduled at the time of capture) contribute
    nothing: they cannot define a gameweek's boundaries.
    """
    fx = fixtures.copy()
    fx["kickoff_utc"] = _to_utc(fx["kickoff_time"])
    fx = fx[fx["event"].notna() & fx["kickoff_utc"].notna()]
    out: dict[int, GameweekWindow] = {}
    for gw, grp in fx.groupby(fx["event"].astype(int)):
        out[int(gw)] = GameweekWindow(
            gw=int(gw),
            first_kickoff_utc=grp["kickoff_utc"].min().to_pydatetime(),
            last_kickoff_utc=grp["kickoff_utc"].max().to_pydatetime(),
        )
    return out


def season_epoch(calendar: Mapping[int, GameweekWindow]) -> dt.datetime:
    """The instant a season's static facts (teams, fixture list) are public.

    Conservatively the first gameweek's deadline. The real Premier League
    fixture list drops in June, but vaastav does not carry that date and
    inventing one would be worse than being late.
    """
    if not calendar:
        raise ValueError("cannot derive a season epoch from an empty calendar")
    return calendar[min(calendar)].deadline_utc


# ---------------------------------------------------------------------------
# per-table builders
# ---------------------------------------------------------------------------


@dataclass
class SeasonIngestReport:
    """Everything the caller needs to judge whether the load is trustworthy."""

    season: str
    rows: dict[str, int] = field(default_factory=dict)
    index_report: SeasonIndexReport | None = None
    resolution: ResolutionReport | None = None
    dropped_manager_rows: int = 0
    dropped_unmatched_rows: int = 0
    dropped_no_fixture: int = 0
    dropped_no_calendar: int = 0
    dropped_duplicate_rows: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        return self.resolution.match_rate if self.resolution else 1.0

    def render(self) -> str:
        lines = [f"[{self.season}]"]
        for table, n in self.rows.items():
            lines.append(f"    {table:24s} {n:>8,}")
        if self.resolution:
            lines.append(f"    {self.resolution.render()}")
        if self.index_report:
            ir = self.index_report
            lines.append(
                f"    identity: {ir.players_indexed} players indexed from "
                f"{ir.rows} players_raw rows, {ir.managers_dropped} manager elements "
                f"excluded, {len(ir.ambiguous_names)} names not unique in-season"
            )
        lines.append(
            f"    dropped: manager={self.dropped_manager_rows} "
            f"unmatched={self.dropped_unmatched_rows} "
            f"no_fixture={self.dropped_no_fixture} "
            f"no_calendar={self.dropped_no_calendar} "
            f"exact_duplicates={self.dropped_duplicate_rows}"
        )
        for w in self.warnings:
            lines.append(f"    WARNING: {w}")
        return "\n".join(lines)


def _team_code_maps(teams: pd.DataFrame) -> tuple[dict[int, int], dict[str, int]]:
    by_id = {int(r.id): int(r.code) for r in teams.itertuples(index=False)}
    by_name = {str(r.name): int(r.code) for r in teams.itertuples(index=False)}
    return by_id, by_name


def build_teams(season: str, teams: pd.DataFrame, as_of: dt.datetime) -> pd.DataFrame:
    return pd.DataFrame({
        "season": season,
        "team_code": teams["code"].astype(int),
        "team_id": teams["id"].astype(int),
        "name": teams["name"].astype(str),
        "short_name": teams["short_name"].astype(str),
        "as_of": as_of,
    })


def build_fixtures(
    season: str,
    fixtures: pd.DataFrame,
    team_code_by_id: Mapping[int, int],
    epoch: dt.datetime,
) -> tuple[pd.DataFrame, int]:
    """Two rows per fixture: the schedule (public early) and the result (full time)."""
    fx = fixtures.copy()
    fx["kickoff_utc"] = _to_utc(fx["kickoff_time"])
    unknown_team = fx[
        ~fx["team_h"].astype(int).isin(team_code_by_id)
        | ~fx["team_a"].astype(int).isin(team_code_by_id)
    ]
    fx = fx.drop(index=unknown_team.index)

    base = pd.DataFrame({
        "season": season,
        "fixture_id": fx["id"].astype(int),
        "gw": _int64(fx["event"]),
        "kickoff_utc": fx["kickoff_utc"],
        "home_team_code": fx["team_h"].astype(int).map(team_code_by_id).astype(int),
        "away_team_code": fx["team_a"].astype(int).map(team_code_by_id).astype(int),
    })

    schedule = base.assign(
        finished=False,
        home_score=pd.array([None] * len(base), dtype="Int64"),
        away_score=pd.array([None] * len(base), dtype="Int64"),
        as_of=pd.Series([epoch] * len(base), index=base.index, dtype="datetime64[ns, UTC]"),
    )

    played = fx["finished"].fillna(False).astype(bool) & fx["kickoff_utc"].notna()
    result = base.loc[played].assign(
        finished=True,
        home_score=_int64(fx.loc[played, "team_h_score"]),
        away_score=_int64(fx.loc[played, "team_a_score"]),
        # Approximately full time: 90 minutes plus half time plus stoppage. This
        # is when a scoreline is public, which is earlier than -- and distinct
        # from -- when the gameweek's FPL points are finalised.
        as_of=(fx.loc[played, "kickoff_utc"] + FULL_TIME_DELTA).clip(lower=epoch),
    )

    out = pd.concat([schedule, result], ignore_index=True)
    return out, len(unknown_team)


def _position_from_row(position_str: object, fallback: Any = None) -> int | None:
    """Map a merged_gw position string to an element_type, dropping non-players.

    Routed through :meth:`Position.from_api` so that element_type 5 (Manager)
    raises rather than being coerced -- the exception is the design.
    """
    raw = POSITION_STRINGS.get(str(position_str).strip().upper())
    if raw is None and fallback is not None and not pd.isna(fallback):
        raw = int(fallback)
    if raw is None:
        return None
    try:
        return int(Position.from_api(raw))
    except ValueError:
        return None


def build_player_fixture(
    season: str,
    gw_rows: pd.DataFrame,
    calendar: Mapping[int, GameweekWindow],
) -> tuple[pd.DataFrame, int]:
    """One row per (code, fixture) with as_of = the gameweek's finalisation instant."""
    df = gw_rows.copy()
    df["gw"] = df["GW"].astype(int)
    known = df["gw"].isin(calendar)
    dropped = int((~known).sum())
    df = df[known]
    if df.empty:
        return pd.DataFrame(), dropped

    out = pd.DataFrame({
        "season": season,
        "code": df["code"].astype(int),
        "fixture_id": df["fixture"].astype(int),
        "gw": df["gw"],
        "was_home": df["was_home"].astype(bool),
        "as_of": df["gw"].map(lambda g: calendar[g].points_final_utc),
    })
    for src, dst in STAT_COLUMNS.items():
        out[dst] = _int64(df[src]) if src in df.columns else pd.array(
            [None] * len(df), dtype="Int64"
        )
    for src, dst in FLOAT_STAT_COLUMNS.items():
        out[dst] = df[src].astype("float64") if src in df.columns else float("nan")
    return out, dropped


def build_player_dim_and_state(
    season: str,
    gw_rows: pd.DataFrame,
    players_raw: pd.DataFrame,
    calendar: Mapping[int, GameweekWindow],
    team_code_by_id: Mapping[int, int],
    team_code_by_name: Mapping[str, int],
    fixture_team: Mapping[int, tuple[int, int]],
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """dim_player (emitted on change) and fact_player_state (emitted per gameweek).

    A player's club is taken from the *fixture* (home/away team id) rather than
    from the ``team`` name string, because ids survive rebrands and the string
    does not. The name column is only a fallback.
    """
    warnings: list[str] = []
    df = gw_rows.copy()
    df["gw"] = df["GW"].astype(int)
    df = df[df["gw"].isin(calendar)]
    # One state row per player per gameweek: a double gameweek repeats the same
    # price and ownership, which are gameweek-level facts, not fixture-level.
    df = df.sort_values(["gw", "fixture"]).drop_duplicates(["code", "gw"], keep="first")

    id_cols = [c for c in ("first_name", "second_name", "web_name", "element_type")
               if c in players_raw.columns]
    names = players_raw.drop_duplicates("id").set_index("id")[id_cols]

    def club(row: Any) -> int | None:
        pair = fixture_team.get(int(row.fixture))
        if pair is not None:
            team_id = pair[0] if bool(row.was_home) else pair[1]
            code = team_code_by_id.get(int(team_id))
            if code is not None:
                return code
        return team_code_by_name.get(str(getattr(row, "team", "")))

    # Ownership: vaastav records `selected`, an absolute count of squads, not a
    # percentage. The denominator is recoverable exactly, because every manager
    # picks 15 players: total squads == sum(selected over all players) / 15.
    # That is a derivation from the data, not an assumption about FPL's userbase.
    #
    # It is only valid if the gameweek's rows cover the whole player pool. A
    # partial file (a test slice, a truncated download) would produce a
    # plausible-looking but wrong percentage, so gameweeks that are obviously
    # incomplete get NULL ownership and a warning instead.
    squads_by_gw: dict[int, float] = {}
    if "selected" in df.columns:
        sizes = df.groupby("gw")["code"].nunique()
        totals = df.groupby("gw")["selected"].sum() / 15.0
        for gw, total in totals.items():
            if sizes.loc[gw] < MIN_PLAYERS_FOR_OWNERSHIP:
                warnings.append(
                    f"gw{int(gw)}: only {int(sizes.loc[gw])} players present, too few to "
                    "recover the ownership denominator; selected_by_pct left NULL"
                )
                continue
            if total > 0:
                squads_by_gw[int(gw)] = float(total)

    dim_rows: list[dict[str, object]] = []
    state_rows: list[dict[str, object]] = []
    last_identity: dict[int, tuple[object, ...]] = {}

    for row in df.itertuples(index=False):
        code = int(row.code)
        gw = int(row.gw)
        element = int(row.element)
        deadline = calendar[gw].deadline_utc
        position = _position_from_row(
            getattr(row, "position", None),
            names["element_type"].get(element) if "element_type" in names.columns else None,
        )
        if position is None:
            # Should be impossible: managers are stripped upstream by the
            # resolver. Loud rather than silent if it ever happens.
            warnings.append(f"gw{gw} code {code}: unmappable position "
                            f"{getattr(row, 'position', None)!r}; row skipped")
            continue
        team_code = club(row)
        if team_code is None:
            warnings.append(f"gw{gw} code {code}: no team code; row skipped")
            continue

        ident = names.loc[element] if element in names.index else None
        web_name = str(ident["web_name"]) if ident is not None and "web_name" in names.columns \
            else str(getattr(row, "name", ""))
        first = str(ident["first_name"]) if ident is not None and "first_name" in names.columns \
            else None
        second = str(ident["second_name"]) if ident is not None and "second_name" in names.columns \
            else None

        signature = (element, web_name, position, team_code)
        if last_identity.get(code) != signature:
            last_identity[code] = signature
            dim_rows.append({
                "season": season, "code": code, "element_id": element,
                "web_name": web_name, "first_name": first, "second_name": second,
                "position": position, "team_code": team_code, "as_of": deadline,
            })

        selected = getattr(row, "selected", None)
        denom = squads_by_gw.get(gw)
        pct = (
            100.0 * float(selected) / denom
            if denom and selected is not None and not pd.isna(selected)
            else None
        )
        state_rows.append({
            "season": season, "code": code, "element_id": element,
            "price_tenths": int(row.value),
            "selected_by_pct": pct,
            # vaastav's per-gameweek archive carries no availability or news,
            # so these stay NULL. Filling them with "a" would fabricate the most
            # decision-relevant field in the table.
            "status": None,
            "chance_of_playing_next_round": None,
            "news": None,
            "news_added": None,
            "transfers_in_event": int(getattr(row, "transfers_in", 0) or 0),
            "transfers_out_event": int(getattr(row, "transfers_out", 0) or 0),
            "cost_change_start": None,
            "as_of": deadline,
        })

    return pd.DataFrame(dim_rows), pd.DataFrame(state_rows), warnings


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def load_season_frames(repo: VaastavRepo, season: str) -> dict[str, pd.DataFrame]:
    return {
        "players_raw": repo.read_csv(season, FILE_PLAYERS_RAW),
        "teams": repo.read_csv(season, FILE_TEAMS),
        "fixtures": repo.read_csv(season, FILE_FIXTURES),
        "merged_gw": repo.read_csv(season, FILE_MERGED_GW),
    }


def ingest_season(
    wh: Warehouse,
    repo: VaastavRepo,
    season: str,
    index: PlayerCodeIndex,
    *,
    write_player_state: bool = True,
) -> SeasonIngestReport:
    """Load one season into the warehouse. ``index`` must already know ``season``."""
    report = SeasonIngestReport(season=season)
    frames = load_season_frames(repo, season)
    players_raw, teams, fixtures = frames["players_raw"], frames["teams"], frames["fixtures"]
    merged = frames["merged_gw"]

    before = len(merged)
    merged = merged.drop_duplicates()
    report.dropped_duplicate_rows = before - len(merged)

    calendar = build_calendar(fixtures)
    if not calendar:
        raise ValueError(f"{season}: no dated fixtures, cannot derive any as_of")
    epoch = season_epoch(calendar)
    team_code_by_id, team_code_by_name = _team_code_maps(teams)
    fixture_team = {
        int(r.id): (int(r.team_h), int(r.team_a)) for r in fixtures.itertuples(index=False)
    }

    # -- identity ------------------------------------------------------------
    resolved, res = index.resolve_frame(season, merged)
    report.resolution = res
    report.dropped_manager_rows = res.dropped_manager
    report.dropped_unmatched_rows = res.dropped_unmatched
    if res.dropped_unmatched:
        report.warnings.append(
            f"{res.dropped_unmatched} rows dropped with no stable code; samples: "
            + "; ".join(res.unmatched_samples[:5])
        )

    known_fixture = resolved["fixture"].astype(int).isin(fixture_team)
    report.dropped_no_fixture = int((~known_fixture).sum())
    if report.dropped_no_fixture:
        report.warnings.append(
            f"{report.dropped_no_fixture} rows reference a fixture absent from fixtures.csv"
        )
    resolved = resolved[known_fixture]

    # -- writes --------------------------------------------------------------
    report.rows["dim_team"] = wh.append("dim_team", build_teams(season, teams, epoch))

    fx_rows, dropped_fx = build_fixtures(season, fixtures, team_code_by_id, epoch)
    if dropped_fx:
        report.warnings.append(f"{dropped_fx} fixtures reference an unknown team id")
    report.rows["fact_fixture"] = wh.append("fact_fixture", fx_rows)

    dim, state, warns = build_player_dim_and_state(
        season, resolved, players_raw, calendar, team_code_by_id, team_code_by_name, fixture_team,
    )
    report.warnings.extend(warns[:5])
    report.rows["dim_player"] = wh.append("dim_player", dim)

    pf, dropped_cal = build_player_fixture(season, resolved, calendar)
    report.dropped_no_calendar = dropped_cal
    if dropped_cal:
        report.warnings.append(f"{dropped_cal} rows sit in a gameweek with no dated fixture")
    report.rows["fact_player_fixture"] = wh.append("fact_player_fixture", pf)

    if write_player_state:
        report.rows["fact_player_state"] = wh.append("fact_player_state", state)

    return report


def ingest_history(
    wh: Warehouse,
    repo: VaastavRepo,
    seasons: Sequence[str] = DEFAULT_SEASONS,
    *,
    write_player_state: bool = True,
) -> tuple[list[SeasonIngestReport], PlayerCodeIndex]:
    """Index every season's identity first, then load. Order is oldest-first.

    Identity is built for *all* seasons before any writing, so the index is a
    single consistent view rather than something that grows mid-load.
    """
    index = PlayerCodeIndex()
    index_reports = {
        season: index.add_season(season, repo.read_csv(season, FILE_PLAYERS_RAW))
        for season in seasons
    }

    reports = []
    for season in seasons:
        rep = ingest_season(wh, repo, season, index, write_player_state=write_player_state)
        rep.index_report = index_reports[season]
        if rep.index_report.temporary_codes:
            rep.warnings.append(
                f"{len(rep.index_report.temporary_codes)} codes flagged "
                "has_temporary_code by FPL; they will be reissued and split a career"
            )
        reports.append(rep)
    return reports, index


def record_provenance(wh: Warehouse, repo: VaastavRepo, fetched_at: dt.datetime) -> int:
    """Write one ``raw_fetch`` row per file read, so a load is traceable to bytes."""
    n = 0
    for rel, (digest, size, _) in repo.provenance.items():
        wh.record_fetch(
            source="vaastav",
            endpoint=f"{repo.base_url}/{rel}",
            params=f"bytes={size}",
            fetched_at=fetched_at,
            sha256=digest,
            body_path=str(repo.root / rel),
            http_status=200,
        )
        n += 1
    return n


def summarise(reports: Iterable[SeasonIngestReport]) -> str:
    reports = list(reports)
    lines = [r.render() for r in reports]
    totals: dict[str, int] = {}
    for r in reports:
        for table, n in r.rows.items():
            totals[table] = totals.get(table, 0) + n
    lines.append("[TOTAL]")
    for table, n in totals.items():
        lines.append(f"    {table:24s} {n:>8,}")
    eligible = sum(r.resolution.eligible for r in reports if r.resolution)
    resolved = sum(r.resolution.resolved for r in reports if r.resolution)
    managers = sum(r.dropped_manager_rows for r in reports)
    unmatched = sum(r.dropped_unmatched_rows for r in reports)
    rate = resolved / eligible if eligible else 1.0
    lines.append(
        f"    cross-season code match rate  {rate:.4%} ({resolved:,}/{eligible:,} eligible rows)"
    )
    lines.append(f"    manager rows stripped         {managers:,}")
    lines.append(f"    rows dropped unmatched        {unmatched:,}")
    return "\n".join(lines)
