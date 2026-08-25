"""The Pulselive confirmed-lineup feed: parser, identity bridge, PIT writes.

The parser tests run against a REAL archived payload (tests/fixtures/pulselive,
fetched 2026-08-24 from footballapi.pulselive.com), so the shape assumptions --
the zero ``playerId`` trap, ``teamLists: [null, null]`` before publication,
UTC kickoff millis -- are pinned to bytes the endpoint actually returned.
Everything else is offline against a synthetic warehouse and a fake fetcher.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest import lineups as ln
from fpl_edge.ingest.http import Fetched
from fpl_edge.store import Warehouse

UTC = dt.UTC
SEASON = "2026-27"
FIXDIR = Path(__file__).resolve().parent.parent / "fixtures" / "pulselive"
AUG1 = dt.datetime(2026, 8, 1, tzinfo=UTC)
KICKOFF = dt.datetime(2026, 8, 23, 13, 0, tzinfo=UTC)
FETCHED_AT = dt.datetime(2026, 8, 23, 12, 5, tzinfo=UTC)


@pytest.fixture(scope="module")
def completed():
    return json.loads((FIXDIR / "fixture_completed_128929.json").read_text())


# -- parser: pinned to real bytes --------------------------------------------


def test_parser_reads_the_real_teamsheet(completed):
    sides = ln.parse_team_lists(completed)
    assert sides is not None and len(sides) == 2
    bha, avl = sides
    assert bha.pl_team_id == 131 and avl.pl_team_id == 2
    assert bha.formation == "4-2-3-1"
    assert len(bha.starters) == 11 and len(bha.players) == 20  # 11 + 9 subs
    keeper = bha.starters[0]
    assert keeper.display == "Bart Verbruggen"
    assert keeper.opta_id == "p489639"
    assert keeper.shirt == 1 and keeper.position == "G"
    assert keeper.started is True
    assert all(not p.started for p in bha.players[11:])


def test_the_zero_playerid_trap_is_not_stepped_on(completed):
    """The entry's own `playerId` is 0 in the real payload; identity must come
    from the nested player object's `id`, or every row would key on zero."""
    raw = completed["teamLists"][0]["lineup"][0]
    assert raw["playerId"] == 0  # the trap, as measured
    sides = ln.parse_team_lists(completed)
    assert all(p.pl_id > 0 for side in sides for p in side.players)
    assert sides[0].starters[0].pl_id == 75709


def test_an_unpublished_teamsheet_is_not_yet_not_an_error():
    upcoming = json.loads((FIXDIR / "fixture_upcoming_128937.json").read_text())
    assert upcoming["teamLists"] == [None, None]  # the real pre-T-60m shape
    assert ln.parse_team_lists(upcoming) is None
    assert ln.parse_team_lists({"teamLists": None}) is None
    assert ln.parse_team_lists({}) is None


def test_kickoff_millis_are_read_as_utc(completed):
    # The label says "14:00 BST"; the instant is 13:00Z. Only millis is truth.
    assert ln.kickoff_utc(completed) == KICKOFF


# -- team bridge -------------------------------------------------------------


def dim_team_df():
    rows = [(36, "Brighton", "BHA"), (7, "Aston Villa", "AVL"), (1, "Man Utd", "MUN")]
    return pd.DataFrame(
        [
            {"season": SEASON, "team_code": c, "team_id": i + 1, "name": n,
             "short_name": s, "as_of": AUG1}
            for i, (c, n, s) in enumerate(rows)
        ]
    )


def test_team_bridge_matches_abbr_then_falls_back_to_name():
    pl = [
        {"team": {"id": 131, "name": "Brighton & Hove Albion",
                  "club": {"name": "Brighton and Hove Albion", "abbr": "BHA"}}},
        # No abbr agreement -> the normalised-name fallback must catch it.
        {"team": {"id": 2, "name": "Aston Villa",
                  "club": {"name": "Aston Villa", "abbr": "XXX"}}},
        # Not in our season at all (relegated side in the listing's back-pages).
        {"team": {"id": 38, "name": "Wolverhampton Wanderers",
                  "club": {"name": "Wolverhampton Wanderers", "abbr": "WOL"}}},
    ]
    bridge, misses = ln.build_team_bridge(pl, dim_team_df())
    assert bridge == {131: 36, 2: 7}
    assert len(misses) == 1 and "Wolverhampton" in misses[0]


# -- the identity bridge: name matching, ambiguity, persistence --------------


def pool(rows):
    return pd.DataFrame(
        [{"code": c, "web_name": w, "first_name": f, "second_name": s}
         for c, w, f, s in rows]
    )


def entry(pl_id, first, last, display=None, started=True):
    return ln.LineupPlayer(
        pl_id=pl_id, display=display or f"{first} {last}", first=first, last=last,
        opta_id=None, shirt=None, position=None, started=started, captain=False,
    )


def test_the_odegaard_class_of_diacritics_folds_to_a_match():
    p = pool([(1, "Ødegaard", "Martin", "Ødegaard"),
              (2, "F.Kadıoğlu", "Ferdi", "Kadıoğlu")])
    got, rep = ln.match_players(
        [entry(10, "Martin", "Odegaard"), entry(11, "Ferdi", "Kadioglu")], p
    )
    assert got == {10: (1, "name"), 11: (2, "name")}
    assert rep.matched == 2 and not rep.misses


def test_surname_first_renderings_collapse_onto_one_player():
    # FPL stores "Mitoma Kaoru" (surname first); Pulselive says "Kaoru Mitoma".
    p = pool([(3, "Mitoma", "Mitoma", "Kaoru")])
    got, _ = ln.match_players([entry(12, "Kaoru", "Mitoma")], p)
    assert got[12][0] == 3


def test_ambiguity_is_dropped_loudly_never_guessed():
    """The two-Ben-Davies rule: a tie is a drop with names attached, because a
    wrong code silently poisons every downstream join forever."""
    p = pool([(152898, "Davies", "Ben", "Davies"),
              (115556, "B.Davies", "Ben", "Davies")])
    got, rep = ln.match_players([entry(13, "Ben", "Davies")], p)
    assert got == {}
    assert rep.dropped_ambiguous == 1 and rep.matched == 0
    assert "AMBIGUOUS" in rep.misses[0]
    assert "115556" in rep.misses[0] and "152898" in rep.misses[0]


def test_last_name_tier_fires_only_when_unique_within_the_team():
    p = pool([(5, "Saka", "Bukayo", "Saka"), (6, "Timber", "Jurrien", "Timber")])
    got, _rep = ln.match_players([entry(14, "B.", "Saka", display="B. Saka")], p)
    assert got[14] == (5, "last_name")
    # Two Timbers -> the tier refuses.
    p2 = pool([(6, "J.Timber", "Jurrien", "Timber"), (8, "Q.Timber", "Quinten", "Timber")])
    got2, rep2 = ln.match_players([entry(15, "X.", "Timber", display="X. Timber")], p2)
    assert got2 == {} and rep2.dropped_ambiguous == 1


def test_a_bridged_player_never_goes_through_names_again():
    p = pool([(5, "Saka", "Bukayo", "Saka")])
    got, rep = ln.match_players([entry(16, "Totally", "Different")], p, bridge={16: 5})
    assert got[16] == (5, "bridge")
    assert rep.via_bridge == 1 and rep.matched == 1


def test_an_unknown_player_is_dropped_and_named():
    got, rep = ln.match_players([entry(17, "Triston", "Rowe")], pool([]))
    assert got == {}
    assert rep.dropped_unmatched == 1
    assert "UNMATCHED 'Triston Rowe'" in rep.misses[0]


# -- our-fixture mapping ------------------------------------------------------


def _pl_fixture(pl_id, ko, home_pl, away_pl):
    return {
        "id": pl_id,
        "kickoff": {"millis": ko.timestamp() * 1000},
        "teams": [{"team": {"id": home_pl}}, {"team": {"id": away_pl}}],
    }


def test_fixtures_map_by_kickoff_instant_plus_team_pair():
    ours = pd.DataFrame(
        [
            {"fixture_id": 7, "kickoff_utc": KICKOFF,
             "home_team_code": 36, "away_team_code": 7},
            {"fixture_id": 8, "kickoff_utc": KICKOFF,  # same instant on purpose
             "home_team_code": 43, "away_team_code": 91},
        ]
    )
    bridge = {131: 36, 2: 7, 11: 43, 127: 91}
    pl = [_pl_fixture(128929, KICKOFF, 131, 2), _pl_fixture(128930, KICKOFF, 11, 127)]
    assert ln.match_fixtures(pl, ours, bridge) == {7: 128929, 8: 128930}
    # Reversed home/away is a different fixture, not a fuzzy match.
    assert ln.match_fixtures([_pl_fixture(1, KICKOFF, 2, 131)], ours, bridge) == {}
    # A kickoff an hour off does not match either.
    off = _pl_fixture(2, KICKOFF + dt.timedelta(hours=1), 131, 2)
    assert ln.match_fixtures([off], ours, bridge) == {}


# -- the ingest, offline ------------------------------------------------------


class FakeFetcher:
    """Answers by endpoint prefix; records every call. Never touches a socket."""

    def __init__(self, bodies):
        self.bodies = bodies
        self.calls: list[str] = []

    def get_json(self, endpoint, params=None):
        self.calls.append(endpoint)
        return Fetched(
            body=self.bodies[endpoint], fetched_at=FETCHED_AT, sha256="0" * 64,
            body_path=Path("/dev/null"), http_status=200, from_cache=False,
        )

    def close(self):
        pass


def teamsheet_side(team_id, entries):
    return {
        "teamId": team_id,
        "formation": {"label": "4-4-2"},
        "lineup": [
            {"playerId": 0, "matchShirtNumber": n, "captain": False,
             "name": {"display": f"{f} {l}", "first": f, "last": l},
             "id": pid, "altIds": {"opta": f"p{pid}"}, "matchPosition": "M"}
            for pid, f, l, n in entries[:2]
        ],
        "substitutes": [
            {"playerId": 0, "matchShirtNumber": n, "captain": False,
             "name": {"display": f"{f} {l}", "first": f, "last": l},
             "id": pid, "altIds": {"opta": f"p{pid}"}, "matchPosition": "M"}
            for pid, f, l, n in entries[2:]
        ],
    }


def world(db_path):
    """dim_team, dim_player and one GW1 fixture: Brighton (36) v Villa (7)."""
    with Warehouse(db_path) as wh:
        wh.append("dim_team", dim_team_df())
        players = [
            (36, 100, "Verbruggen", "Bart", "Verbruggen"),
            (36, 101, "Ødegaard", "Martin", "Ødegaard"),
            (36, 102, "Mitoma", "Mitoma", "Kaoru"),
            (7, 200, "Cash", "Matty", "Cash"),
            (7, 201, "Davies", "Ben", "Davies"),
            (7, 202, "B.Davies", "Ben", "Davies"),  # the ambiguity trap
        ]
        wh.append(
            "dim_player",
            pd.DataFrame(
                [
                    {"season": SEASON, "code": c, "element_id": c, "web_name": w,
                     "first_name": f, "second_name": s, "position": 3,
                     "team_code": t, "as_of": AUG1}
                    for t, c, w, f, s in players
                ]
            ),
        )
        wh.append(
            "fact_fixture",
            pd.DataFrame(
                [{"season": SEASON, "fixture_id": 7, "gw": 1, "kickoff_utc": KICKOFF,
                  "home_team_code": 36, "away_team_code": 7, "finished": False,
                  "home_score": None, "away_score": None, "as_of": AUG1}]
            ),
        )
    return db_path


LISTING = {
    "content": [
        {
            "id": 900,
            "kickoff": {"millis": KICKOFF.timestamp() * 1000},
            "teams": [
                {"team": {"id": 131, "name": "Brighton & Hove Albion",
                          "club": {"name": "Brighton and Hove Albion", "abbr": "BHA"}}},
                {"team": {"id": 2, "name": "Aston Villa",
                          "club": {"name": "Aston Villa", "abbr": "AVL"}}},
            ],
        }
    ]
}

PUBLISHED = {
    "id": 900,
    "kickoff": {"millis": KICKOFF.timestamp() * 1000},
    "teams": LISTING["content"][0]["teams"],
    "teamLists": [
        teamsheet_side(131, [
            (75709, "Bart", "Verbruggen", 1),
            (75710, "Martin", "Odegaard", 8),   # diacritic fold, ASCII feed
            (75711, "Kaoru", "Mitoma", 22),     # surname-first in dim_player
        ]),
        teamsheet_side(2, [
            (80001, "Matty", "Cash", 2),
            (80002, "Ben", "Davies", 6),        # ambiguous: MUST be dropped
            (80003, "Triston", "Rowe", 44),     # not in FPL: dropped, named
        ]),
    ],
}

NOT_YET = {
    "id": 900,
    "kickoff": {"millis": KICKOFF.timestamp() * 1000},
    "teams": LISTING["content"][0]["teams"],
    "teamLists": [None, None],
}


def test_ingest_writes_pit_rows_and_earns_the_bridges(tmp_path):
    db = world(tmp_path / "w.duckdb")
    fake = FakeFetcher({"fixtures": LISTING, "fixtures/900": PUBLISHED})
    with Warehouse(db) as wh:
        rep = ln.ingest_lineups(
            wh, season=SEASON, fetcher=fake, sleep_s=0,
            now=KICKOFF - dt.timedelta(hours=1),
        )
        fx = rep["fixtures"][0]
        assert fx["status"] == "published" and fx["pl_fixture_id"] == 900
        # 4 resolvable of 6: Verbruggen, Ødegaard, Mitoma, Cash.
        assert fx["rows_written"] == 4
        sides = {s["team_code"]: s for s in fx["sides"]}
        assert sides[36]["matched"] == 3 and sides[36]["misses"] == []
        assert sides[7]["dropped_ambiguous"] == 1  # Ben Davies x2: refused
        assert sides[7]["dropped_unmatched"] == 1
        assert any("AMBIGUOUS 'Ben Davies'" in m for m in sides[7]["misses"])

        rows = wh.sql("SELECT * FROM fact_confirmed_lineup ORDER BY code")
        assert sorted(rows["code"]) == [100, 101, 102, 200]
        assert bool(rows[rows["code"] == 100]["started"].iloc[0]) is True
        assert (rows["formation"] == "4-4-2").all()
        # as_of is the FETCH instant: a deadline snapshot must never see these.
        deadline = wh.snapshot_at(dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC))
        assert deadline.table("fact_confirmed_lineup").empty
        later = wh.snapshot_at(FETCHED_AT + dt.timedelta(minutes=1))
        assert len(later.table("fact_confirmed_lineup")) == 4

        # Bridges persisted: fixture and every matched player, never the drops.
        assert wh.sql("SELECT fixture_id FROM bridge_pl_fixture")["fixture_id"].tolist() == [7]
        bridged = wh.sql("SELECT pl_player_id, code FROM bridge_pl_player ORDER BY code")
        assert dict(zip(bridged["pl_player_id"], bridged["code"])) == {
            75709: 100, 75710: 101, 75711: 102, 80001: 200,
        }


def test_second_run_joins_by_id_and_skips_the_listing(tmp_path):
    db = world(tmp_path / "w.duckdb")
    with Warehouse(db) as wh:
        ln.ingest_lineups(
            wh, season=SEASON, sleep_s=0, now=KICKOFF - dt.timedelta(hours=1),
            fetcher=FakeFetcher({"fixtures": LISTING, "fixtures/900": PUBLISHED}),
        )
        fake = FakeFetcher({"fixtures/900": PUBLISHED})  # no listing available
        rep = ln.ingest_lineups(
            wh, season=SEASON, fetcher=fake, sleep_s=0,
            now=KICKOFF - dt.timedelta(minutes=30),
        )
        assert fake.calls == ["fixtures/900"]  # fixture bridge remembered
        side = {s["team_code"]: s for s in rep["fixtures"][0]["sides"]}
        assert side[36]["via_bridge"] == 3  # players remembered too
        # Identical teamsheet at the same fetch instant: idempotent, no dupes.
        n = wh.sql("SELECT count(*) AS n FROM fact_confirmed_lineup")["n"].iloc[0]
        assert int(n) == 4


def test_an_unpublished_teamsheet_polls_clean_and_writes_nothing(tmp_path):
    db = world(tmp_path / "w.duckdb")
    fake = FakeFetcher({"fixtures": LISTING, "fixtures/900": NOT_YET})
    with Warehouse(db) as wh:
        rep = ln.ingest_lineups(
            wh, season=SEASON, fetcher=fake, sleep_s=0,
            now=KICKOFF - dt.timedelta(hours=2),
        )
        assert rep["fixtures"][0]["status"] == "not-yet"
        assert int(wh.sql("SELECT count(*) n FROM fact_confirmed_lineup")["n"].iloc[0]) == 0


def test_no_kickoffs_in_the_window_is_quiet_and_touches_nothing(tmp_path):
    db = world(tmp_path / "w.duckdb")
    fake = FakeFetcher({})
    with Warehouse(db) as wh:
        rep = ln.ingest_lineups(
            wh, season=SEASON, fetcher=fake, sleep_s=0,
            now=KICKOFF - dt.timedelta(days=3),
        )
    assert rep["fixtures"] == [] and "no kickoffs" in rep["note"]
    assert fake.calls == []  # a blank window costs the endpoint nothing
