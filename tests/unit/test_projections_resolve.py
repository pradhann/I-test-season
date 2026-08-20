"""Name and id resolution: what it accepts, and what it refuses to guess.

The rule this file enforces everywhere: an unresolvable name is DROPPED AND
COUNTED. It is never fuzzy-matched, never assigned to the nearest candidate,
and never silently discarded. A projection on the wrong player is worse than a
missing projection, because a missing one is visible.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest.player_mapping import normalize_name
from fpl_edge.ingest.projections import livefpl, rotowire

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "projections"
AS_OF = dt.datetime(2026, 8, 20, 6, 30, tzinfo=dt.timezone.utc)


def roster(*people: tuple[int, str, str, str]) -> pd.DataFrame:
    """``(code, first, second, web)`` rows, prepared the way ingestion does."""
    frame = pd.DataFrame(
        [{"code": c, "team_code": 3, "first_name": f, "second_name": s,
          "web_name": w} for c, f, s, w in people]
    )
    return frame.assign(
        norm_full=(frame["first_name"].fillna("") + " "
                   + frame["second_name"].fillna("")).map(normalize_name),
        norm_web=frame["web_name"].map(normalize_name),
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# normalize_name is the floor everything else stands on
# ---------------------------------------------------------------------------


def test_stroke_letters_fold_rather_than_vanish():
    """"Odegaard" must match "Ødegaard".

    NFKD cannot decompose the stroke, so the combining-mark filter leaves it
    and the ``[a-z]`` strip then DELETES it -- "Ødegaard" becomes "degaard" and
    silently fails every match against the ASCII spelling every provider uses.
    """
    assert normalize_name("Martin Ødegaard") == normalize_name("Martin Odegaard")
    assert normalize_name("Gyökeres") == "gyokeres"
    assert normalize_name("Dedić") == "dedic"


# ---------------------------------------------------------------------------
# the match ladder
# ---------------------------------------------------------------------------


def test_exact_full_name_resolves():
    r = roster((1, "Bukayo", "Saka", "Saka"))
    assert rotowire._resolve_name("Bukayo Saka", r) == 1


def test_given_name_variants_resolve_on_a_unique_surname():
    """The five variants Rotowire and FPL actually disagreed about on GW1."""
    r = roster(
        (1, "Benjamin", "White", "White"),
        (2, "William", "Osula", "Osula"),
        (3, "Tino", "Livramento", "Livramento"),
        (4, "Oli", "McBurnie", "McBurnie"),
        (5, "Vitalii", "Mykolenko", "Mykolenko"),
    )
    assert rotowire._resolve_name("Ben White", r) == 1
    assert rotowire._resolve_name("Will Osula", r) == 2
    assert rotowire._resolve_name("Valentino Livramento", r) == 3
    assert rotowire._resolve_name("Oliver McBurnie", r) == 4
    assert rotowire._resolve_name("Vitaliy Mykolenko", r) == 5


def test_a_shared_surname_inside_one_club_refuses_rather_than_picking():
    """What makes the surname rung safe is the SCOPE, not the surname.

    Unique within twenty-five team-mates is close to an identifier. Two
    team-mates sharing it is ambiguity, and ambiguity is a counted drop.
    """
    r = roster((1, "Ben", "Davies", "Davies"), (2, "Bill", "Davies", "B.Davies"))
    assert rotowire._resolve_name("Benjamin Davies", r) is None


def test_a_contradictory_given_name_vetoes_a_unique_surname():
    r = roster((1, "Junior", "Kroupi", "Kroupi"))
    assert rotowire._resolve_name("Eli Kroupi", r) is None


def test_a_name_absent_from_the_roster_resolves_to_nothing():
    r = roster((1, "Bukayo", "Saka", "Saka"))
    assert rotowire._resolve_name("Djordje Petrovic", r) is None
    assert rotowire._resolve_name("Ryan McAidoo", r) is None


def test_a_bare_surname_is_enough_when_unique_in_the_club():
    r = roster((1, "Gabriel", "Magalhaes", "Gabriel"))
    assert rotowire._resolve_name("Gabriel", r) == 1


# ---------------------------------------------------------------------------
# whole-page resolution
# ---------------------------------------------------------------------------


def _entries():
    return rotowire.parse_lineups((FIXTURES / "rotowire_lineups.html").read_text())


def test_unresolved_lineup_names_are_returned_not_dropped():
    entries = _entries()
    rows, unresolved = rotowire.to_lineup_rows(
        entries, season="2026-27", gw=1, as_of=AS_OF,
        rosters=roster((1, "Bukayo", "Saka", "Saka")),
        short_to_code={"ARS": 3, "COV": 9, "HUL": 88, "MUN": 1},
    )
    assert len(rows) == 1
    assert len(rows) + len(unresolved) == len(entries)
    assert set(unresolved["reason"]) == {"no unique match on this club's roster"}


def test_a_starter_who_is_also_a_doubt_becomes_one_row_carrying_both():
    """Rotowire names Garnacho twice: in the XI, and on the doubtful list.

    That is not the page contradicting itself. It is a stronger claim than
    either half, and the first implementation threw four predicted starters
    away to dodge a primary-key collision.
    """
    entries = [
        rotowire.LineupEntry("ARS", "COV", True, "Bukayo Saka", "AMR", True, "expected"),
        rotowire.LineupEntry("ARS", "COV", True, "Bukayo Saka", "AMR", False,
                             "questionable"),
    ]
    rows, unresolved = rotowire.to_lineup_rows(
        entries, season="2026-27", gw=1, as_of=AS_OF,
        rosters=roster((1, "Bukayo", "Saka", "Saka")), short_to_code={"ARS": 3},
    )
    assert len(rows) == 1
    assert unresolved.empty
    assert bool(rows.iloc[0]["predicted_start"]) is True
    assert rows.iloc[0]["certainty"] == "questionable"


def test_a_starter_who_is_also_ruled_out_is_refused():
    """XI plus OUT is a genuine contradiction and does not merge."""
    entries = [
        rotowire.LineupEntry("ARS", "COV", True, "Bukayo Saka", "AMR", True, "expected"),
        rotowire.LineupEntry("ARS", "COV", True, "Bukayo Saka", "AMR", False, "out"),
    ]
    rows, unresolved = rotowire.to_lineup_rows(
        entries, season="2026-27", gw=1, as_of=AS_OF,
        rosters=roster((1, "Bukayo", "Saka", "Saka")), short_to_code={"ARS": 3},
    )
    assert rows.empty
    assert len(unresolved) == 2
    assert "do not merge" in unresolved.iloc[0]["reason"]


def test_two_different_names_collapsing_to_one_code_is_refused():
    entries = [
        rotowire.LineupEntry("ARS", "COV", True, "Bukayo Saka", "AMR", True, "expected"),
        rotowire.LineupEntry("ARS", "COV", True, "B. Saka", "AMR", True, "expected"),
    ]
    rows, unresolved = rotowire.to_lineup_rows(
        entries, season="2026-27", gw=1, as_of=AS_OF,
        rosters=roster((1, "Bukayo", "Saka", "Saka")), short_to_code={"ARS": 3},
    )
    assert rows.empty
    assert len(unresolved) == 2


def test_an_unknown_team_abbreviation_stops_the_ingest():
    """A mis-attributed club poisons twenty players at once, so no fallback."""
    entries = _entries()
    with pytest.raises(rotowire.RotowireError, match="not in dim_team"):
        rotowire.resolve_teams(entries, {"ARS": 3})


def test_a_page_showing_a_different_matchday_is_refused():
    entries = _entries()
    with pytest.raises(rotowire.RotowireError, match="not fixtures of"):
        rotowire.validate_fixture_pairs(
            entries, {(3, 9)}, {"ARS": 3, "COV": 9, "HUL": 88, "MUN": 1}
        )


def test_the_right_matchday_passes():
    entries = _entries()
    pairs = {(3, 9), (88, 1)}
    rotowire.validate_fixture_pairs(
        entries, pairs, {"ARS": 3, "COV": 9, "HUL": 88, "MUN": 1}
    )


# ---------------------------------------------------------------------------
# which season a keyless file belongs to
# ---------------------------------------------------------------------------


def test_season_inference_survives_a_stale_dim_player():
    """The bug that wrote 2026-27 GW1 ownership under season 2022-23.

    LiveFPL knew three players dim_player did not, so the current season was
    not a SUPERSET of the file's ids, and containment fell through to a season
    four years old whose larger id set happened to contain them all.
    """
    catalogs = {
        "2022-23": set(range(1, 779)),
        "2026-27": set(range(1, 593)),
    }
    assert livefpl.infer_season(set(range(1, 596)), catalogs) == "2026-27"


def test_season_inference_prefers_one_wrong_id_over_twenty_seven():
    """Jaccard 0.999 vs 0.969 sounds close. One wrong id vs 27 is not."""
    catalogs = {
        "2023-24": set(range(1, 866)),
        "2025-26": set(range(1, 842)),
    }
    assert livefpl.infer_season(set(range(1, 841)), catalogs) == "2025-26"


def test_season_inference_refuses_when_nothing_fits():
    catalogs = {"2026-27": set(range(1, 593))}
    with pytest.raises(livefpl.AmbiguousSeasonError, match="within 10%"):
        livefpl.infer_season(set(range(5000, 5600)), catalogs)


def test_season_inference_refuses_a_genuine_tie():
    catalogs = {"a": {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11},
                "b": {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12}}
    with pytest.raises(livefpl.AmbiguousSeasonError, match="comparably well"):
        livefpl.infer_season(set(range(1, 11)), catalogs)


def test_ownership_rows_drop_unmapped_element_ids():
    parsed = pd.DataFrame({"element_id": [1, 2, 3], "value": [0.5, 0.4, 0.3]})
    rows, unresolved = livefpl.to_ownership_rows(
        parsed, kind="predicted_eo", season="2026-27", gw=1, as_of=AS_OF,
        id_to_code={1: 100, 3: 300},
    )
    assert set(rows["code"]) == {100, 300}
    assert list(unresolved["element_id"]) == [2]
