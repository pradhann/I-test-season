"""Anchors the simulated field has to reproduce, and the code that checks them.

A field model that cannot reproduce the real rank-vs-points relationship will
report P(top 10k) numbers that are internally consistent and externally
meaningless, so this module is not optional decoration.

Anchors come in two tiers, and the difference is deliberately visible in the
code, in the same spirit as ``fpl_edge/rules/registry.yaml``:

``verified=True``
    Recomputed from the warehouse every time this module runs. These come from
    ``fact_player_fixture`` and ``fact_player_state`` for 2022-23..2025-26, which
    the historical-data team loaded from the vaastav archive. No number here is
    typed in from memory.

``verified=False``
    Widely published end-of-season figures for the overall league's rank
    thresholds. FPL does not expose historical rank-vs-points in any API this
    project ingests, and this engine runs offline, so they cannot be recomputed.
    They carry an explicit tolerance and any conclusion that depends on them
    says so.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fpl_edge.store import Warehouse


@dataclass(frozen=True, slots=True)
class Anchor:
    """A number the simulation is checked against, with its provenance."""

    name: str
    value: float
    tolerance: float
    source: str
    verified: bool
    note: str = ""

    def check(self, got: float) -> tuple[bool, str]:
        ok = abs(got - self.value) <= self.tolerance
        flag = "PASS" if ok else "FAIL"
        tag = "" if self.verified else "  [UNVERIFIED ANCHOR]"
        return ok, (
            f"{flag}  {self.name:34s} got {got:8.1f}  want {self.value:8.1f} "
            f"+-{self.tolerance:.0f}{tag}"
        )


#: Published overall-league season thresholds. Not recomputable offline.
#: Stated as gaps rather than levels, because the level moves with the scoring
#: rules (defensive contributions raised every score from 2025-26) while the
#: shape of the ladder has been far more stable.
PUBLISHED_LADDER = (
    Anchor(
        "top10k minus mean (season)", 308.0, 70.0,
        "commonly published end-of-season rank thresholds, recent seasons",
        verified=False,
        note="Typical reported values: average ~2,100-2,200, top-10k ~2,400-2,500.",
    ),
    Anchor(
        "top1k minus top10k (season)", 61.0, 35.0,
        "commonly published end-of-season rank thresholds, recent seasons",
        verified=False,
    ),
)


def warehouse_anchors(db_path: str = "data/warehouse/fpl.duckdb",
                      season: str = "2025-26") -> tuple[Anchor, ...]:
    """Recompute the verified anchors from real per-fixture returns.

    ``owned-15 points per gameweek`` is the important one. It is the expected
    return of a squad drawn at the field's own marginal ownership -- exactly
    what :class:`~fpl_edge.sim.field.FieldModel` samples -- and it is directly
    measurable, because ``fact_player_state`` carries the real
    ``selected_by_pct`` at every historical deadline.
    """
    wh = Warehouse(db_path, read_only=True)
    tot = wh.sql(
        """
        SELECT avg(t) AS v FROM (
            SELECT gw, sum(total_points) AS t
            FROM fact_player_fixture WHERE season = ? GROUP BY gw
        )
        """,
        [season],
    ).iloc[0]["v"]
    owned = wh.sql(
        """
        WITH st AS (
            SELECT code, selected_by_pct / 100.0 AS eo,
                   dense_rank() OVER (ORDER BY as_of) AS gw
            FROM fact_player_state WHERE season = ?
        ), pf AS (
            SELECT code, gw, sum(total_points) AS p
            FROM fact_player_fixture WHERE season = ? GROUP BY 1, 2
        )
        SELECT avg(v) AS v FROM (
            SELECT st.gw AS gw, sum(st.eo * coalesce(pf.p, 0)) AS v
            FROM st LEFT JOIN pf USING (code, gw) WHERE st.gw <= 38 GROUP BY 1
        )
        """,
        [season, season],
    ).iloc[0]["v"]
    starters = wh.sql(
        """
        SELECT avg(n) AS v FROM (
            SELECT gw, count(*) AS n FROM (
                SELECT code, gw, sum(minutes) AS m
                FROM fact_player_fixture WHERE season = ? GROUP BY 1, 2
            ) WHERE m >= 60 GROUP BY gw
        )
        """,
        [season],
    ).iloc[0]["v"]
    wh.close()
    src = f"warehouse fact_player_fixture / fact_player_state, {season}"
    return (
        Anchor("total player points per GW", float(tot), 110.0, src, verified=True),
        Anchor("owned-15 points per GW", float(owned), 3.0, src, verified=True,
               note="Expected points of a random 15 at the field's marginal ownership."),
        Anchor("players with 60+ minutes per GW", float(starters), 20.0, src, verified=True),
    )


def validate_points_model(points_model, snapshot, season, ownership_model,
                          *, gws=range(1, 11), n_sims: int = 900, seed: int = 5,
                          db_path: str = "data/warehouse/fpl.duckdb",
                          anchor_season: str = "2025-26") -> tuple[dict, list[str]]:
    """Check a points model's *level* against the verified warehouse anchors."""
    u = points_model.universe
    tot = np.zeros(u.n_players)
    pool = starters = 0.0
    n = 0
    for gw in gws:
        s = points_model.simulate(snapshot, season, gw, n_sims=n_sims, seed=seed)
        tot += s.mean()
        pool += float(s.points.sum(axis=0).mean())
        starters += float((s.minutes >= 60).sum(axis=0).mean())
        n += 1
    xp = tot / n
    own = _forecast(ownership_model, snapshot, season, xp)
    # The anchor is sum(selected_by_pct * points): a squad of 15 at the field's
    # marginal OWNERSHIP, bench included. Effective ownership is a different
    # quantity -- it drops benched owners and counts the captain twice -- so
    # using eo_overall here would compare the model against an anchor that
    # measures something else. See engine._align_ownership.
    column = "own_mean" if "own_mean" in own.columns else "eo_overall"
    weight = own[column].to_numpy()
    got = {
        "total player points per GW": pool / n,
        "owned-15 points per GW": float((weight * xp).sum()),
        "players with 60+ minutes per GW": starters / n,
    }
    lines = []
    for a in warehouse_anchors(db_path, anchor_season):
        lines.append(a.check(got[a.name])[1])
    return got, lines


def _forecast(ownership_model, snapshot, season, xp):
    """Ask for a forecast, handing over expected points however the model wants.

    ``OwnershipModel.forecast`` deliberately takes no expected points; models
    that want them advertise a ``set_expected_points`` hook. The development
    stand-in accepts them as a keyword instead, so both are supported rather
    than widening the shared contract.
    """
    setter = getattr(ownership_model, "set_expected_points", None)
    if setter is not None:
        setter(xp)
        return ownership_model.forecast(snapshot, season, 1)
    return ownership_model.forecast(snapshot, season, 1, expected_points=xp)


def validate_field(simulator) -> tuple[dict, list[str]]:
    """Check the *shape* of the simulated field against the published ladder."""
    ladder = simulator.field_rank_ladder()
    n = simulator.field_config.field_size
    top10k = ladder[f"rank_{round(n * 0.001):,}"]
    top1k = ladder[f"rank_{round(n * 0.0001):,}"]
    got = {
        "top10k minus mean (season)": top10k - ladder["mean"],
        "top1k minus top10k (season)": top1k - top10k,
    }
    lines = [a.check(got[a.name])[1] for a in PUBLISHED_LADDER]
    got.update(ladder)
    return got, lines
