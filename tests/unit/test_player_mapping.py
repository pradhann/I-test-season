"""Cross-season player identity.

These tests are the reason the historical training set is worth anything. FPL
reassigns ``element`` every summer, so a join on it welds different players'
careers together and produces a "form" feature that is noise with a plausible
distribution. Everything here runs offline from the committed slices in
``tests/fixtures/vaastav``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest.player_mapping import (
    IdentityCollisionError,
    PlayerCodeIndex,
    build_index,
    normalize_name,
    shares_name_token,
)
from fpl_edge.ingest.vaastav import FILE_MERGED_GW, FILE_PLAYERS_RAW, VaastavRepo

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "vaastav"
SEASONS = ("2022-23", "2023-24", "2024-25", "2025-26")

# Stable codes, read off the real archive.
RICE = 204480          # West Ham 2022-23 -> Arsenal 2023-24
KUDUS = 460842         # West Ham -> Tottenham 2025-26
RAYA = 154561          # Brentford -> Arsenal *during* 2023-24
COLE_PALMER = 244851
ALEX_PALMER = 112520   # same web_name as Cole
BEN_DAVIES_LIV = 152898
BEN_DAVIES_TOT = 115556  # identical first+second name to the Liverpool one
ARTETA = 100051017     # element_type 5, 2024-25 only
EMERY = 100037568


@pytest.fixture(scope="module")
def repo() -> VaastavRepo:
    return VaastavRepo(FIXTURE_ROOT, offline=True)


@pytest.fixture(scope="module")
def raws(repo: VaastavRepo) -> dict[str, pd.DataFrame]:
    return {s: repo.read_csv(s, FILE_PLAYERS_RAW) for s in SEASONS}


@pytest.fixture(scope="module")
def index(raws: dict[str, pd.DataFrame]) -> PlayerCodeIndex:
    idx, _ = build_index(raws, order=SEASONS)
    return idx


# -- name folding ------------------------------------------------------------


def test_normalize_name_folds_diacritics_and_punctuation() -> None:
    assert normalize_name("Viktor Gyökeres") == "viktor gyokeres"
    assert normalize_name("Kaine Kesler-Hayden") == "kaine kesler hayden"
    assert normalize_name("  Mohamed   Salah ") == "mohamed salah"
    assert normalize_name("Nott'm Forest") == "nott m forest"


def test_shares_name_token_separates_rendering_drift_from_different_people() -> None:
    # Same person, different rendering across seasons.
    assert shares_name_token("kepa arrizabalaga", "kepa arrizabalaga revuelta")
    assert shares_name_token("tomiyasu takehiro", "takehiro tomiyasu")
    # Genuinely different people.
    assert not shares_name_token("erling haaland", "declan rice")


# -- the core claim ----------------------------------------------------------


def test_element_id_is_reassigned_but_code_is_not(index: PlayerCodeIndex) -> None:
    """The whole reason this module exists."""
    elements = {s: index.element_for_code(s, RICE) for s in SEASONS}
    assert elements == {"2022-23": 467, "2023-24": 540, "2024-25": 16, "2025-26": 21}
    # Four different element ids, one code.
    assert len(set(elements.values())) == 4
    assert {index.code_for_element(s, e) for s, e in elements.items()} == {RICE}


def test_player_who_moved_clubs_keeps_one_code_across_seasons(
    index: PlayerCodeIndex, raws: dict[str, pd.DataFrame]
) -> None:
    """Declan Rice: West Ham in 2022-23, Arsenal from 2023-24. One identity."""
    assert index.seasons_for_code(RICE) == SEASONS

    clubs = {}
    for season in SEASONS:
        raw = raws[season]
        row = raw[raw["code"] == RICE].iloc[0]
        clubs[season] = int(row["team"])
        assert index.code_for_element(season, int(row["id"])) == RICE

    # He really did change club, so this is not a trivially-constant fixture.
    assert clubs["2022-23"] != clubs["2023-24"]
    assert clubs["2023-24"] == clubs["2025-26"]


def test_second_club_mover_also_keeps_one_code(index: PlayerCodeIndex) -> None:
    """Kudus: West Ham -> Tottenham, and only appears from 2023-24."""
    assert index.seasons_for_code(KUDUS) == ("2023-24", "2024-25", "2025-26")
    assert {index.code_for_element(s, index.element_for_code(s, KUDUS))
            for s in index.seasons_for_code(KUDUS)} == {KUDUS}


def test_mid_season_mover_keeps_one_code(index: PlayerCodeIndex) -> None:
    """Raya moved Brentford -> Arsenal inside 2023-24, keeping element id 113."""
    assert index.element_for_code("2023-24", RAYA) == 113
    assert index.code_for_element("2023-24", 113) == RAYA


# -- collisions --------------------------------------------------------------


def test_two_players_with_identical_names_do_not_collapse(index: PlayerCodeIndex) -> None:
    """2022-23 had two Premier League players called Ben Davies, at once.

    Liverpool's and Tottenham's. Identical first name, identical second name,
    identical web_name. A name-keyed join merges them; a code-keyed one does not.
    """
    assert BEN_DAVIES_LIV != BEN_DAVIES_TOT
    liv = index.element_for_code("2022-23", BEN_DAVIES_LIV)
    tot = index.element_for_code("2022-23", BEN_DAVIES_TOT)
    assert liv is not None and tot is not None and liv != tot
    assert index.code_for_element("2022-23", liv) == BEN_DAVIES_LIV
    assert index.code_for_element("2022-23", tot) == BEN_DAVIES_TOT

    # And the index knows the name alone is not enough to tell them apart.
    assert index.codes_for_name("2022-23", "Ben Davies") == frozenset(
        {BEN_DAVIES_LIV, BEN_DAVIES_TOT}
    )


def test_same_web_name_different_players_stay_distinct(index: PlayerCodeIndex) -> None:
    """Cole Palmer and Alex Palmer both render as web_name "Palmer"."""
    assert index.codes_for_name("2024-25", "Palmer") == frozenset(
        {COLE_PALMER, ALEX_PALMER}
    )
    assert index.identity(COLE_PALMER)["first_name"] == "Cole"
    assert index.identity(ALEX_PALMER)["first_name"] == "Alex"


def test_no_code_carries_two_different_identities(index: PlayerCodeIndex) -> None:
    """The inverse failure: distinct players collapsing onto one code."""
    assert index.identity_conflicts() == {}


def test_duplicate_code_within_a_season_is_fatal() -> None:
    """If two elements claimed one code the key would not be a key."""
    bad = pd.DataFrame([
        {"id": 1, "code": 999, "element_type": 3, "first_name": "A", "second_name": "B",
         "web_name": "AB"},
        {"id": 2, "code": 999, "element_type": 3, "first_name": "C", "second_name": "D",
         "web_name": "CD"},
    ])
    with pytest.raises(IdentityCollisionError, match="both claim code 999"):
        PlayerCodeIndex().add_season("2099-00", bad)


def test_players_raw_without_code_is_rejected() -> None:
    idx = PlayerCodeIndex()
    with pytest.raises(KeyError, match="missing"):
        idx.add_season("2099-00", pd.DataFrame([{"id": 1, "element_type": 3}]))


# -- resolution --------------------------------------------------------------


def test_merged_gw_resolves_entirely_by_element_id(
    index: PlayerCodeIndex, repo: VaastavRepo
) -> None:
    """merged_gw carries `element` and no `code`; resolution must be complete."""
    for season in SEASONS:
        merged = repo.read_csv(season, FILE_MERGED_GW).drop_duplicates()
        assert "code" not in merged.columns, f"{season}: upstream layout changed"
        resolved, report = index.resolve_frame(season, merged)
        assert report.by_code_column == 0
        assert report.by_element_id == report.resolved
        assert report.dropped_unmatched == 0
        assert report.match_rate == 1.0
        assert len(resolved) == report.resolved
        assert resolved["code"].notna().all()


def test_explicit_code_column_takes_precedence(index: PlayerCodeIndex) -> None:
    """Some seasons ship a code column; when present it is the authority."""
    df = pd.DataFrame([{"element": 467, "code": RICE, "name": "Declan Rice",
                        "position": "MID"}])
    resolved, report = index.resolve_frame("2022-23", df)
    assert report.by_code_column == 1 and report.by_element_id == 0
    assert resolved["code"].tolist() == [RICE]


def test_name_fallback_resolves_only_unambiguous_names(index: PlayerCodeIndex) -> None:
    df = pd.DataFrame([
        {"name": "Erling Haaland", "position": "FWD"},   # unique -> resolves
        {"name": "Ben Davies", "position": "DEF"},       # two players -> refused
        {"name": "Nobody At All", "position": "MID"},    # unknown -> refused
    ])
    resolved, report = index.resolve_frame("2022-23", df)
    assert report.by_name == 1
    assert report.dropped_unmatched == 2
    assert len(resolved) == 1
    assert any("ambiguous name" in s for s in report.unmatched_samples)


def test_unresolvable_rows_are_dropped_not_filled(index: PlayerCodeIndex) -> None:
    """Never impute. A row without an identity leaves, and is counted."""
    df = pd.DataFrame([
        {"element": 467, "name": "Declan Rice", "position": "MID"},
        {"element": 999999, "name": "Ghost Player", "position": "MID"},
    ])
    resolved, report = index.resolve_frame("2022-23", df)
    assert len(resolved) == 1
    assert resolved["code"].tolist() == [RICE]
    assert report.dropped_unmatched == 1
    assert report.match_rate == 0.5
    assert "code" in resolved.columns
    assert not (resolved["code"] < 0).any()  # no sentinel codes invented


def test_resolution_without_any_identifier_raises(index: PlayerCodeIndex) -> None:
    with pytest.raises(KeyError, match="no way to reach a stable code"):
        index.resolve_frame("2022-23", pd.DataFrame([{"minutes": 90}]))


# -- managers ----------------------------------------------------------------


def test_manager_elements_are_excluded_from_the_index(
    index: PlayerCodeIndex, raws: dict[str, pd.DataFrame]
) -> None:
    """element_type 5 exists in the archive and must never become a player."""
    raw = raws["2024-25"]
    manager_ids = set(raw.loc[raw["element_type"] == 5, "id"].astype(int))
    assert manager_ids, "fixture no longer contains manager elements"

    assert index.manager_elements("2024-25") == frozenset(manager_ids)
    for element in manager_ids:
        assert index.code_for_element("2024-25", element) is None
        assert index.is_manager("2024-25", element)
    for code in (ARTETA, EMERY):
        assert index.seasons_for_code(code) == ()


def test_manager_rows_are_counted_apart_from_mapping_failures(
    index: PlayerCodeIndex, repo: VaastavRepo
) -> None:
    """A dropped manager is a decision; a dropped unknown is a bug. Never merge them."""
    merged = repo.read_csv("2024-25", FILE_MERGED_GW).drop_duplicates()
    n_manager_rows = int((merged["position"] == "AM").sum())
    assert n_manager_rows > 0, "fixture no longer contains manager gameweek rows"

    resolved, report = index.resolve_frame("2024-25", merged)
    assert report.dropped_manager == n_manager_rows
    assert report.dropped_unmatched == 0
    assert report.total_rows == len(merged)
    assert report.eligible == len(merged) - n_manager_rows
    assert report.match_rate == 1.0
    # Managers are gone from the output entirely.
    assert not resolved["code"].isin([ARTETA, EMERY]).any()
    assert "AM" not in set(resolved["position"])


def test_manager_position_string_alone_is_enough_to_drop(index: PlayerCodeIndex) -> None:
    """Even if the element were unknown to players_raw, "AM" must not survive."""
    df = pd.DataFrame([{"element": 424242, "name": "Some Manager", "position": "AM"}])
    resolved, report = index.resolve_frame("2024-25", df)
    assert resolved.empty
    assert report.dropped_manager == 1 and report.dropped_unmatched == 0


# -- aggregate accounting ----------------------------------------------------


def test_match_rate_over_every_committed_season(
    index: PlayerCodeIndex, repo: VaastavRepo
) -> None:
    total = resolved_total = managers = unmatched = 0
    for season in SEASONS:
        merged = repo.read_csv(season, FILE_MERGED_GW).drop_duplicates()
        _, report = index.resolve_frame(season, merged)
        total += report.total_rows
        resolved_total += report.resolved
        managers += report.dropped_manager
        unmatched += report.dropped_unmatched
    assert unmatched == 0
    assert managers > 0
    assert resolved_total == total - managers
    assert resolved_total / (total - managers) == 1.0


def test_code_reissue_is_reported_rather_than_silently_merged(
    index: PlayerCodeIndex,
) -> None:
    """FPL sometimes reissues a code, cutting one career in half.

    Kaine Kesler-Hayden is 537043 in 2022-23 and 465390 from 2023-24. Nothing
    errors, because both halves are perfectly valid codes -- which is exactly why
    it has to be surfaced by name. It is deliberately *not* repaired: merging on
    name equality is the mistake that turns the two 2022-23 Ben Davieses into one
    player, so the halves stay distinct and the count gets published instead.
    """
    splits = index.split_identities()
    assert "kaine kesler hayden" in splits
    assert set(splits["kaine kesler hayden"].values()) == {537043, 465390}
    # Not repaired: both codes still resolve independently.
    assert index.code_for_element("2022-23", index.element_for_code("2022-23", 537043)) == 537043
    assert index.code_for_element("2023-24", index.element_for_code("2023-24", 465390)) == 465390


def test_temporary_codes_are_recorded_not_trusted() -> None:
    """FPL's own has_temporary_code flag means "this code will be replaced"."""
    raw = pd.DataFrame([
        {"id": 1, "code": 111, "element_type": 3, "first_name": "Stable",
         "second_name": "Player", "web_name": "Stable", "has_temporary_code": False},
        {"id": 2, "code": 222, "element_type": 3, "first_name": "New",
         "second_name": "Signing", "web_name": "New", "has_temporary_code": True},
    ])
    idx = PlayerCodeIndex()
    report = idx.add_season("2099-00", raw)
    assert report.temporary_codes == (222,)
    assert idx.temporary_codes("2099-00") == frozenset({222})
    # Still usable -- it is the only identifier there is -- but flagged.
    assert idx.code_for_element("2099-00", 2) == 222


def test_committed_seasons_carry_no_temporary_codes(index: PlayerCodeIndex) -> None:
    """If this ever fires, the reported match rate is optimistic."""
    assert index.temporary_codes() == frozenset()


def test_index_refuses_to_load_a_season_twice(raws: dict[str, pd.DataFrame]) -> None:
    idx = PlayerCodeIndex()
    idx.add_season("2022-23", raws["2022-23"])
    with pytest.raises(ValueError, match="already indexed"):
        idx.add_season("2022-23", raws["2022-23"])


@pytest.mark.network
def test_live_archive_still_maps_element_to_code() -> None:
    """Guards against the upstream repo changing shape between seasons."""
    live = VaastavRepo(offline=False)
    idx = PlayerCodeIndex()
    for season in ("2024-25", "2025-26"):
        idx.add_season(season, live.read_csv(season, FILE_PLAYERS_RAW))
    live.close()
    assert idx.element_for_code("2024-25", RICE) == 16
    assert idx.element_for_code("2025-26", RICE) == 21
    assert idx.identity_conflicts() == {}
