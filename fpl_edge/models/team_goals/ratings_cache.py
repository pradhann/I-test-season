"""The cached fixture-difficulty artefact: fitted ratings, panel-readable.

The fixtures panel has a 10-second budget and a Dixon-Coles fit costs about a
minute, so the panel must never fit. Instead the post-gameweek job (and the
T-30h pre-deadline refresh) runs this module, which fits the model once at the
latest snapshot and writes ``data/warehouse/fixture_difficulty.parquet`` -- one
row per (upcoming fixture, team). The panel then reads the parquet, which is
O(file read), and merges a ``difficulty`` number onto each opponent.

Why a parquet and not a warehouse table
---------------------------------------
DuckDB is one-writer-XOR-many-readers per file. This job runs alongside ingest
steps that are writers themselves; a table write here would contend for the
single-writer lock for the whole fit. The parquet sits *next to* the database,
the fit reads a private copy (``Warehouse.read_copy``), and nobody blocks
anybody. This is the same pattern as ``gw1_projection.parquet``.

This artefact is a CACHE, not a fact table. The warehouse's append-only
conventions deliberately do not apply: each run overwrites the file wholesale,
because yesterday's difficulties for fixtures that have since kicked off are
not history worth keeping -- the model can be refit at any past snapshot if an
audit ever needs them.

The difficulty formula
----------------------
Everything derives from the fitted Dixon-Coles parameters (intercept ``c``,
home advantage ``g``, per-club ``attack`` and ``defence``); no constant in the
number is invented. For a team facing opponent *O*, with *O* at home or away:

    lam_O = exp(c + g*[O at home] + attack_O + mean_defence)
        -- goals O is expected to score against a league-average defence
    mu_O  = exp(c + g*[O away]    + mean_attack + defence_O)
        -- goals a league-average attack is expected to score against O

    strength(O, venue) = lam_O - mu_O
        -- O's expected goal difference against a league-average side

    difficulty(O, venue) = (strength - min) / (max - min)

where the min and max are taken over the fixed population of all
(club in the season, venue in {home, away}) pairs -- 2N values -- so the scale
is a property of the league, not of whichever horizon happens to be requested.
``mean_attack`` / ``mean_defence`` are means over the season's clubs.

Properties: difficulty is in [0, 1] by construction, higher = harder, the
away trip to the league's best side reads 1.0 and hosting its worst reads 0.0,
and for any opponent the away fixture is harder than the home one because *O*
gains the fitted home advantage. It is a function of the opponent and venue
only -- deliberately, like FPL's own FDR, so a leaky defence does not paint a
club's whole ticker red.
"""

from __future__ import annotations

import argparse
import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from fpl_edge.models.team_goals.dixon_coles import DixonColesFit, DixonColesModel
from fpl_edge.store import DEFAULT_DB, Warehouse

UTC = dt.UTC

#: Written next to the database file, like gw1_projection.parquet.
ARTEFACT_NAME = "fixture_difficulty.parquet"

#: Matches deadline_dag.SEASON. Only the CLI default; callers pass their own.
SEASON = "2026-27"

COLUMNS = (
    "season",
    "gw",
    "fixture_id",
    "team_code",
    "opponent_code",
    "is_home",
    "difficulty",
    "fitted_at",
    "snapshot_as_of",
)


def opponent_difficulty(
    fit: DixonColesFit, season_teams: set[int]
) -> dict[tuple[int, bool], float]:
    """``(opponent_code, opponent_is_home) -> difficulty`` for one fitted model.

    Pure function of the fit; the docstring at the top of this module gives the
    formula. Normalised over the full 2N-pair league population so the values
    do not shift with the horizon.
    """
    codes = sorted(int(c) for c in season_teams)
    idx = [fit.index_of(c) for c in codes]
    atk = fit.attack[idx]
    dfn = fit.defence[idx]
    mean_atk = float(atk.mean())
    mean_dfn = float(dfn.mean())
    c, g = fit.intercept, fit.home_adv

    strength: dict[tuple[int, bool], float] = {}
    for code, a, d in zip(codes, atk, dfn, strict=True):
        for opp_home in (True, False):
            lam = np.exp(c + (g if opp_home else 0.0) + a + mean_dfn)
            mu = np.exp(c + (0.0 if opp_home else g) + mean_atk + d)
            strength[(code, opp_home)] = float(lam - mu)

    values = np.array(list(strength.values()))
    lo, hi = float(values.min()), float(values.max())
    if hi <= lo:  # a degenerate fit where every club is identical
        return {k: 0.5 for k in strength}
    return {k: (v - lo) / (hi - lo) for k, v in strength.items()}


def build_fixture_difficulty(
    wh: Warehouse, *, season: str = SEASON, now: dt.datetime | None = None
) -> pd.DataFrame:
    """Fit at the latest snapshot; difficulty rows for every upcoming fixture.

    Point-in-time: the fit reads through ``wh.snapshot_at(now)``, so it sees
    exactly the results that were public at that instant and nothing later.
    Two rows per fixture -- one per team, each carrying the difficulty its
    *opponent* poses at that venue.
    """
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    snapshot = wh.snapshot_at(now)
    fit = DixonColesModel().fit(snapshot, season)

    fixtures = snapshot.upcoming_fixtures(season)
    fitted_at = dt.datetime.now(UTC)
    if fixtures.empty:
        return pd.DataFrame(columns=list(COLUMNS))

    teams = set(fixtures["home_team_code"].astype(int)) | set(
        fixtures["away_team_code"].astype(int)
    )
    difficulty = opponent_difficulty(fit, teams)

    rows: list[dict] = []
    for fx in fixtures.itertuples(index=False):
        home, away = int(fx.home_team_code), int(fx.away_team_code)
        for team, opp, is_home in ((home, away, True), (away, home, False)):
            rows.append(
                {
                    "season": str(fx.season),
                    "gw": int(fx.gw),
                    "fixture_id": int(fx.fixture_id),
                    "team_code": team,
                    "opponent_code": opp,
                    "is_home": is_home,
                    # The opponent's venue is the inverse of ours.
                    "difficulty": difficulty[(opp, not is_home)],
                    "fitted_at": fitted_at,
                    "snapshot_as_of": snapshot.as_of,
                }
            )
    return pd.DataFrame(rows, columns=list(COLUMNS))


def write_fixture_difficulty(
    db_path: Path | str = DEFAULT_DB,
    *,
    season: str = SEASON,
    out_path: Path | str | None = None,
    now: dt.datetime | None = None,
) -> tuple[Path, pd.DataFrame]:
    """Fit from a private read copy and overwrite the parquet artefact.

    ``read_copy`` keeps the minute-long fit off the live file, so ingest
    writers are never blocked. Overwriting is correct here -- see the module
    docstring: this is a cache, not an append-only fact table.
    """
    db_path = Path(db_path)
    with Warehouse.read_copy(db_path) as wh:
        df = build_fixture_difficulty(wh, season=season, now=now)
    out = Path(out_path) if out_path is not None else db_path.parent / ARTEFACT_NAME
    df.to_parquet(out, index=False)
    return out, df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--season", default=SEASON)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    t0 = time.monotonic()
    out, df = write_fixture_difficulty(
        Path(args.db), season=args.season, out_path=args.out
    )
    seconds = time.monotonic() - t0
    if df.empty:
        print(f"no upcoming {args.season} fixtures; wrote empty {out} in {seconds:.1f}s")
    else:
        print(
            f"wrote {len(df)} rows (GW{int(df['gw'].min())}-GW{int(df['gw'].max())}, "
            f"{df['team_code'].nunique()} clubs) to {out} in {seconds:.1f}s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
