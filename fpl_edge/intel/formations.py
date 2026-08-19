"""Formation, counted from who actually started -- in FPL's classification.

The shape reported here is ``defenders-midfielders-forwards`` **as FPL labels
them**, not as the manager would describe the tactic. That is deliberate and it
is the useful quantity, for a reason worth stating plainly:

A back three with two wing-backs is 3-5-2 on a whiteboard. If FPL classifies both
wing-backs as defenders it is 5-3-2 here; if it classifies them as midfielders it
is 3-5-2. The *disagreement between those two numbers* is precisely the
out-of-position edge that :mod:`fpl_edge.intel.oop` scores, so measuring the
FPL-classified shape and the real one separately is more informative than
guessing at a single "true" formation from starting-eleven data alone.

What it is good for
-------------------
* **Clean-sheet exposure.** A side that moves from four FPL-defenders to five has
  one more clean-sheet-eligible player for the same defensive event, which
  changes the value of every defender in that squad.
* **Rotation and role change.** A team whose modal shape shifts between
  gameweeks is a team whose defenders' minutes are less safe than their price
  suggests.

What it is not
--------------
This reads finalised results, so it is **retrospective**. It cannot tell you what
a manager will pick on Saturday; it tells you what the last N fixtures actually
looked like. Any dossier section built on it says so.

Point-in-time: the observation is dated to the fixture's own kickoff, not to
when we computed it. A lineup becomes public when the teamsheet drops, so a
snapshot before kickoff must not see it, and one after must.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from fpl_edge.intel.items import (
    FormationObservation,
    IntelItem,
    IntelKind,
    content_id,
)
from fpl_edge.store import Snapshot
from fpl_edge.types import Position

UTC = dt.timezone.utc

#: A team-fixture with fewer starters than this did not yield a readable lineup
#: -- usually a partially ingested gameweek. Skipped rather than reported as a
#: bizarre formation.
MIN_STARTERS = 10


def observe(snapshot: Snapshot, season: str) -> tuple[list[FormationObservation], dict[str, int]]:
    """Starting shape for every team-fixture visible at this snapshot.

    Only rows with ``starts == 1`` count. Substitute appearances are excluded on
    purpose: a defender who came on at 80 minutes tells you nothing about the
    shape the manager picked, and counting him turns every game into a back six.
    """
    results = snapshot.results_before(season)
    counts = {"fixtures": 0, "skipped_incomplete": 0, "no_starts_column": 0}
    if results.empty or "starts" not in results.columns:
        if not results.empty:
            counts["no_starts_column"] = int(len(results))
        return [], counts

    dim = snapshot.table("dim_player", where="season = ?", params=[season])
    if dim.empty:
        return [], counts
    meta = dim.set_index("code")[["position", "team_code"]]
    fixtures = snapshot.table("fact_fixture", where="season = ?", params=[season])
    kickoff = (
        fixtures.set_index("fixture_id")["kickoff_utc"].to_dict()
        if not fixtures.empty else {}
    )

    joined = results.join(meta, on="code", how="inner")
    starters = joined[joined["starts"].fillna(0).astype(int) == 1]
    if starters.empty:
        return [], counts

    out: list[FormationObservation] = []
    for (fixture_id, team_code), grp in starters.groupby(["fixture_id", "team_code"]):
        counts["fixtures"] += 1
        if len(grp) < MIN_STARTERS:
            counts["skipped_incomplete"] += 1
            continue
        by_pos = grp["position"].astype(int).value_counts()
        ko = kickoff.get(fixture_id)
        if ko is None or pd.isna(ko):
            counts["skipped_incomplete"] += 1
            continue
        out.append(
            FormationObservation(
                season=season,
                team_code=int(team_code),
                fixture_id=int(fixture_id),
                gw=int(grp["gw"].iloc[0]) if "gw" in grp.columns else None,
                n_def=int(by_pos.get(int(Position.DEF), 0)),
                n_mid=int(by_pos.get(int(Position.MID), 0)),
                n_fwd=int(by_pos.get(int(Position.FWD), 0)),
                # Dated to kickoff: that is when the teamsheet became public.
                as_of=pd.Timestamp(ko).to_pydatetime().astimezone(UTC),
            )
        )
    out.sort(key=lambda o: (o.as_of, o.team_code))
    return out, counts


def recent_shapes(
    frame: pd.DataFrame, *, team_code: int, last_n: int = 6
) -> tuple[list[str], str | None, bool]:
    """``(shapes newest first, modal shape, changed_recently)`` for one club.

    ``changed_recently`` is True when the most recent shape differs from the
    modal shape of the window, which is the cheap, honest version of "they have
    changed formation" -- it does not claim to know why.
    """
    if frame.empty:
        return [], None, False
    sub = frame[frame["team_code"].astype(int) == int(team_code)]
    if sub.empty:
        return [], None, False
    sub = sub.sort_values(["as_of"], ascending=False).head(int(last_n))
    shapes = [str(s) for s in sub["shape"]]
    modal = pd.Series(shapes).mode()
    mode = str(modal.iloc[0]) if not modal.empty else None
    return shapes, mode, bool(shapes and mode is not None and shapes[0] != mode)


def to_items(
    observations: list[FormationObservation], *, team_names: dict[int, str]
) -> list[IntelItem]:
    """One item per formation *change*, not per fixture.

    A club playing 4-3-3 for the eleventh consecutive week is not news. The
    change is, so only transitions are emitted.
    """
    items: list[IntelItem] = []
    last: dict[int, str] = {}
    for o in sorted(observations, key=lambda x: (x.as_of, x.team_code)):
        previous = last.get(o.team_code)
        last[o.team_code] = o.shape
        if previous is None or previous == o.shape:
            continue
        club = team_names.get(o.team_code, f"team {o.team_code}")
        items.append(
            IntelItem(
                item_id=content_id("form", o.season, o.team_code, o.fixture_id, o.shape),
                published_at=o.as_of,
                observed_at=o.as_of,
                kind=IntelKind.FORMATION,
                headline=f"{club} started {o.shape} (previous fixture: {previous})",
                body=(
                    "Shape counted from starters in FPL's own position classification, "
                    "so a wing-back FPL lists as a midfielder appears in the middle "
                    "number. Retrospective: this is what was picked, not a prediction."
                ),
                source="fpl_edge.intel.formations",
                season=o.season,
                team_code=o.team_code,
                confidence=0.8,
            )
        )
    return items
