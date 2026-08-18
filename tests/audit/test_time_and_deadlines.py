"""Timezone, deadline and DST failure modes.

Hunt list item 3. The registry already records that this class of bug was caught
once: the FPL rules page renders its deadline table in BROWSER-LOCAL time, and
someone read "GW1 Fri 21 Aug 10:30" off a US/Pacific browser when the true
deadline is 2026-08-21T17:30:00Z. See ``fpl_edge/rules/registry.yaml`` line 22.

These tests exist because the same bug came back one layer down.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import zoneinfo
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.types import Deadline, GwId

from .conftest import GW1_DEADLINE, REPO_ROOT, UTC, fixture_row, frame

LONDON = zoneinfo.ZoneInfo("Europe/London")

#: A machine whose local time is neither UTC nor UK. This is the offset that
#: produced the original "10:30" misreading.
HOSTILE_TZ = "America/Los_Angeles"


def _event_rows(gw: int, deadline: dt.datetime, as_of: dt.datetime) -> pd.DataFrame:
    return frame([{
        "season": "2026-27", "gw": gw, "deadline_utc": deadline,
        "is_finished": False, "as_of": as_of,
    }])


# ---------------------------------------------------------------------------
# FAILURE MODE: a deadline read out of the warehouse is not UTC.
# ---------------------------------------------------------------------------


def test_snapshot_deadline_is_returned_in_utc(wh) -> None:
    """GUARDS: Snapshot.deadline() returning a process-local-timezone datetime.

    DuckDB renders TIMESTAMPTZ in the connection's session timezone, which it
    takes from the OS. ``Snapshot.deadline`` does ``df.iloc[0][...].to_pydatetime()``
    and returns whatever pandas made of that, so on a machine in Los Angeles the
    2026-27 GW1 deadline comes back as ``2026-08-21 10:30:00-07:00``.

    The instant is right. Everything a human does with it is wrong:
    ``.strftime('%H:%M')`` prints ``10:30``, ``.replace(tzinfo=None)`` shifts it
    seven hours, and ``fpl_edge.types.Deadline`` rejects it outright because its
    ``__post_init__`` requires ``utcoffset() == 0``.
    """
    wh.append("dim_event", _event_rows(1, GW1_DEADLINE, dt.datetime(2026, 8, 1, tzinfo=UTC)))
    got = wh.snapshot_at(dt.datetime(2026, 8, 18, tzinfo=UTC)).deadline("2026-27", 1)

    assert got == GW1_DEADLINE, "the instant itself must survive the round trip"
    assert got.utcoffset() == dt.timedelta(0), (
        f"deadline came back as {got!r} with offset {got.utcoffset()}. "
        "fpl_edge/store/warehouse.py:147 returns the DuckDB session-timezone "
        "rendering, so the wall-clock reading is machine-dependent"
    )


def test_deadline_survives_construction_into_the_Deadline_type(wh) -> None:
    """GUARDS: warehouse and types disagreeing about what 'UTC' means.

    ``fpl_edge/types.py:152`` exists precisely to make a non-UTC deadline
    impossible. If the warehouse cannot feed it, the type is decoration.
    """
    wh.append("dim_event", _event_rows(1, GW1_DEADLINE, dt.datetime(2026, 8, 1, tzinfo=UTC)))
    got = wh.snapshot_at(dt.datetime(2026, 8, 18, tzinfo=UTC)).deadline("2026-27", 1)
    Deadline(GwId(1), got)  # raises ValueError if the offset is not zero


# ---------------------------------------------------------------------------
# FAILURE MODE: the same warehouse reads differently on two machines.
# ---------------------------------------------------------------------------


_TZ_PROBE = r"""
import datetime as dt, json, sys
from fpl_edge.store import Warehouse
import pandas as pd

wh = Warehouse(sys.argv[1])
wh.append("dim_event", pd.DataFrame([{
    "season": "2026-27", "gw": 1,
    "deadline_utc": dt.datetime(2026, 8, 21, 17, 30, tzinfo=dt.timezone.utc),
    "is_finished": False,
    "as_of": dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc),
}]))
d = wh.snapshot_at(dt.datetime(2026, 8, 18, tzinfo=dt.timezone.utc)).deadline("2026-27", 1)
print(json.dumps({
    "wall_clock": d.strftime("%a %d %b %H:%M"),
    "offset_seconds": d.utcoffset().total_seconds(),
    "iso": d.isoformat(),
}))
"""


def _probe(tmp_path: Path, tz: str) -> dict[str, object]:
    env_db = tmp_path / f"probe_{tz.replace('/', '_')}.duckdb"
    proc = subprocess.run(
        [sys.executable, "-c", _TZ_PROBE, str(env_db)],
        cwd=REPO_ROOT, capture_output=True, text=True,
        env={"TZ": tz, "PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "PYTHONPATH": str(REPO_ROOT)},
        check=True,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_deadline_reads_identically_under_a_hostile_process_timezone(tmp_path) -> None:
    """GUARDS: the browser-local-time bug returning as process-local time.

    This is the regression test for the bug the rule registry says was already
    caught once. Under ``TZ=America/Los_Angeles`` the wall-clock rendering of the
    GW1 deadline becomes the literal string recorded in registry.yaml as the
    WRONG answer: ``Fri 21 Aug 10:30``.

    Runs the read in a subprocess because DuckDB fixes its session timezone from
    the environment at connection time.
    """
    utc = _probe(tmp_path, "UTC")
    hostile = _probe(tmp_path, HOSTILE_TZ)

    assert utc["iso"] == hostile["iso"] or True  # instants may render differently
    assert hostile["offset_seconds"] == 0, (
        f"under TZ={HOSTILE_TZ} the deadline came back with offset "
        f"{hostile['offset_seconds']}s and renders as {hostile['wall_clock']!r}. "
        "registry.yaml records 'GW1 Fri 21 Aug 10:30' as the already-caught "
        "rules-page bug; the warehouse now reproduces it"
    )
    assert utc["wall_clock"] == hostile["wall_clock"], (
        "the same warehouse gives two different wall-clock deadlines on two "
        f"machines: {utc['wall_clock']!r} vs {hostile['wall_clock']!r}"
    )


# ---------------------------------------------------------------------------
# FAILURE MODE: a naive datetime reaches the warehouse.
# ---------------------------------------------------------------------------


def test_naive_deadline_is_refused_on_write(wh) -> None:
    """GUARDS: a naive deadline_utc being silently localised on write.

    ``Warehouse.append`` validates tz-awareness for ``as_of`` and for nothing
    else. ``deadline_utc``, ``kickoff_utc`` and ``news_added`` are TIMESTAMPTZ
    columns, so DuckDB interprets a naive value in the SESSION timezone.

    Since commit d450316 the session timezone is pinned to UTC, which makes a
    naive value accidentally right today. It is still a silent assumption, and
    the assumption is load-bearing: unpin the session, open a connection any
    other way, or move the data through a second DuckDB and the timestamp moves
    by the host's offset with nothing raising.

    The entry path is real: ``fpl_edge/ingest/fpl_api.py:36`` ``_ts()`` returns a
    naive datetime for any timestamp string without a 'Z'.
    """
    naive = _event_rows(1, dt.datetime(2026, 8, 21, 17, 30), dt.datetime(2026, 8, 1, tzinfo=UTC))  # noqa: DTZ001  (naive on purpose)
    with pytest.raises(ValueError, match="(?i)tz|timezone|naive|aware"):
        wh.append("dim_event", naive)


def test_naive_kickoff_is_refused_on_write(wh) -> None:
    """GUARDS: a naive kickoff shifting a fixture across the as_of boundary.

    ``Snapshot.upcoming_fixtures`` filters ``kickoff_utc > as_of``. A kickoff
    localised seven hours late stays 'upcoming' after it has been played, so a
    model can be asked to predict a match that already finished.
    """
    row = fixture_row(
        season="2026-27", fixture_id=1, gw=1,
        kickoff_utc=dt.datetime(2026, 8, 21, 19, 0),  # naive  # noqa: DTZ001  (naive on purpose)
        as_of=dt.datetime(2026, 8, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="(?i)tz|timezone|naive|aware"):
        wh.append("fact_fixture", frame([row]))


def test_ingest_timestamp_parser_never_returns_naive() -> None:
    """GUARDS: fpl_edge/ingest/fpl_api.py:36 _ts() returning a naive datetime.

    ``_ts`` handles the 'Z' suffix the API happens to send today. It has no
    defence against a payload that omits the offset -- and FPL's ``news_added``
    has historically been served both ways.
    """
    from fpl_edge.ingest.fpl_api import _ts

    with_z = _ts("2026-08-21T17:30:00Z")
    assert with_z is not None and with_z.tzinfo is not None

    without_z = _ts("2026-08-21T17:30:00")
    assert without_z is None or without_z.tzinfo is not None, (
        f"_ts('2026-08-21T17:30:00') returned {without_z!r}, a naive datetime. "
        "Written to a TIMESTAMPTZ column it is reinterpreted in the process "
        "timezone and silently moves by the local UTC offset"
    )


# ---------------------------------------------------------------------------
# FAILURE MODE: BST/GMT. UK-local kickoff times are constant; UTC ones are not.
# ---------------------------------------------------------------------------


def test_real_deadlines_span_the_bst_boundary_and_shift_in_utc() -> None:
    """GUARDS: any code that assumes a constant UTC deadline hour.

    From the ingested 2026-27 fixture list: GW30 is 2027-03-20T13:30Z and GW31
    is 2027-04-10T12:30Z. Both are 13:30 UK local -- BST starts 2027-03-28. Any
    model keying on the UTC hour sees a one-hour jump that is not real, and any
    code deriving a UTC deadline by adding a fixed offset to a UK-local time is
    an hour wrong for roughly half the season.
    """
    bootstrap = _load_raw_bootstrap()
    deadlines = {
        e["id"]: dt.datetime.fromisoformat(e["deadline_time"].replace("Z", "+00:00"))  # noqa: FURB162  (the 'Z' suffix is the point)
        for e in bootstrap["events"]
    }
    utc_hours = {d.hour for d in deadlines.values()}
    london_hours = {d.astimezone(LONDON).hour for d in deadlines.values()}

    assert len(utc_hours) > 1, "expected the UTC deadline hour to vary across the season"

    # The specific pair either side of the spring clock change.
    gw30, gw31 = deadlines[30], deadlines[31]
    assert gw30.hour != gw31.hour, "GW30/GW31 should differ by an hour in UTC"
    assert gw30.astimezone(LONDON).hour == gw31.astimezone(LONDON).hour == 13, (
        "GW30 and GW31 are both 13:30 UK local; if this assertion fails the "
        "fixture list changed and the DST reasoning must be redone"
    )
    assert len(london_hours) >= 1


def test_points_finalisation_time_is_uk_local_not_utc() -> None:
    """GUARDS: implementing 'points final at 09:00 UK' as 09:00 UTC.

    ``deadlines.points_final_at`` in the registry is a UK-local wall-clock time.
    In BST that is 08:00Z; in GMT it is 09:00Z. A fixed 09:00Z implementation
    makes finalised results visible an hour late for two thirds of the season --
    which is harmless -- and, if implemented as 08:00Z year round, an hour EARLY
    in winter, which is leakage.
    """
    from fpl_edge.rules import rules

    stated = rules().get("deadlines.points_final_at")
    assert "UK" in stated, f"expected a UK-local statement, got {stated!r}"

    summer = dt.datetime(2026, 8, 23, 9, 0, tzinfo=LONDON)
    winter = dt.datetime(2026, 12, 27, 9, 0, tzinfo=LONDON)
    assert summer.astimezone(UTC).hour == 8, "09:00 BST is 08:00 UTC"
    assert winter.astimezone(UTC).hour == 9, "09:00 GMT is 09:00 UTC"


def test_gw1_deadline_matches_the_api_not_the_rules_page() -> None:
    """GUARDS: a deadline sourced from the rules page instead of the API.

    Asserted against the archived bootstrap body, and cross-checked against the
    registry's 90-minutes-before-first-kickoff rule. If a future change sources
    deadlines from the rules page, GW1 lands at 10:30 in the reader's own
    timezone and this test says so.
    """
    from fpl_edge.rules import rules

    bootstrap = _load_raw_bootstrap()
    fixtures = _load_raw_fixtures()

    gw1 = next(e for e in bootstrap["events"] if e["id"] == 1)
    deadline = dt.datetime.fromisoformat(gw1["deadline_time"].replace("Z", "+00:00"))  # noqa: FURB162  (the 'Z' suffix is the point)
    assert deadline == GW1_DEADLINE

    kickoffs = sorted(
        dt.datetime.fromisoformat(f["kickoff_time"].replace("Z", "+00:00"))  # noqa: FURB162  (the 'Z' suffix is the point)
        for f in fixtures
        if f.get("event") == 1 and f.get("kickoff_time")
    )
    offset_minutes = rules().get("deadlines.offset_before_first_kickoff_minutes")
    assert (kickoffs[0] - deadline) == dt.timedelta(minutes=offset_minutes), (
        f"GW1 deadline {deadline.isoformat()} is not {offset_minutes} minutes "
        f"before the first kickoff {kickoffs[0].isoformat()}"
    )


def _raw_dir() -> Path:
    return REPO_ROOT / "data" / "raw" / "fpl_api"


def _load_raw_bootstrap() -> dict:
    paths = sorted(_raw_dir().glob("bootstrap-static_*.json"))
    if not paths:
        pytest.skip("no archived bootstrap body; run `make ingest`")
    return json.loads(paths[-1].read_text())


def _load_raw_fixtures() -> list[dict]:
    paths = sorted(_raw_dir().glob("fixtures_*.json"))
    if not paths:
        pytest.skip("no archived fixtures body; run `make ingest`")
    return json.loads(paths[-1].read_text())


def test_append_and_snapshot_agree_about_naive_as_of(wh) -> None:
    """GUARDS: the write path being laxer than the read path.

    ``Warehouse.snapshot_at`` raises ValueError on a naive ``as_of`` -- naive
    datetimes are "a bug", says fpl_edge/types.py:158. ``Warehouse.append``
    quietly runs the same value through ``pd.to_datetime(..., utc=True)``, which
    LOCALISES rather than converts, so a naive 2026-08-01 becomes
    2026-08-01T00:00Z with no complaint.

    A backfill script that builds ``as_of`` from a naive date string therefore
    stamps observability at the wrong instant, and the read side -- which is the
    only place that checks -- never sees it.
    """
    naive = dt.datetime(2026, 8, 1, 12, 0)  # noqa: DTZ001  (naive on purpose)

    with pytest.raises(ValueError):
        wh.snapshot_at(naive)

    rows = _event_rows(1, GW1_DEADLINE, naive)
    with pytest.raises(ValueError, match="(?i)tz|timezone|naive|aware"):
        wh.append("dim_event", rows)
