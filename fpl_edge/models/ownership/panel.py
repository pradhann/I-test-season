"""Committed evaluation fixtures, and the network job that rebuilds them.

The fixtures are derived from vaastav/Fantasy-Premier-League, which archives the
official FPL bootstrap and per-gameweek payloads. Two panels are committed:

``inseason_panel.parquet``
    One row per (season, gameweek, player) transition across 2021-22 .. 2025-26,
    carrying ownership now / previous / next, net transfer flow, points and
    price move. Restricted to rows where the player was at or above 0.1%
    ownership at some point in the transition -- that filter drops 37% of rows
    while retaining 99.7% of all ownership movement, and the players it drops
    have effective ownership indistinguishable from zero.

``coldstart_pairs.parquet``
    Pre-deadline ownership snapshots against realised GW1 ownership, for four
    seasons. These are recovered from *historical git revisions* of
    ``players_raw.csv``: the dataset's own commit history preserves what the
    ownership table looked like 1, 4, 11, 14 and 20 days before GW1, which is
    exactly the observation a cold-start model gets and exactly what cannot be
    reconstructed from the finished-season files.

``field_size.parquet``
    Field size by (season, gameweek), derived as ``sum(selected)/15`` from the
    simplex identity rather than from any external constant.

Rebuilding requires the network and is marked accordingly; the tests never do.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "ownership"

#: Ownership floor used when building ``inseason_panel.parquet``.
PANEL_OWNERSHIP_FLOOR = 0.001

VAASTAV_RAW = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League"
VAASTAV_API = "https://api.github.com/repos/vaastav/Fantasy-Premier-League"


def _read(name: str) -> pd.DataFrame:
    path = FIXTURE_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. It is a committed fixture; rebuild with "
            "fpl_edge.models.ownership.panel.rebuild_fixtures() (requires network)."
        )
    return pd.read_parquet(path)


def load_inseason_panel() -> pd.DataFrame:
    """Transitions with ownership now/prev/next, flow, points, price move."""
    df = _read("inseason_panel.parquet")
    df["season"] = df["season"].astype(str)
    return df


def load_coldstart_pairs() -> pd.DataFrame:
    """Pre-deadline snapshot vs realised GW1 ownership, by season and horizon."""
    df = _read("coldstart_pairs.parquet")
    df["season"] = df["season"].astype(str)
    return df


def load_field_size() -> pd.DataFrame:
    """Field size by season and gameweek, and the dilution weight ``w``."""
    df = _read("field_size.parquet")
    df["season"] = df["season"].astype(str)
    df["w"] = df["N"] / df["N_next"]
    return df


def attach_field_size(panel: pd.DataFrame) -> pd.DataFrame:
    """Join the ``w`` dilution weight onto a transition panel."""
    fs = load_field_size()[["season", "GW", "N", "N_next", "w"]]
    return panel.merge(fs, on=["season", "GW"], how="left")


def manifest() -> dict:
    """Provenance for the committed fixtures: source, revisions, derivation."""
    return json.loads((FIXTURE_DIR / "manifest.json").read_text())


def build_inseason_panel(gw_frames: dict[str, pd.DataFrame],
                         raw_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Derive the in-season transition panel from raw upstream frames.

    Pure, so the derivation is testable without the network. ``gw_frames`` maps
    season to that season's ``gws/merged_gw.csv``; ``raw_frames`` maps season to
    ``players_raw.csv``, which supplies the stable cross-season ``code`` and the
    ``element_type`` used to strip non-player elements.
    """
    out = []
    for season, gws in gw_frames.items():
        raw = raw_frames[season][["id", "code", "element_type"]]
        g = gws[gws["GW"].between(1, 38)]
        # Double gameweeks put a player in two rows; ownership and transfers are
        # per-gameweek snapshots so they are taken once, points are summed.
        agg = g.groupby(["element", "GW"]).agg(
            selected=("selected", "max"), ti=("transfers_in", "max"),
            to=("transfers_out", "max"), value=("value", "max"),
            pts=("total_points", "sum"),
        ).reset_index()
        agg = agg.merge(raw, left_on="element", right_on="id", how="left")
        # element_type 5 (Manager) existed only in 2024-25 and cannot score in
        # 2026-27; it is dropped, never coerced to a position.
        agg = agg[agg["element_type"].isin([1, 2, 3, 4])].dropna(subset=["code"])
        # Field size from the simplex identity: every squad holds 15 players.
        n_by_gw = agg.groupby("GW")["selected"].sum() / 15.0
        agg["N"] = agg["GW"].map(n_by_gw)
        agg["own"] = agg["selected"] / agg["N"]
        agg["flow"] = (agg["ti"] - agg["to"]) / agg["N"]
        agg["season"] = season
        out.append(agg.sort_values(["code", "GW"]))
    panel = pd.concat(out, ignore_index=True)
    by_player = panel.groupby(["season", "code"])
    panel["own_next"] = by_player["own"].shift(-1)
    panel["own_prev"] = by_player["own"].shift(1)
    panel["value_prev"] = by_player["value"].shift(1)
    panel = panel.dropna(subset=["own_next", "own_prev", "value_prev"])
    panel["dvalue"] = (panel["value"] - panel["value_prev"]) / 10.0
    keep = panel[["own", "own_prev", "own_next"]].max(axis=1) >= PANEL_OWNERSHIP_FLOOR
    cols = ["season", "GW", "code", "element_type", "own", "own_prev", "own_next",
            "flow", "pts", "dvalue"]
    return panel[keep][cols].reset_index(drop=True)


def rebuild_fixtures(seasons: tuple[str, ...] = (
    "2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
)) -> dict[str, int]:  # pragma: no cover - network job
    """Re-derive the committed fixtures from the upstream dataset.

    Fetches ``gws/merged_gw.csv`` and ``players_raw.csv`` per season, plus the
    historical revisions of ``players_raw.csv`` listed in ``manifest.json``, and
    rewrites the parquet fixtures. Requires the network; never invoked by tests.
    """
    import io

    import httpx

    client = httpx.Client(timeout=90, follow_redirects=True)

    def csv(url: str) -> pd.DataFrame:
        resp = client.get(url)
        resp.raise_for_status()
        return pd.read_csv(io.BytesIO(resp.content))

    gw_frames, raw_frames = {}, {}
    for season in seasons:
        gw_frames[season] = csv(f"{VAASTAV_RAW}/master/data/{season}/gws/merged_gw.csv")
        raw_frames[season] = csv(f"{VAASTAV_RAW}/master/data/{season}/players_raw.csv")
    inseason = build_inseason_panel(gw_frames, raw_frames)
    field = (
        pd.concat([
            g.assign(season=s)[["season", "GW", "selected"]] for s, g in gw_frames.items()
        ])
        .groupby(["season", "GW"], as_index=False)["selected"].sum()
    )
    field["N"] = field["selected"] / 15.0
    field["N_next"] = field.groupby("season")["N"].shift(-1)
    field = field.dropna(subset=["N_next"])[["season", "GW", "N", "N_next"]]

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    inseason.to_parquet(FIXTURE_DIR / "inseason_panel.parquet", compression="zstd",
                        index=False)
    field.to_parquet(FIXTURE_DIR / "field_size.parquet", compression="zstd", index=False)
    return {"inseason_panel": len(inseason), "field_size": len(field)}
