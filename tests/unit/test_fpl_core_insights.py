"""FPL-Core-Insights parse and resolve correctness, offline.

Every fixture in ``tests/fixtures/fpl_core_insights/`` is a TRIMMED COPY OF A
REAL RESPONSE this repo received on 2026-08-24 -- 11 player-match rows from
the 2026-27 GW1 Premier League ``playermatchstats.csv`` (Brentford vs Spurs
plus one Everton row), the matching 11 rows of the repo's own ``players.csv``
map, and the verbatim header-only GW2 file the repo pre-creates for gameweeks
that have not been processed yet. No test here touches the network.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest import fpl_core_insights as fci
from fpl_edge.store import Warehouse

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "fpl_core_insights"
AS_OF = dt.datetime(2026, 8, 24, 4, 0, tzinfo=dt.timezone.utc)

SPURS_MATCH = "26-27-prem-brentford-vs-tottenham-hotspur"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


def _players() -> pd.DataFrame:
    return fci.parse_players(_read("players.csv"))


def _pms() -> pd.DataFrame:
    return fci.parse_playermatchstats(_read("playermatchstats_gw1.csv"))


def _codes(players: pd.DataFrame) -> set[int]:
    return set(players["player_code"].astype(int))


# ---------------------------------------------------------------------------
# paths and parsing
# ---------------------------------------------------------------------------


def test_season_dir_matches_the_repos_layout():
    assert fci.season_dir("2026-27") == "2026-2027"
    assert fci.season_dir("2024-25") == "2024-2025"
    assert fci.playermatchstats_path("2026-27", 1) == (
        "data/2026-2027/By Tournament/Premier League/GW1/playermatchstats.csv"
    )


def test_parse_copies_the_publishers_numbers_without_touching_them():
    pms = _pms()
    assert len(pms) == 11
    schade = pms[pms["player_id"] == 94].iloc[0]
    assert schade["xg"] == pytest.approx(0.52)
    assert schade["minutes_played"] == pytest.approx(90.0)
    # A keeper's columns survive: xgot_faced is a claim only this source makes.
    kelleher = pms[pms["player_id"] == 82].iloc[0]
    assert kelleher["saves"] == pytest.approx(4.0)
    assert kelleher["xgot_faced"] == pytest.approx(0.61)


def test_a_header_only_file_is_an_absence_not_an_error():
    """The repo pre-creates every gameweek's file; empty means not-played-yet."""
    pms = fci.parse_playermatchstats(_read("playermatchstats_empty.csv"))
    assert pms.empty
    rows, unresolved = fci.to_stat_rows(
        pms, _players(), season="2026-27", gw=2, as_of=AS_OF,
        valid_codes=_codes(_players()),
    )
    assert rows.empty
    assert unresolved.empty


def test_a_schema_change_is_refused_rather_than_nulled():
    text = _read("playermatchstats_gw1.csv").replace(",xg,", ",expected_goals,")
    with pytest.raises(fci.FplCoreInsightsError, match="missing"):
        fci.parse_playermatchstats(text)


def test_an_ambiguous_player_map_is_refused():
    text = _read("players.csv")
    dup_row = text.strip().splitlines()[1]
    with pytest.raises(fci.FplCoreInsightsError, match="twice"):
        fci.parse_players(text + dup_row + "\n")


# ---------------------------------------------------------------------------
# identity resolution
# ---------------------------------------------------------------------------


def test_rows_resolve_through_the_repos_own_map_onto_stable_codes():
    players = _players()
    rows, unresolved = fci.to_stat_rows(
        _pms(), players, season="2026-27", gw=1, as_of=AS_OF,
        valid_codes=_codes(players),
    )
    assert unresolved.empty
    assert len(rows) == 11
    # player_id 94 (Schade) -> player_code 513418, the cross-season key.
    schade = rows[rows["code"] == 513418].iloc[0]
    assert schade["match_id"] == SPURS_MATCH
    assert schade["xg"] == pytest.approx(0.52)
    assert schade["tournament"] == "Premier League"
    assert schade["gw"] == 1
    # A 0-minute unused sub is a fact (named, did not play), kept with NULLs.
    henry = rows[rows["code"] == 194010].iloc[0]
    assert henry["minutes_played"] == pytest.approx(0.0)
    assert pd.isna(henry["xg"])


def test_codes_dim_player_has_never_seen_are_dropped_and_counted():
    players = _players()
    valid = _codes(players) - {513418, 200720}  # forget Schade and Kelleher
    rows, unresolved = fci.to_stat_rows(
        _pms(), players, season="2026-27", gw=1, as_of=AS_OF, valid_codes=valid,
    )
    assert len(rows) == 9
    assert 513418 not in set(rows["code"])
    assert sorted(unresolved["player_id"].astype(int)) == [82, 94]
    assert (unresolved["reason"] == "player_id not resolvable to a dim_player code").all()


def test_a_player_id_missing_from_the_repos_map_is_dropped_and_counted():
    players = _players()
    trimmed = players[players["player_id"] != 455]  # forget Tonali's mapping
    rows, unresolved = fci.to_stat_rows(
        _pms(), trimmed, season="2026-27", gw=1, as_of=AS_OF,
        valid_codes=_codes(players),
    )
    assert len(rows) == 10
    assert list(unresolved["player_id"].astype(int)) == [455]


def test_a_duplicated_player_match_row_is_refused_not_coin_flipped():
    pms = _pms()
    dup = pd.concat([pms, pms.iloc[[2]].assign(xg=0.99)], ignore_index=True)
    players = _players()
    rows, unresolved = fci.to_stat_rows(
        dup, players, season="2026-27", gw=1, as_of=AS_OF,
        valid_codes=_codes(players),
    )
    assert 513418 not in set(rows["code"]), "both contradicting rows must go"
    assert (unresolved["reason"] == "duplicate (code, match_id) within one file").all()
    assert len(unresolved) == 2


def test_naive_as_of_is_refused():
    with pytest.raises(ValueError, match="timezone-aware"):
        fci.to_stat_rows(
            _pms(), _players(), season="2026-27", gw=1,
            as_of=dt.datetime(2026, 8, 24, 4, 0),
            valid_codes=_codes(_players()),
        )


# ---------------------------------------------------------------------------
# the warehouse table
# ---------------------------------------------------------------------------


def test_rows_land_in_fact_player_match_stats_and_read_back_point_in_time(tmp_path):
    players = _players()
    rows, _ = fci.to_stat_rows(
        _pms(), players, season="2026-27", gw=1, as_of=AS_OF,
        valid_codes=_codes(players),
    )
    with Warehouse(tmp_path / "w.duckdb") as wh:
        assert wh.append("fact_player_match_stats", rows) == 11
        assert wh.append("fact_player_match_stats", rows) == 0, "idempotent"
        # Readable through the sanctioned snapshot path, and invisible to a
        # snapshot taken before the fetch instant.
        seen = wh.snapshot_at(AS_OF).table(
            "fact_player_match_stats", where="source = ?", params=[fci.SOURCE]
        )
        assert len(seen) == 11
        before = wh.snapshot_at(AS_OF - dt.timedelta(hours=1)).table(
            "fact_player_match_stats"
        )
        assert before.empty
