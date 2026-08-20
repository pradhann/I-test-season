"""Parse correctness for every ingested projection provider, offline.

Every fixture in ``tests/fixtures/projections/`` is a TRIMMED COPY OF A REAL
RESPONSE this repo received on 2026-08-20, not a hand-written approximation of
one. That distinction is the point of the directory: a hand-written fixture
tests the parser against the shape we believe the site has, which is the belief
the parser already encodes, so it can only ever agree with itself. A trimmed
real response tests it against the shape the site actually had.

No test here touches the network.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest.projections import (
    fpl_ep,
    fplform,
    github_csv,
    livefpl,
    premierinjuries,
    rotowire,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "projections"
AS_OF = dt.datetime(2026, 8, 20, 6, 30, tzinfo=dt.timezone.utc)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# FPL Form
# ---------------------------------------------------------------------------


def test_fplform_wide_csv_becomes_long_rows_with_both_factors():
    parsed = fplform.parse_csv(_read("fplform_export.csv"))
    assert set(parsed["gw"]) == set(range(1, 9))
    assert parsed["element_id"].nunique() == 6
    assert len(parsed) == 6 * 8

    raya = parsed[(parsed["element_id"] == 1) & (parsed["gw"] == 1)].iloc[0]
    # The decomposition is the reason this provider is worth more than a bare
    # xP column: the minutes opinion and the points opinion are separately
    # scoreable, and xp must be the product of the other two.
    assert raya["xp"] == pytest.approx(
        raya["xp_if_appears"] * raya["p_appear"], abs=0.02
    )
    assert 0.0 <= raya["p_appear"] <= 1.0


def test_fplform_refuses_unresolvable_element_ids_instead_of_guessing():
    parsed = fplform.parse_csv(_read("fplform_export.csv"))
    # Only three of the six element_ids are known to dim_player.
    id_to_code = {1: 154561, 2: 109745, 4: 226597}
    rows, unresolved = fplform.to_projection_rows(
        parsed, season="2026-27", as_of=AS_OF, id_to_code=id_to_code
    )
    assert set(rows["code"]) == {154561, 109745, 226597}
    assert set(unresolved["element_id"]) == {3, 5, 6}
    # Dropped AND counted: the caller can see the size of the gap.
    assert len(rows) == 3 * 8
    assert len(unresolved) == 3 * 8
    assert rows["xmins"].isna().all(), "FPL Form publishes no minutes expectation"


def test_fplform_row_count_survives_a_partial_id_map():
    """Regression: the null-gameweek bug that killed the first live run.

    An all-None ``xmins`` Series built without ``index=rows.index`` gets a
    fresh RangeIndex, which pandas ALIGNS against the filtered frame -- adding
    phantom rows whose every key is NaN. The symptom was a NOT NULL violation
    on ``gw``, three columns from the actual mistake.
    """
    parsed = fplform.parse_csv(_read("fplform_export.csv"))
    rows, _ = fplform.to_projection_rows(
        parsed, season="2026-27", as_of=AS_OF, id_to_code={6: 462424}
    )
    assert len(rows) == 8
    assert rows["gw"].notna().all()
    assert rows["code"].notna().all()
    assert sorted(rows["gw"]) == list(range(1, 9))


def test_fplform_drops_tba_columns_rather_than_guessing_a_gameweek():
    text = _read("fplform_export.csv")
    header, rest = text.split("\n", 1)
    doctored = (header + ",tba_pts_no_prob\n"
                + "\n".join(line + ",1.5" for line in rest.splitlines() if line))
    parsed = fplform.parse_csv(doctored)
    assert parsed.attrs["dropped_tba_columns"] == 1
    assert set(parsed["gw"]) == set(range(1, 9))


# ---------------------------------------------------------------------------
# FPL's own ep_next
# ---------------------------------------------------------------------------


def test_fpl_ep_keys_on_stable_code_and_reads_the_is_next_event():
    bootstrap = json.loads(_read("fpl_bootstrap.json"))
    rows = fpl_ep.to_projection_rows(bootstrap, season="2026-27", as_of=AS_OF)
    assert set(rows["gw"]) == {1}
    assert 154561 in set(rows["code"])
    assert (rows["provider"] == "fpl_ep").all()
    assert rows["xmins"].isna().all()


def test_fpl_ep_distinguishes_unflagged_from_unknown_availability():
    bootstrap = json.loads(_read("fpl_bootstrap.json"))
    rows = fpl_ep.to_projection_rows(bootstrap, season="2026-27", as_of=AS_OF)
    by_code = rows.set_index("code")["p_appear"]
    # status 'a' with a null chance means "no flag", which the game treats as
    # fully available.
    assert by_code[154561] == 1.0
    # An explicit 0% is a claim, not an absence.
    assert by_code[445122] == 0.0


def test_fpl_ep_refuses_when_no_event_is_next():
    bootstrap = json.loads(_read("fpl_bootstrap.json"))
    for ev in bootstrap["events"]:
        ev["is_next"] = False
    with pytest.raises(fpl_ep.FplEpError, match="is_next"):
        fpl_ep.to_projection_rows(bootstrap, season="2026-27", as_of=AS_OF)


# ---------------------------------------------------------------------------
# LiveFPL
# ---------------------------------------------------------------------------


def test_livefpl_keeps_effective_ownership_above_one():
    body = json.loads(_read("livefpl_predicted_eo.json"))
    parsed = livefpl.parse_ownership(body, "predicted_eo")
    assert len(parsed) == 595
    # Effective ownership includes captaincy, so exactly the template captain
    # exceeds 1.0. Clipping would destroy the signal the file exists to carry.
    assert parsed["value"].max() > 1.0
    assert 8.0 <= parsed["value"].sum() <= 18.0


def test_livefpl_rejects_a_file_that_is_not_effective_ownership():
    with pytest.raises(livefpl.LiveFplError, match="sum to"):
        livefpl.parse_ownership({"1": 0.01, "2": 0.02}, "predicted_eo")


def test_livefpl_code_map_parses():
    body = json.loads(_read("livefpl_player_info.json"))
    codes = livefpl.parse_code_map(body)
    assert len(codes) == 20
    assert all(isinstance(k, int) and isinstance(v, int) for k, v in codes.items())


# ---------------------------------------------------------------------------
# Rotowire
# ---------------------------------------------------------------------------


def test_rotowire_parses_eleven_starters_per_side_and_an_injury_list():
    entries = rotowire.parse_lineups(_read("rotowire_lineups.html"))
    abbrs = {e.team_abbr for e in entries}
    assert abbrs == {"ARS", "COV", "HUL", "MUN"}
    for abbr in abbrs:
        starters = [e for e in entries if e.team_abbr == abbr and e.predicted_start]
        assert len(starters) == 11, f"{abbr} has {len(starters)} starters"
    assert {e.certainty for e in entries if e.predicted_start} <= {"expected", "confirmed"}


def test_rotowire_aborts_on_a_short_team_sheet():
    """A nine-man XI is the page changing shape, and must stop the ingest."""
    html = _read("rotowire_lineups.html")
    cut = html.replace('<li class="lineup__player">', "<li class='dropped'>", 2)
    with pytest.raises(rotowire.RotowireError, match="starters parsed"):
        rotowire.parse_lineups(cut)


def test_rotowire_tolerates_an_unknown_player_flag():
    """One new three-letter code must not cost twenty correct team sheets.

    Rotowire introduced "SUS" mid-run and the first implementation raised on
    it, throwing the whole page away.
    """
    html = _read("rotowire_lineups.html").replace(">OUT<", ">BANANA<")
    entries = rotowire.parse_lineups(html)
    assert any(e.certainty == "unknown:BANANA" for e in entries)
    assert sum(1 for e in entries if e.predicted_start) == 44


def test_rotowire_maps_suspension_to_its_own_label():
    html = _read("rotowire_lineups.html").replace(">OUT<", ">SUS<", 1)
    entries = rotowire.parse_lineups(html)
    assert any(e.certainty == "suspended" for e in entries)


# ---------------------------------------------------------------------------
# Premier Injuries
# ---------------------------------------------------------------------------


def test_premierinjuries_reads_the_published_probability():
    entries = premierinjuries.parse_table(_read("premierinjuries_table.html"))
    assert {e.club for e in entries} == {"AFC Bournemouth", "Arsenal"}
    assert len(entries) == 13
    # The mobile-layout header div is stripped, or every value arrives prefixed
    # with its own column name.
    assert all(not e.player_name.startswith("Player") for e in entries)
    kroupi = next(e for e in entries if e.player_name == "Eli Kroupi")
    assert kroupi.status == "Ruled Out"
    assert kroupi.p_appear == 0.0
    assert kroupi.potential_return == "07/11/2026"
    assert {e.p_appear for e in entries} <= {0.0, 0.25, 0.5, 0.75, 1.0}


def test_premierinjuries_refuses_players_it_cannot_attribute_to_a_club():
    html = _read("premierinjuries_table.html").replace(
        'class="heading"', 'class="not-a-heading"'
    )
    with pytest.raises(premierinjuries.PremierInjuriesError, match="no club heading"):
        premierinjuries.parse_table(html)


def test_premierinjuries_writes_only_p_appear():
    entries = premierinjuries.parse_table(_read("premierinjuries_table.html"))
    rosters = pd.DataFrame([
        {"code": 1, "team_code": 91, "web_name": "Adli",
         "first_name": "Amine", "second_name": "Adli"},
        {"code": 2, "team_code": 3, "web_name": "Saliba",
         "first_name": "William", "second_name": "Saliba"},
    ])
    rows, unresolved = premierinjuries.to_projection_rows(
        entries, season="2026-27", gw=1, as_of=AS_OF, rosters=rosters,
        name_to_team_code={"Bournemouth": 91, "Arsenal": 3},
    )
    assert set(rows["code"]) <= {1, 2}
    assert rows["p_appear"].notna().all()
    for col in ("xp", "xp_if_appears", "xmins"):
        assert rows[col].isna().all(), f"{col} must stay NULL, not become 0"
    # Everyone else on the page is dropped and counted, never invented.
    assert len(rows) + len(unresolved) == len(entries)


def test_premierinjuries_refuses_an_unmappable_club():
    entries = premierinjuries.parse_table(_read("premierinjuries_table.html"))
    with pytest.raises(premierinjuries.PremierInjuriesError, match="dim_team"):
        premierinjuries.to_projection_rows(
            entries, season="2026-27", gw=1, as_of=AS_OF,
            rosters=pd.DataFrame(columns=["code", "team_code", "web_name",
                                          "first_name", "second_name"]),
            name_to_team_code={"Arsenal": 3},
        )


# ---------------------------------------------------------------------------
# Community GitHub feeds
# ---------------------------------------------------------------------------


def test_fplbench_keys_on_stable_code_and_publishes_minutes():
    feed = github_csv.BY_KEY["gh_fplbench"]
    parsed = github_csv.parse(feed, _read("fplbench_gw1.csv"))
    rows, unresolved = github_csv.to_projection_rows(
        feed, parsed, season="2026-27", as_of=AS_OF, id_to_code=None,
        valid_codes={223094, 141746, 424876}, default_gw=1,
    )
    assert set(rows["code"]) == {223094, 141746, 424876}
    assert len(unresolved) == 3, "codes dim_player has never seen are dropped"
    haaland = rows[rows["code"] == 223094].iloc[0]
    assert haaland["xmins"] == pytest.approx(84.371, abs=1e-3)
    assert haaland["xp"] == pytest.approx(7.4442818, abs=1e-6)
    assert pd.isna(haaland["p_appear"]), "minutes are not a probability"


def test_blueladd_expands_its_horizon_into_one_row_per_gameweek():
    feed = github_csv.BY_KEY["gh_blueladd"]
    parsed = github_csv.parse(feed, _read("blueladd_gw1.csv"))
    id_to_code = {411: 223094, 106: 500000, 426: 141746,
                  481: 600000, 82: 432720, 155: 700000}
    rows, unresolved = github_csv.to_projection_rows(
        feed, parsed, season="2026-27", as_of=AS_OF, id_to_code=id_to_code,
        valid_codes=set(id_to_code.values()), default_gw=1,
    )
    assert unresolved.empty
    assert sorted(rows["gw"].unique()) == [1, 2, 3, 4, 5, 6]
    assert len(rows) == 6 * 6

    haaland = rows[rows["code"] == 223094].set_index("gw")["xp"]
    assert haaland[1] == pytest.approx(4.89)
    assert haaland[6] == pytest.approx(5.06)
    # The minutes opinion is published for the base gameweek only. Copying it
    # forward would invent a claim the publisher never made.
    xmins = rows[rows["code"] == 223094].set_index("gw")["xmins"]
    assert xmins[1] == pytest.approx(75.7)
    assert xmins[2:].isna().all()


def test_blueladd_refuses_to_expand_an_unanchored_horizon():
    """The horizon's first element must be the file's own single-gw number.

    If it is not, the two columns are not the quantities the mapping assumes,
    and positional alignment to gw1..gw6 is a guess. The run falls back to one
    row rather than writing six confidently wrong ones.
    """
    feed = github_csv.BY_KEY["gh_blueladd"]
    text = _read("blueladd_gw1.csv").replace("4.89;4.64", "9.99;4.64")
    parsed = github_csv.parse(feed, text)
    rows, _ = github_csv.to_projection_rows(
        feed, parsed, season="2026-27", as_of=AS_OF, id_to_code={411: 223094},
        valid_codes={223094}, default_gw=1,
    )
    assert sorted(rows["gw"].unique()) == [1]
    assert rows.iloc[0]["xp"] == pytest.approx(4.89)


def test_github_feed_refuses_a_schema_change_rather_than_nulling_it():
    feed = github_csv.BY_KEY["gh_fplbench"]
    text = _read("fplbench_gw1.csv").replace("pred_minutes", "minutes_pred")
    with pytest.raises(github_csv.GithubFeedError, match="missing"):
        github_csv.parse(feed, text)


def test_every_registered_feed_declares_a_licence_and_a_cadence():
    for feed in github_csv.FEEDS:
        assert feed.licence.strip(), feed.key
        assert feed.cadence.strip(), feed.key
        assert feed.coverage.strip(), feed.key
        assert feed.key_column_kind in ("player_code", "element_id")
