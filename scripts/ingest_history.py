"""Load vaastav's FPL history into the point-in-time warehouse.

    uv run python scripts/ingest_history.py                    # default seasons, network
    uv run python scripts/ingest_history.py --offline          # replay the local mirror
    uv run python scripts/ingest_history.py --seasons 2025-26
    uv run python scripts/ingest_history.py --build-fixtures   # regenerate test fixtures

The mirror under ``data/raw/vaastav`` is written on first fetch and reused
afterwards, so a load is reproducible offline and every byte is recorded in
``raw_fetch`` with its sha256.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from fpl_edge.ingest.vaastav import (
    CACHE_ROOT,
    DEFAULT_SEASONS,
    FILE_FIXTURES,
    FILE_MERGED_GW,
    FILE_PLAYERS_RAW,
    FILE_TEAMS,
    VaastavRepo,
    ingest_history,
    record_provenance,
    summarise,
)
from fpl_edge.store import DEFAULT_DB, Warehouse

TEST_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "vaastav"

#: Seasons kept in the committed offline fixtures. 2022-23 carries the two
#: simultaneous Ben Davieses, 2024-25 carries the manager elements, 2025-26
#: carries the defensive-contribution columns.
FIXTURE_SEASONS = ("2022-23", "2023-24", "2024-25", "2025-26")

#: Stable player codes kept in the committed fixtures, chosen to exercise the
#: identity logic rather than to be representative.
FIXTURE_CODES = (
    204480,  # Declan Rice        -- West Ham -> Arsenal, element id changes every season
    460842,  # Mohammed Kudus     -- West Ham -> Tottenham
    219847,  # Kai Havertz        -- Chelsea -> Arsenal, and changes element_type
    223094,  # Erling Haaland     -- present throughout, control
    118748,  # Mohamed Salah      -- web_name changes (Salah -> M.Salah)
    244851,  # Cole Palmer        -- same web_name as Alex Palmer
    112520,  # Alex Palmer        -- ditto
    152898,  # Ben Davies (LIV)   -- identical full name to the other Ben Davies
    115556,  # Ben Davies (TOT)   -- ditto; a name-keyed join merges these two
    232413,  # Eberechi Eze       -- Crystal Palace -> Arsenal
    224117,  # Viktor Gyokeres    -- first appears in 2025-26
    154561,  # David Raya         -- a goalkeeper, and Brentford -> Arsenal
    111234,  # Jordan Pickford    -- a goalkeeper who never moves
    537043,  # Kaine Kesler-Hayden in 2022-23 ...
    465390,  # ... and the code FPL reissued him from 2023-24. One career, two codes.
)

#: Gameweeks kept per season. Everything gets GW1-4 so the leakage test has real
#: consecutive deadlines to work with. 2024-25 additionally gets GW23-24, the
#: first gameweeks in which manager elements (element_type 5) actually appear in
#: merged_gw -- without them the manager-stripping test would be vacuous.
FIXTURE_GWS = {
    "2022-23": (1, 2, 3, 4),
    "2023-24": (1, 2, 3, 4),
    "2024-25": (1, 2, 3, 4, 23, 24),
    "2025-26": (1, 2, 3, 4),
}


def build_test_fixtures(repo: VaastavRepo, out_root: Path = TEST_FIXTURE_ROOT) -> None:
    """Slice the upstream CSVs down to a few kilobytes the unit suite can commit.

    The slice keeps real column sets, real timestamps and real identity hazards.
    ``fixtures.csv``'s ``stats`` blob is emptied for all but the first fixture of
    each season -- it is several kilobytes per row and nothing here reads it, but
    one real row is retained so the CSV quoting shape stays honest.
    """
    for season in FIXTURE_SEASONS:
        dest = out_root / season
        (dest / "gws").mkdir(parents=True, exist_ok=True)

        players = repo.read_csv(season, FILE_PLAYERS_RAW)
        keep = players[players["code"].isin(FIXTURE_CODES)].copy()
        # Two managers, to prove element_type 5 never reaches the warehouse.
        if "element_type" in players.columns:
            keep = pd.concat([keep, players[players["element_type"] == 5].head(2)])
        keep = keep.drop_duplicates("id").sort_values("id")
        keep.to_csv(dest / FILE_PLAYERS_RAW, index=False)

        teams = repo.read_csv(season, FILE_TEAMS)
        teams.to_csv(dest / FILE_TEAMS, index=False)

        gws = FIXTURE_GWS[season]
        fixtures = repo.read_csv(season, FILE_FIXTURES)
        fx = fixtures[fixtures["event"].isin(gws)].copy()
        if "stats" in fx.columns:
            mask = fx.index != fx.index[0]
            fx.loc[mask, "stats"] = "[]"
        fx.to_csv(dest / FILE_FIXTURES, index=False)

        merged = repo.read_csv(season, FILE_MERGED_GW).drop_duplicates()
        elements = set(keep["id"].astype(int))
        slice_ = merged[
            merged["element"].astype(int).isin(elements) & merged["GW"].isin(gws)
        ]
        slice_.to_csv(dest / FILE_MERGED_GW, index=False)

        print(
            f"  {season}: players_raw={len(keep)} teams={len(teams)} "
            f"fixtures={len(fx)} merged_gw={len(slice_)}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seasons", nargs="*", default=list(DEFAULT_SEASONS))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--root", default=str(CACHE_ROOT), help="local mirror of the CSVs")
    ap.add_argument("--offline", action="store_true", help="fail rather than fetch")
    ap.add_argument("--no-player-state", action="store_true",
                    help="skip fact_player_state (price/ownership) writes")
    ap.add_argument("--build-fixtures", action="store_true",
                    help="regenerate tests/fixtures/vaastav and exit")
    args = ap.parse_args()

    with VaastavRepo(args.root, offline=args.offline) as repo:
        if args.build_fixtures:
            print("Rebuilding committed test fixtures:")
            build_test_fixtures(repo)
            return

        with Warehouse(args.db) as wh:
            reports, index = ingest_history(
                wh, repo, args.seasons, write_player_state=not args.no_player_state,
            )
            n = record_provenance(wh, repo, dt.datetime.now(dt.UTC))

        print(summarise(reports))
        print(f"    raw_fetch rows recorded       {n}")
        conflicts = index.identity_conflicts()
        print(f"    identity conflicts            {len(conflicts)}")
        for code, names in list(conflicts.items())[:5]:
            print(f"      code {code}: {sorted(names)}")

        splits = index.split_identities()
        print(f"    cross-season code reissues    {len(splits)}")
        for name, by_season in list(splits.items())[:10]:
            print(f"      {name}: {by_season}")

        temporary = index.temporary_codes()
        print(f"    codes flagged temporary       {len(temporary)}")


if __name__ == "__main__":
    main()
