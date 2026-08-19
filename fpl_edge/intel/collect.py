"""One pass of the intel collector: archive in, dated intel rows out.

Everything here is a *replay* over material already on disk -- the archived
``bootstrap-static`` bodies under ``data/raw/fpl_api`` and the warehouse's own
fact tables -- except :func:`collect`'s optional ``probe_sources`` step, which is
the only part that touches the network. That split is intentional:

* The offline path is deterministic and idempotent. Running it twice inserts
  nothing the second time, because every id is a content hash. It can be run in
  a test, in CI, and against a rebuilt warehouse.
* The network path is quarantined behind a flag, so a dossier can never
  accidentally block on a Cloudflare timeout while someone is standing at a
  deadline.

The collector never invents a timestamp. Where FPL supplies one (``news_added``)
it is used; where FPL supplies none, the instant of the first archived poll
carrying the value is used and the docstrings say so. Nothing is dated "now"
just because that is when the collector happened to run.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from fpl_edge.intel import availability, formations, oop, setpieces, sources
from fpl_edge.intel.bootstrap import ARCHIVE_DIR
from fpl_edge.intel.items import IntelItem
from fpl_edge.intel.store import IntelStore
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc

DEFAULT_HISTORY = ("2022-23", "2023-24", "2024-25", "2025-26")


@dataclass
class CollectionReport:
    """What one pass did, and what it could not do.

    Every field is reported, including the zeroes. A collector that silently
    writes nothing looks identical to one that had nothing to write, and the
    difference matters enormously when someone is deciding whether to trust a
    dossier section that came back empty.
    """

    started: dt.datetime
    season: str
    written: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"Intel collection for {self.season} at {self.started:%Y-%m-%d %H:%M}Z", ""]
        for table, n in sorted(self.written.items()):
            lines.append(f"  {table:<24} +{n}")
        if self.notes:
            lines.append("")
            lines += [f"  note: {n}" for n in self.notes]
        if self.skipped:
            lines.append("")
            lines += [f"  SKIPPED: {s}" for s in self.skipped]
        return "\n".join(lines)


def _season_ends(wh: Warehouse, seasons: tuple[str, ...]) -> dict[str, dt.datetime]:
    """Last kickoff per season, used to date an end-of-season duty snapshot.

    Read unfiltered on purpose: this is a *fixture schedule* lookup, and fixture
    scheduling is public well in advance, so it carries no result information.
    The dates it produces are then used as ``published_at`` values, which is
    where the point-in-time discipline actually bites.
    """
    df = wh.sql(
        "SELECT season, max(kickoff_utc) AS ko FROM fact_fixture "
        "WHERE season IN (" + ", ".join("?" * len(seasons)) + ") GROUP BY season",
        list(seasons),
    )
    out: dict[str, dt.datetime] = {}
    for _, row in df.iterrows():
        if row["ko"] is None:
            continue
        import pandas as pd

        if pd.isna(row["ko"]):
            continue
        out[str(row["season"])] = pd.Timestamp(row["ko"]).to_pydatetime().astimezone(UTC)
    return out


def collect(
    wh: Warehouse,
    *,
    season: str,
    now: dt.datetime | None = None,
    archive_dir: Path = ARCHIVE_DIR,
    history: tuple[str, ...] = DEFAULT_HISTORY,
    probe_sources: bool = False,
) -> CollectionReport:
    """Run every offline collector, optionally probing external sources too."""
    when = (now or dt.datetime.now(UTC)).astimezone(UTC)
    store = IntelStore(wh)
    report = CollectionReport(started=when, season=season)
    items: list[IntelItem] = []

    # -- availability: FPL's news, dated by FPL's news_added ------------------
    avail_items, avail_counts = availability.availability_items(
        wh, season=season, observed_at=when
    )
    items.extend(avail_items)
    report.notes.append(
        f"availability: {len(avail_items)} dated items from {avail_counts['rows']} "
        f"state rows ({avail_counts['empty']} carried no news text, "
        f"{avail_counts['undated']} had news but no news_added and were DROPPED "
        f"rather than dated to the poll)"
    )

    # -- set pieces: state and change detection over the archive -------------
    scan = setpieces.scan_archive(season=season, directory=archive_dir)
    if scan.polls == 0:
        report.skipped.append(
            f"set-piece scan: no archived bootstrap bodies under {archive_dir}. "
            "Run `make ingest` at least twice to give the change detector two "
            "observations to compare."
        )
    else:
        report.written["set_piece_duty"] = store.put_duties(scan.duties)
        report.written["set_piece_change"] = store.put_changes(scan.changes)
        items.extend(scan.items)
        report.notes.append(
            f"set pieces: {scan.window_note()}; {len(scan.duties)} live duty rows, "
            f"{len(scan.changes)} changes, {len(scan.alerts)} above the alert threshold"
        )

    # -- set pieces: the same detector over the historical season-end archive.
    # The live archive is hours old, so it cannot yet show a duty actually
    # moving. The historical one spans four seasons and shows plenty.
    try:
        snap = wh.snapshot_at(when)
        ends = _season_ends(wh, history)
        hist_scan = setpieces.scan_seasons(list(history), season_end=ends)
        if hist_scan.polls >= 2:
            report.written["set_piece_duty"] = (
                report.written.get("set_piece_duty", 0) + store.put_duties(hist_scan.duties)
            )
            report.written["set_piece_change"] = (
                report.written.get("set_piece_change", 0) + store.put_changes(hist_scan.changes)
            )
            items.extend(hist_scan.items)
            report.notes.append(
                f"set pieces (historical): {hist_scan.polls} season-end snapshots, "
                f"{len(hist_scan.changes)} duty changes, {len(hist_scan.alerts)} above "
                "the alert threshold"
            )
        else:
            report.skipped.append(
                "set pieces (historical): fewer than two season-end players_raw.csv "
                "snapshots available, so there is nothing to compare."
            )
    except (OSError, ValueError, KeyError) as exc:
        report.skipped.append(f"set pieces (historical): {type(exc).__name__}: {exc}")

    # -- press conference links FPL attaches itself --------------------------
    press = sources.press_links_from_bootstrap(season=season, directory=archive_dir)
    items.extend(press)
    report.notes.append(f"press links: {len(press)} distinct scout_news_link URLs in the archive")

    # -- out of position, from the same rates the points model uses ----------
    try:
        from fpl_edge.models.points.shares import estimate_rates

        snap = wh.snapshot_at(when)
        rates = estimate_rates(snap, list(history))
        players = snap.players(season)
        frame = oop.build_frame(rates.frame, players)
        signals, oop_counts = oop.detect(frame, season=season, as_of=when)
        report.written["oop_signal"] = store.put_oop(signals)
        names = dict(zip(players["code"].astype(int), players["web_name"].astype(str)))
        items.extend(oop.to_items(signals, names))
        report.notes.append(
            f"out of position: {oop_counts['flagged']} flagged of "
            f"{oop_counts['assessed']} assessed; {oop_counts['excluded_minutes']} "
            f"excluded for under {oop.MIN_MINUTES:.0f} weighted minutes"
        )
    except (ValueError, KeyError) as exc:
        report.skipped.append(f"out of position: {type(exc).__name__}: {exc}")

    # -- formations, from finalised lineups ----------------------------------
    try:
        snap = wh.snapshot_at(when)
        obs, form_counts = formations.observe(snap, season)
        report.written["formation_observation"] = store.put_formations(obs)
        teams = snap.table("dim_team", where="season = ?", params=[season])
        team_names = (
            dict(zip(teams["team_code"].astype(int), teams["name"].astype(str)))
            if not teams.empty else {}
        )
        items.extend(formations.to_items(obs, team_names=team_names))
        if not obs:
            report.skipped.append(
                f"formations: no finalised lineups for {season} yet "
                f"({form_counts['fixtures']} team-fixtures examined). This is expected "
                "before the season starts; the shape is counted from who started, "
                "which is unknowable until a match has been played."
            )
        else:
            report.notes.append(f"formations: {len(obs)} team-fixtures with a readable shape")
    except (ValueError, KeyError) as exc:
        report.skipped.append(f"formations: {type(exc).__name__}: {exc}")

    # -- external sources, only when explicitly asked ------------------------
    if probe_sources:
        probes = sources.probe_all(now=when)
        report.written["source_probe"] = store.put_probes(probes)
        items.extend(sources.probe_items(probes, now=when))
        for p in probes:
            report.notes.append(f"probe: {p.render()}")
    else:
        report.skipped.append(
            "source probes: not run (network). Pass --probe to reach the external "
            "press-conference and injury-table candidates and record their real "
            "HTTP status."
        )

    report.written["intel_item"] = store.put_items(items)
    return report


def collect_changes_only(
    wh: Warehouse, *, season: str, archive_dir: Path = ARCHIVE_DIR
) -> list:
    """Just the set-piece change detector. Cheap enough for a frequent timer."""
    store = IntelStore(wh)
    scan = setpieces.scan_archive(season=season, directory=archive_dir)
    store.put_duties(scan.duties)
    store.put_changes(scan.changes)
    store.put_items(scan.items)
    return scan.alerts
