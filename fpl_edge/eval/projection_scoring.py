"""The projection calibration loop: score providers against settled actuals,
then let the scores earn the ensemble weights.

``projection_weight`` had 0 rows by design until the first gameweek of the
season settled -- weighting sources without a track record is fabrication
(MASTER_PROMPT Phase 2.5). This module is the machinery that earns the rows:

1. :func:`score_gameweek` -- for every provider that had projections for a
   settled gameweek FETCHED BEFORE ITS DEADLINE, measure per-player error
   against the official ``fact_player_fixture`` actuals (MAE + RMSE, overall
   and per position; Brier for published ``p_appear``). One measurement per
   (provider, gw) is appended to ``fact_projection_score`` and kept forever.
2. :func:`fit_weights` -- inverse-MSE weights over the ACCUMULATED scores,
   written to ``projection_weight`` with loss, baseline loss and n_obs beside
   every number, exactly as that table's contract demands.

Honesty rules, each one enforced rather than remembered:

* **Point in time.** The projection scored is each provider's LAST fetch at
  or before ``dim_event.deadline_utc`` (via ``ProjectionStore.as_of``), never
  a post-deadline revision. A provider whose only fetch for the gameweek came
  after the deadline gets no score -- it made no scoreable claim.
* **Zero-fill is real.** A projected player whose team played but who never
  came off the bench scored 0 real points; dropping him would flatter every
  provider that over-projects fringe players. Players whose TEAM had no
  fixture in the gameweek (blank GW) are excluded -- there was nothing to
  predict.
* **The n_obs floor.** A provider earns a nonzero weight only with
  ``n_obs >= N_OBS_FLOOR`` (default 200 player-gameweek observations). At 200
  obs the standard error of an MSE estimate is roughly ``MSE * sqrt(2/200)``
  ~= 10% of the MSE itself, so differences smaller than that are noise a
  weight must not encode; and one fully-covered settled gameweek (~590
  projected players) clears the floor while a partial feed (a 91-player
  injury list, a provider that missed the deadline) must accumulate several
  gameweeks first. Below the floor: ``earned = FALSE``, ``weight = 0``, and
  the reason written into ``holdout`` where a reader will find it.
* **Baseline = the all-provider mean** on the same observations. Stored per
  score row and pooled into ``baseline_loss`` so "beat the consensus?" is
  answerable next to every weight.
* **Tiny samples say so.** With one gameweek scored the weights ARE earned
  if the floor is met, but the ``holdout`` text and the
  ``sem_projection_weights`` macro's ``track_record_gws`` column carry how
  deep the record is. Nothing here blends projections -- the solver blend is
  a later, explicit step; this loop ends at weights-with-evidence.

Run by ``fpl_edge.jobs.post_gw`` immediately after ``settle_results``; safe
to run any time (idempotent: already-scored (provider, gw) pairs are skipped,
a refit over unchanged scores replaces its own fit_id).
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import numpy as np
import pandas as pd

from fpl_edge.eval.calibration import brier_score
from fpl_edge.ingest.projections.store import ProjectionStore
from fpl_edge.store import Warehouse

UTC = dt.UTC

#: Minimum player-gameweek observations before a provider's weight is earned.
#: See the module docstring for the derivation; do not lower it quietly.
N_OBS_FLOOR = 200

POSITION_LABEL = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

SEASON = "2026-27"


# -- gameweek scoring --------------------------------------------------------


def _settlement_state(
    wh: Warehouse, season: str, gw: int, now: dt.datetime
) -> tuple[pd.DataFrame, str | None]:
    """The gameweek's settled actuals, or the honest reason there are none.

    Returns ``(per_player_actuals, pending_reason)``; exactly one is useful.
    Settled means: the gameweek has fixtures, every one of them is finished
    per the latest fact_fixture rows, and fact_player_fixture carries rows
    for the gameweek (written only by the results settlement gate).
    """
    snap = wh.snapshot_at(now)
    fixtures = snap.table("fact_fixture", where="season = ?", params=[season])
    fixtures = fixtures[fixtures["gw"] == gw] if not fixtures.empty else fixtures
    if fixtures.empty:
        return pd.DataFrame(), f"GW{gw}: no fixtures known"
    unfinished = fixtures[~fixtures["finished"].fillna(False).astype(bool)]
    if not unfinished.empty:
        return pd.DataFrame(), (
            f"GW{gw}: {len(unfinished)} of {len(fixtures)} fixture(s) not "
            "finished; scoring a partial gameweek would grade providers on "
            "matches that have not been played"
        )
    pf = snap.table(
        "fact_player_fixture", where="season = ? AND gw = ?", params=[season, gw]
    )
    if pf.empty:
        return pd.DataFrame(), (
            f"GW{gw}: fixtures finished but fact_player_fixture has no rows -- "
            "results settlement has not landed yet"
        )
    actual = (
        pf.groupby("code", as_index=False)
        .agg(actual_points=("total_points", "sum"), minutes=("minutes", "sum"))
    )
    # Zero-fill: every player known at the DEADLINE whose team had a fixture
    # in this gameweek made an (implicit) 0 if he never featured. Players at
    # clubs with no fixture are excluded -- nothing was there to predict.
    deadline = snap.deadline(season, gw)
    dl_snap = wh.snapshot_at(_as_utc(deadline))
    players = dl_snap.table("dim_player", where="season = ?", params=[season])
    state = dl_snap.table(
        "fact_player_state", where="season = ?", params=[season]
    )[["code", "selected_by_pct"]]
    teams_playing = set(fixtures["home_team_code"]) | set(fixtures["away_team_code"])
    universe = players[players["team_code"].isin(teams_playing)][
        ["code", "position"]
    ].merge(state, on="code", how="left")
    merged = universe.merge(actual, on="code", how="left")
    merged["actual_points"] = merged["actual_points"].fillna(0).astype(int)
    merged["minutes"] = merged["minutes"].fillna(0).astype(int)
    return merged, None


def _as_utc(ts: Any) -> dt.datetime:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        raise ValueError(f"expected tz-aware deadline, got naive {ts!r}")
    return ts.tz_convert("UTC").to_pydatetime()


def _xp_rows(
    provider: str, joined: pd.DataFrame
) -> list[dict[str, Any]]:
    """MAE/RMSE rows (overall + per position) for one provider's xp claims."""
    rows: list[dict[str, Any]] = []

    def _one(scope: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        err = df["xp"] - df["actual_points"]
        base = df["consensus_xp"] - df["actual_points"]
        n = len(df)
        rows.append({"scope": scope, "metric": "mae",
                     "value": float(err.abs().mean()),
                     "baseline": float(base.abs().mean()), "n_obs": n})
        rows.append({"scope": scope, "metric": "rmse",
                     "value": float(np.sqrt((err ** 2).mean())),
                     "baseline": float(np.sqrt((base ** 2).mean())), "n_obs": n})

    _one("overall", joined)
    for pos, label in POSITION_LABEL.items():
        _one(f"pos:{label}", joined[joined["position"] == pos])
    # Ownership cohorts: the whole board stays the headline (a model must
    # price everyone), but DECISIONS live among owned players -- a provider
    # brilliant on 0.1%-owned fodder and poor on the template is the wrong
    # model to follow. Ownership is as-of the deadline, point-in-time.
    own = joined["selected_by_pct"].fillna(0)
    _one("own_gt5", joined[own > 5])
    _one("own_gt20", joined[own > 20])
    return rows


def score_gameweek(
    wh: Warehouse,
    season: str,
    gw: int,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Score every provider's pre-deadline projections for one settled GW.

    Idempotent: a (provider, gw) already present in ``fact_projection_score``
    is skipped, so re-running after a crash measures nothing twice. Returns a
    report dict; ``report["pending"]`` is set (and nothing is written) when
    the gameweek has not settled.
    """
    now = now or dt.datetime.now(UTC)
    store = ProjectionStore(wh)
    actuals, pending = _settlement_state(wh, season, gw, now)
    if pending is not None:
        return {"season": season, "gw": gw, "pending": pending,
                "rows_written": 0, "scored": [], "skipped": []}

    deadline = _as_utc(wh.snapshot_at(now).deadline(season, gw))
    # THE point-in-time filter: the last fetch per (provider, code) at or
    # before the deadline. A projection fetched after the deadline -- possibly
    # revised on lineup news, possibly after kickoff -- is not a claim the
    # provider staked before the gameweek and must never be scored as one.
    proj = store.as_of(
        "fact_projection", deadline, where="season = ? AND gw = ?",
        params=[season, gw],
    )
    if proj.empty:
        return {"season": season, "gw": gw,
                "pending": f"GW{gw}: settled, but no provider had projections "
                           "fetched before the deadline",
                "rows_written": 0, "scored": [], "skipped": []}

    # The baseline both metrics are judged against: the all-provider mean per
    # player, over providers that made a pre-deadline claim for that player.
    consensus_xp = (
        proj[proj["xp"].notna()].groupby("code")["xp"].mean().rename("consensus_xp")
    )
    consensus_p = (
        proj[proj["p_appear"].notna()]
        .groupby("code")["p_appear"].mean().rename("consensus_p")
    )

    #: the scope that marks a fully current score-set; adding a new scope
    #: bumps this so older gameweeks backfill exactly the missing rows.
    NEWEST_SCOPE = "own_gt5"
    have = wh.sql(
        "SELECT provider, scope FROM fact_projection_score "
        "WHERE season = ? AND gw = ?", [season, gw],
    )
    scopes_by_provider: dict[str, set[str]] = {}
    for _, r in have.iterrows():
        scopes_by_provider.setdefault(str(r["provider"]), set()).add(str(r["scope"]))
    already = {p_ for p_, sc in scopes_by_provider.items() if NEWEST_SCOPE in sc}

    out_rows: list[dict[str, Any]] = []
    scored: list[dict[str, Any]] = []
    skipped: list[str] = []
    for provider, dfp in proj.groupby("provider"):
        provider = str(provider)
        if provider in already:
            skipped.append(provider)
            continue
        joined = dfp.merge(actuals, on="code", how="inner")
        rows: list[dict[str, Any]] = []

        xp_obs = (
            joined[joined["xp"].notna()]
            .merge(consensus_xp, on="code", how="left")
        )
        rows.extend(_xp_rows(provider, xp_obs))

        p_obs = (
            joined[joined["p_appear"].notna()]
            .merge(consensus_p, on="code", how="left")
        )
        if not p_obs.empty:
            played = (p_obs["minutes"] > 0).astype(int).to_numpy()
            rows.append({
                "scope": "p_appear", "metric": "brier",
                "value": brier_score(p_obs["p_appear"].to_numpy(), played),
                "baseline": brier_score(p_obs["consensus_p"].to_numpy(), played),
                "n_obs": len(p_obs),
            })

        existing = scopes_by_provider.get(provider, set())
        if existing:
            rows = [r for r in rows if r["scope"] not in existing]
        if not rows:
            skipped.append(provider)
            continue
        for r in rows:
            r.update({"provider": provider, "season": season, "gw": int(gw),
                      "deadline_utc": deadline, "as_of": now})
        out_rows.extend(rows)
        overall = next((r for r in rows if r["scope"] == "overall"
                        and r["metric"] == "mae"), None)
        scored.append({
            "provider": provider,
            "mae": None if overall is None else round(overall["value"], 4),
            "baseline_mae": None if overall is None else round(overall["baseline"], 4),
            "n_obs": None if overall is None else overall["n_obs"],
        })

    written = store.append("fact_projection_score", pd.DataFrame(out_rows)) \
        if out_rows else 0
    scored.sort(key=lambda r: (r["mae"] is None, r["mae"]))
    return {"season": season, "gw": int(gw), "pending": None,
            "deadline_utc": deadline.isoformat(), "rows_written": int(written),
            "scored": scored, "skipped": sorted(skipped)}


# -- weight fitting ----------------------------------------------------------


def fit_weights(
    wh: Warehouse,
    season: str,
    *,
    n_obs_floor: int = N_OBS_FLOOR,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Inverse-MSE weights over the accumulated track record.

    Pools each provider's per-gameweek 'overall' RMSE rows into one MSE
    (observation-weighted), then ``weight_i = (1/mse_i) / sum(1/mse_j)`` over
    providers that clear the ``n_obs`` floor. Everyone else gets an explicit
    ``earned = FALSE, weight = 0`` row whose ``holdout`` says why -- absence
    of evidence is recorded, never implied. Writes via
    ``ProjectionStore.record_weights`` under a deterministic fit_id
    (``{season}:invmse:thru-gw{N}``), so refitting the same state replaces
    the fit instead of stacking copies.
    """
    now = now or dt.datetime.now(UTC)
    store = ProjectionStore(wh)
    scores = wh.sql(
        """
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY provider, season, gw, scope, metric
                ORDER BY as_of DESC) rn
            FROM fact_projection_score
            WHERE season = ? AND scope = 'overall' AND metric = 'rmse'
        ) WHERE rn = 1
        """,
        [season],
    )
    if scores.empty:
        return {"season": season,
                "pending": "no scored gameweeks yet -- projection_weight "
                           "stays empty rather than holding opinions",
                "fit_id": None, "weights": []}

    pooled = {}
    for provider, dfp in scores.groupby("provider"):
        n = dfp["n_obs"].astype(int)
        mse = float((dfp["value"] ** 2 * n).sum() / n.sum())
        base = float((dfp["baseline"] ** 2 * n).sum() / n.sum())
        pooled[str(provider)] = {
            "mse": mse, "baseline_mse": base, "n_obs": int(n.sum()),
            "gws": sorted(int(g) for g in dfp["gw"].unique()),
        }

    gws_scored = sorted(int(g) for g in scores["gw"].unique())
    depth = (f"track record {len(gws_scored)} gameweek(s) deep "
             f"(GW{', GW'.join(str(g) for g in gws_scored)})")

    providers = sorted(
        wh.sql(
            "SELECT provider, count(xp) AS n_xp FROM fact_projection "
            "WHERE season = ? GROUP BY 1", [season],
        ).itertuples(index=False),
        key=lambda r: r.provider,
    )

    rows: list[dict[str, Any]] = []
    for p in providers:
        provider = str(p.provider)
        stats = pooled.get(provider)
        row: dict[str, Any] = {
            "provider": provider, "weight": 0.0, "loss": None,
            "loss_metric": "mse", "baseline_loss": None, "n_obs": 0,
            "earned": False, "as_of": now,
        }
        if int(p.n_xp) == 0:
            row["holdout"] = ("publishes p_appear only, no xp to score; "
                             "weight not applicable. " + depth)
        elif stats is None:
            row["holdout"] = ("no pre-deadline xp projections for any settled "
                             "gameweek yet. " + depth)
        else:
            row.update({"loss": stats["mse"],
                        "baseline_loss": stats["baseline_mse"],
                        "n_obs": stats["n_obs"]})
            if stats["n_obs"] < n_obs_floor:
                row["holdout"] = (
                    f"below the n_obs floor: {stats['n_obs']} < {n_obs_floor} "
                    f"player-GW observations -- measured but not yet earned. "
                    + depth)
            else:
                row["earned"] = True
                row["holdout"] = (
                    f"{season} settled actuals, {stats['n_obs']} player-GW "
                    f"obs, walk-forward accumulation, baseline = all-provider "
                    f"mean. " + depth)
        rows.append(row)

    # Floor the MSE before inverting: an MSE of exactly 0 (possible only in
    # degenerate samples, but division by zero is division by zero) would
    # otherwise hand one provider an infinite weight.
    inv = {r["provider"]: 1.0 / max(pooled[r["provider"]]["mse"], 1e-9)
           for r in rows if r["earned"]}
    total = sum(inv.values())
    for r in rows:
        if r["earned"]:
            r["weight"] = inv[r["provider"]] / total

    fit_id = f"{season}:invmse:thru-gw{max(gws_scored)}"
    store.record_weights(fit_id, pd.DataFrame(rows))
    return {
        "season": season, "pending": None, "fit_id": fit_id,
        "gameweeks_scored": gws_scored,
        "weights": [
            {k: r[k] for k in ("provider", "weight", "loss", "baseline_loss",
                               "n_obs", "earned")}
            for r in sorted(rows, key=lambda r: -r["weight"])
        ],
    }


# -- the post_gw entry point -------------------------------------------------


def run(wh: Warehouse, season: str = SEASON,
        *, now: dt.datetime | None = None) -> dict[str, Any]:
    """Score any newly settled gameweeks, then refit. The post_gw step."""
    now = now or dt.datetime.now(UTC)
    ProjectionStore(wh)
    settled = wh.sql(
        "SELECT DISTINCT gw FROM fact_player_fixture WHERE season = ?", [season]
    )
    if settled.empty:
        return {"season": season,
                "pending": "no settled gameweeks in fact_player_fixture yet; "
                           "scoring and weighting wait for real actuals",
                "scoring": [], "fit": None}
    reports = [score_gameweek(wh, season, int(g), now=now)
               for g in sorted(settled["gw"].astype(int))]
    fit = fit_weights(wh, season, now=now)
    return {"season": season, "pending": None, "scoring": reports, "fit": fit}


def main() -> int:
    with Warehouse() as wh:
        report = run(wh)
    print(json.dumps(report, indent=1, default=str))
    # Pending is not failure: before settlement the honest result is "not yet".
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
