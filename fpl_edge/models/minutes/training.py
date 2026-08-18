"""Assembling training data without reaching around the Snapshot.

A model that trains on "all of last season" as one frame has already lost: the
features of a GW12 row would be computed with GW38 information in scope. Here,
one snapshot is minted per historical deadline, the features for that gameweek
are built from it, and the labels are joined from a *separate* snapshot taken
after the gameweek settled. The builder never touches a table itself; it only
holds a factory that mints Snapshots.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from fpl_edge.models.minutes.features import (
    COLD_FEATURE_COLUMNS,
    FEATURE_COLUMNS,
    attach_labels,
    build_feature_frame,
)
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Season

SnapshotFactory = Callable[[dt.datetime], Snapshot]

#: How long after a deadline the gameweek is certainly settled (FPL finalises at
#: 09:00 UK the day after the last match; midweek spillover makes 12 days safe).
LABEL_LAG = dt.timedelta(days=12)

#: Rule registry: deadlines.offset_before_first_kickoff_minutes = 90.
DEADLINE_BEFORE_FIRST_KICKOFF = dt.timedelta(minutes=90)

#: Gameweeks used to augment the cold-start training set. Features for these are
#: built from a *season-start* snapshot, so they contain no current-season
#: information at all -- the same structural blindness a real GW1 has.
COLD_AUGMENT_GWS = (1, 2, 3, 4, 5, 6)


@dataclass(frozen=True)
class TrainingSet:
    """Feature rows with realised buckets, split by cold-start eligibility."""

    frame: pd.DataFrame
    cold_frame: pd.DataFrame

    @property
    def warm(self) -> pd.DataFrame:
        return self.frame[self.frame["is_cold_start"] < 0.5]

    def X(self, cold: bool = False) -> np.ndarray:
        cols = COLD_FEATURE_COLUMNS if cold else FEATURE_COLUMNS
        src = self.cold_frame if cold else self.warm
        return src[list(cols)].to_numpy(dtype=float)

    def y(self, cold: bool = False) -> np.ndarray:
        src = self.cold_frame if cold else self.warm
        return src["bucket"].to_numpy(dtype=int)

    def __len__(self) -> int:
        return len(self.frame)


class TrainingSetBuilder:
    """Replays historical deadlines into a supervised frame."""

    def __init__(self, snapshot_at: SnapshotFactory, catalog: Snapshot) -> None:
        self.snapshot_at = snapshot_at
        self.catalog = catalog

    def deadlines(self, season: Season) -> pd.DataFrame:
        """Gameweek deadlines, derived from the fixture list when necessary.

        ``dim_event`` only carries the *current* season: the historical archive
        is fixtures and results, with no events endpoint behind it. The rule
        registry pins the deadline at 90 minutes before a gameweek's first
        kickoff (``deadlines.offset_before_first_kickoff_minutes``), so a
        historical deadline is reconstructible from the schedule - which is
        public in advance and therefore not a leak.
        """
        ev = self.catalog.table("dim_event", where="season = ?", params=[season])
        if not ev.empty:
            return ev.sort_values("gw")[["gw", "deadline_utc"]].reset_index(drop=True)
        fx = self.catalog.table("fact_fixture", where="season = ?", params=[season])
        if fx.empty:
            return pd.DataFrame(columns=["gw", "deadline_utc"])
        fx = fx[fx["gw"].notna() & fx["kickoff_utc"].notna()]
        if fx.empty:
            return pd.DataFrame(columns=["gw", "deadline_utc"])
        first = fx.groupby("gw")["kickoff_utc"].min().reset_index()
        first["deadline_utc"] = first["kickoff_utc"] - DEADLINE_BEFORE_FIRST_KICKOFF
        out = first[["gw", "deadline_utc"]].sort_values("gw").reset_index(drop=True)
        out["gw"] = out["gw"].astype(int)
        return out

    def build(
        self,
        seasons: Sequence[Season],
        *,
        gws: Sequence[int] | None = None,
        cold_augment_gws: Sequence[int] = COLD_AUGMENT_GWS,
    ) -> TrainingSet:
        """Replay ``seasons`` deadline by deadline.

        ``gws`` restricts which gameweeks are replayed, which exists so tests
        can train on a slice in seconds rather than replaying whole seasons.
        """
        main: list[pd.DataFrame] = []
        cold: list[pd.DataFrame] = []
        for season in seasons:
            ev = self.deadlines(season)
            if ev.empty:
                continue
            if gws is not None:
                ev = ev[ev["gw"].isin(list(gws))]
            for row in ev.itertuples():
                deadline = pd.Timestamp(row.deadline_utc).to_pydatetime()
                feats = build_feature_frame(
                    self.snapshot_at(deadline), season, [GwId(int(row.gw))]
                )
                labelled = attach_labels(feats, self.snapshot_at(deadline + LABEL_LAG), season)
                if not labelled.empty:
                    main.append(labelled)
            if cold_augment_gws:
                first = pd.Timestamp(ev.iloc[0]["deadline_utc"]).to_pydatetime()
                feats = build_feature_frame(
                    self.snapshot_at(first), season, [GwId(int(g)) for g in cold_augment_gws]
                )
                if not feats.empty:
                    last_gw = max(int(g) for g in cold_augment_gws)
                    last_dl = pd.Timestamp(
                        ev[ev["gw"] == last_gw]["deadline_utc"].iloc[0]
                    ).to_pydatetime()
                    labelled = attach_labels(
                        feats, self.snapshot_at(last_dl + LABEL_LAG), season
                    )
                    if not labelled.empty:
                        cold.append(labelled)
        empty = pd.DataFrame(columns=[*FEATURE_COLUMNS, "bucket", "is_cold_start"])
        frame = pd.concat(main, ignore_index=True) if main else empty
        cold_frame = pd.concat(cold, ignore_index=True) if cold else empty
        # genuine GW1 rows belong in the cold set too
        if not frame.empty:
            gw1 = frame[frame["is_cold_start"] >= 0.5]
            if not gw1.empty:
                cold_frame = pd.concat([cold_frame, gw1], ignore_index=True)
        if not cold_frame.empty:
            # the augmented GW1 rows and the genuine GW1 rows are the same rows
            # built from the same snapshot; keeping both would double their weight
            cold_frame = cold_frame.drop_duplicates(
                subset=["season", "code", "fixture_id"], keep="first"
            ).reset_index(drop=True)
        return TrainingSet(frame=frame, cold_frame=cold_frame)
