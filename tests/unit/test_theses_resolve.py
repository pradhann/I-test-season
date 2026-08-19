"""End-to-end resolution: grading, file moves, scoreboard, counterfactuals, git.

The seeder here is deliberately deterministic (no RNG): every assertion about an
outcome is checkable by mental arithmetic against the scoring table below. It
also writes point-in-time-correct rows itself -- results carry ``as_of`` at the
gameweek's finalisation instant -- because the theses engine's whole claim is
that it only ever sees what was visible at the run instant, and a fixture that
cheats on ``as_of`` would make that claim vacuously true.

Scoring table (per finished gameweek, 8 of them):
  Hero (101, FWD £10.0, Man Utd):  2 pts in GW1-4, 10 pts in GW5-8
  PeerA-E (102-106, FWD £9.6-10.4): 3 pts every week
  Villain (107, FWD £10.0):         1 pt every week
  Cap (108, MID £12.0, 60% owned):  5 pts every week (the captaincy proxy)
  Rot (109, MID £8.0):              starts GW1-5, 20-minute cameos GW6-7
  Watchme (115, MID £5.0):          2 pts every week
"""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.store import Warehouse
from fpl_edge.theses.create import create_thesis, sync_from_registry
from fpl_edge.theses.model import ClaimType, ThesisOutcome, ThesisSource
from fpl_edge.theses.resolve import resolve_theses
from fpl_edge.theses.scoreboard import source_weights
from fpl_edge.theses.store import ThesesStore

UTC = dt.timezone.utc
SEASON = "2026-27"
T0 = dt.datetime(2026, 8, 1, 12, tzinfo=UTC)
GW1_DEADLINE = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)

#: Hero's price/ownership move on this date; the leakage tests key on it.
T_LATE_STATE = dt.datetime(2026, 9, 25, 12, tzinfo=UTC)
#: After GW4 finalised (2026-09-15 09:00Z), before the GW5 deadline (Sep 18).
T_CREATE = dt.datetime(2026, 9, 16, 12, tzinfo=UTC)
T_RESOLVE = dt.datetime(2026, 11, 1, 9, tzinfo=UTC)

TEAMS = ((1, 16, "Man Utd", "MUN"), (3, 1, "Arsenal", "ARS"),
         (43, 15, "Man City", "MCI"), (14, 13, "Liverpool", "LIV"))

#: (code, web, first, second, position, team_code, price_tenths, owned%)
PLAYERS = (
    (101, "Hero", "Harry", "Hero", 4, 1, 100, 30.0),
    (102, "PeerA", "Pa", "PeerA", 4, 3, 96, 5.0),
    (103, "PeerB", "Pb", "PeerB", 4, 43, 98, 5.0),
    (104, "PeerC", "Pc", "PeerC", 4, 14, 100, 5.0),
    (105, "PeerD", "Pd", "PeerD", 4, 3, 102, 5.0),
    (106, "PeerE", "Pe", "PeerE", 4, 43, 104, 5.0),
    (107, "Villain", "Vic", "Villain", 4, 43, 100, 8.0),
    (108, "Cap", "Carl", "Cap", 3, 14, 120, 60.0),
    (109, "Rot", "Rory", "Rot", 3, 1, 80, 3.0),
    (110, "MPeerA", "Ma", "MPeerA", 3, 3, 76, 2.0),
    (111, "MPeerB", "Mb", "MPeerB", 3, 43, 78, 2.0),
    (112, "MPeerC", "Mc", "MPeerC", 3, 14, 80, 2.0),
    (113, "MPeerD", "Md", "MPeerD", 3, 3, 82, 2.0),
    (114, "MPeerE", "Me", "MPeerE", 3, 43, 84, 2.0),
    (115, "Watchme", "Walt", "Watchme", 3, 3, 50, 1.0),
    (116, "Keeper", "Ken", "Keeper", 1, 1, 45, 70.0),  # proves GKs stay out of captain pools
)

FINISHED_GWS = 8


def gw_deadline(gw: int) -> dt.datetime:
    return GW1_DEADLINE + dt.timedelta(days=7 * (gw - 1))


def gw_finalised(gw: int) -> dt.datetime:
    return gw_deadline(gw) + dt.timedelta(days=3, hours=15, minutes=30)


def _points(code: int, gw: int) -> tuple[int, int, int, int, int]:
    """(points, minutes, starts, goals, assists) for the scoring table."""
    if code == 101:
        pts = 2 if gw <= 4 else 10
        return pts, 90, 1, (1 if gw >= 5 else 0), 0
    if code in (102, 103, 104, 105, 106):
        return 3, 90, 1, 0, 0
    if code == 107:
        return 1, 90, 1, 0, 0
    if code == 108:
        return 5, 90, 1, 0, 0
    if code == 109:
        if gw in (6, 7):
            return 1, 20, 0, 0, 0
        return 2, 90, 1, 0, 0
    if code == 115:
        return 2, 90, 1, 0, 0
    return 3, 90, 1, 0, 0


def seed_theses_warehouse(path: Path) -> Warehouse:
    """A small deterministic warehouse with PIT-correct as_of stamps."""
    wh = Warehouse(path)
    wh.append("dim_team", pd.DataFrame(
        [{"season": SEASON, "team_code": tc, "team_id": tid, "name": n,
          "short_name": s, "as_of": T0} for tc, tid, n, s in TEAMS]
    ))
    wh.append("dim_player", pd.DataFrame(
        [{"season": SEASON, "code": c, "element_id": i + 1, "web_name": w,
          "first_name": f, "second_name": s, "position": p, "team_code": tc,
          "as_of": T0}
         for i, (c, w, f, s, p, tc, _, _) in enumerate(PLAYERS)]
    ))
    wh.append("fact_player_state", pd.DataFrame(
        [{"season": SEASON, "code": c, "element_id": i + 1, "price_tenths": price,
          "selected_by_pct": own, "status": "a", "chance_of_playing_next_round": None,
          "news": "", "news_added": None, "transfers_in_event": 0,
          "transfers_out_event": 0, "cost_change_start": 0, "as_of": T0}
         for i, (c, _, _, _, _, _, price, own) in enumerate(PLAYERS)]
    ))
    # Hero's price and ownership move later in the season. Any thesis created
    # before this instant must carry the old values; after it, the new ones.
    wh.append("fact_player_state", pd.DataFrame(
        [{"season": SEASON, "code": 101, "element_id": 1, "price_tenths": 110,
          "selected_by_pct": 45.0, "status": "a", "chance_of_playing_next_round": None,
          "news": "", "news_added": None, "transfers_in_event": 0,
          "transfers_out_event": 0, "cost_change_start": 1, "as_of": T_LATE_STATE}]
    ))

    events, fixtures = [], []
    for gw in range(1, 11):
        events.append({"season": SEASON, "gw": gw, "deadline_utc": gw_deadline(gw),
                       "is_finished": False, "as_of": T0})
        # Two fixtures per gameweek across the four clubs. Written as
        # unfinished at T0 -- a result is not public before kickoff.
        for fid_offset, (home, away) in enumerate(((1, 3), (43, 14))):
            fixtures.append({
                "season": SEASON, "fixture_id": gw * 100 + fid_offset, "gw": gw,
                "kickoff_utc": gw_deadline(gw) + dt.timedelta(hours=2),
                "home_team_code": home, "away_team_code": away,
                "finished": False, "home_score": None, "away_score": None,
                "as_of": T0,
            })
    wh.append("dim_event", pd.DataFrame(events))
    wh.append("fact_fixture", pd.DataFrame(fixtures))

    rows = []
    fixture_of_team = {1: 0, 3: 0, 43: 1, 14: 1}
    for gw in range(1, FINISHED_GWS + 1):
        for code, _, _, _, _, team, _, _ in PLAYERS:
            pts, minutes, starts, goals, assists = _points(code, gw)
            rows.append({
                "season": SEASON, "code": code,
                "fixture_id": gw * 100 + fixture_of_team[team], "gw": gw,
                "minutes": minutes, "goals_scored": goals, "assists": assists,
                "clean_sheets": 0, "goals_conceded": 0, "own_goals": 0,
                "penalties_saved": 0, "penalties_missed": 0, "yellow_cards": 0,
                "red_cards": 0, "saves": 0, "bonus": 0, "bps": pts * 3,
                "starts": starts, "tackles": 0,
                "clearances_blocks_interceptions": 0, "recoveries": 0,
                "defensive_contribution": 0, "expected_goals": 0.0,
                "expected_assists": 0.0, "expected_goals_conceded": 0.0,
                "total_points": pts, "was_home": team in (1, 43),
                "as_of": gw_finalised(gw),
            })
    wh.append("fact_player_fixture", pd.DataFrame(rows))
    return wh


@pytest.fixture()
def wh(tmp_path):
    warehouse = seed_theses_warehouse(tmp_path / "wh.duckdb")
    yield warehouse
    warehouse.close()


@pytest.fixture()
def store(tmp_path):
    return ThesesStore(tmp_path / "theses")


def _file_the_book(wh: Warehouse, store: ThesesStore) -> dict[str, str]:
    """The five theses every test below reasons about. Returns id by nickname."""
    ids = {}
    t, _ = create_thesis(
        wh, raw_input="Hero is about to go on a run, buy before GW5",
        source=ThesisSource.USER_CHAT, player="hero", claim_type=ClaimType.BUY,
        gw_start=5, horizon_gws=3, as_of=T_CREATE, store=store, season=SEASON,
    )
    ids["buy"] = t.id
    t, _ = create_thesis(
        wh, raw_input="Villain is finished, sell", source=ThesisSource.CREATOR,
        creator="FPL Harry", player="villain", claim_type=ClaimType.AVOID,
        gw_start=5, horizon_gws=3, acted=True, as_of=T_CREATE, store=store,
        season=SEASON,
    )
    ids["avoid"] = t.id
    t, _ = create_thesis(
        wh, raw_input="model says Hero beats the template captain in GW5",
        source=ThesisSource.MODEL, creator="points_model", player="hero",
        claim_type=ClaimType.CAPTAIN, gw_start=5, as_of=T_CREATE, store=store,
        season=SEASON,
    )
    ids["captain"] = t.id
    t, _ = create_thesis(
        wh, raw_input="Rot is nailed now", source=ThesisSource.USER_CHAT,
        player="rot", claim_type=ClaimType.MINUTES, gw_start=5, horizon_gws=3,
        as_of=T_CREATE, store=store, season=SEASON,
    )
    ids["minutes"] = t.id
    t, _ = create_thesis(
        wh, raw_input="keep an eye on Watchme", source=ThesisSource.USER_CHAT,
        player="watchme", claim_type=ClaimType.WATCH, gw_start=5, horizon_gws=3,
        as_of=T_CREATE, store=store, season=SEASON,
    )
    ids["watch"] = t.id
    # Window reaches GW9-10, which never finalise here: must stay open.
    t, _ = create_thesis(
        wh, raw_input="Cap will keep chugging", source=ThesisSource.USER_CHAT,
        player="cap", claim_type=ClaimType.BUY, gw_start=8, horizon_gws=3,
        as_of=T_CREATE, store=store, season=SEASON,
    )
    ids["open"] = t.id
    return ids


def test_creation_freezes_claim_comparator_and_verdict(wh, store):
    ids = _file_the_book(wh, store)
    by_id = {t.id: t for t, _ in store.load_open()}

    buy = by_id[ids["buy"]]
    assert buy.falsifiable_prediction == \
        "outscores positional price-peer median over GW5-GW7"
    # Same-position players within ±£0.5m of £10.0m, excluding the subject.
    assert set(buy.comparator_codes) == {102, 103, 104, 105, 106, 107}
    assert buy.player_code == 101
    assert buy.model_verdict_at_creation["price"] == 10.0
    assert buy.model_verdict_at_creation["is_supported_club"] is True

    captain = by_id[ids["captain"]]
    # The most-owned outfielder at creation is Cap (108) -- not the 70%-owned
    # goalkeeper, who cannot be a captaincy proxy.
    assert captain.falsifiable_prediction == \
        "outscores the most-captained player Cap (code 108) in GW5"

    minutes = by_id[ids["minutes"]]
    assert minutes.falsifiable_prediction == "starts in 2+ of GW5-GW7"

    watch = by_id[ids["watch"]]
    assert watch.falsifiable_prediction is None


def test_captaining_the_most_captained_never_grades_against_itself(wh, store):
    # Cap (108) is the most-owned outfielder. A captain thesis about Cap must
    # not become "Cap outscores Cap": it falls back to the pool median, with
    # the subject excluded from its own frozen yardstick.
    t, _ = create_thesis(
        wh, raw_input="Cap is still the armband", source=ThesisSource.USER_CHAT,
        player="cap", claim_type=ClaimType.CAPTAIN, gw_start=5,
        as_of=T_CREATE, store=store, season=SEASON,
    )
    assert t.falsifiable_prediction == \
        "outscores the median of the frozen captain pool over GW5-GW5"
    assert 108 not in t.comparator_codes
    assert t.comparator_codes  # the rest of the pool is there


def test_resolve_grades_moves_scores_and_reports(wh, store):
    ids = _file_the_book(wh, store)
    report = resolve_theses(
        wh, season=SEASON, as_of=T_RESOLVE, store=store,
        dry_run=False, commit=False, sync_registry=False,
    )

    outcomes = {g.thesis.id: g.thesis.outcome for g in report.graded}
    assert outcomes[ids["buy"]] is ThesisOutcome.CORRECT       # 30 vs median 9
    assert outcomes[ids["avoid"]] is ThesisOutcome.CORRECT     # 3 vs median 9, inverted
    assert outcomes[ids["captain"]] is ThesisOutcome.CORRECT   # 10 vs Cap's 5
    assert outcomes[ids["minutes"]] is ThesisOutcome.INCORRECT  # started 1 of 3
    assert outcomes[ids["watch"]] is ThesisOutcome.UNSCORED
    assert report.still_open == (ids["open"],)

    # Files moved; the open one stayed.
    assert {t.id for t, _ in store.load_open()} == {ids["open"]}
    assert {t.id for t, _ in store.load_resolved()} == set(outcomes)

    # The buy call was never acted on: the counterfactual prices the hesitancy.
    buy = {t.id: t for t, _ in store.load_resolved()}[ids["buy"]]
    assert buy.resolution["margin"] == 21.0
    assert "NOT acted on" in buy.resolution["counterfactual"]
    assert "+21.0" in buy.resolution["counterfactual"]

    # The acted avoid says so instead.
    avoid = {t.id: t for t, _ in store.load_resolved()}[ids["avoid"]]
    assert avoid.resolution["counterfactual"].startswith("Acted on.")

    # Scoreboard: user_chat scored 1/2 (watch excluded), hesitancy +21.
    rec = {(r.entity_type, r.entity): r for r in report.scoreboard}
    user = rec[("source", "user_chat")]
    assert (user.correct, user.incorrect, user.unscored) == (1, 1, 1)
    assert user.hit_rate == 0.5
    assert user.hesitancy_cost_pts == 21.0
    harry = rec[("creator", "FPL Harry")]
    assert (harry.correct, harry.sample) == (1, 1)

    # Scoreboard files exist and history has one row per entity.
    assert (store.scoreboard_dir / "sources.json").exists()
    history = (store.scoreboard_dir / "history.csv").read_text().strip().splitlines()
    assert len(history) == 1 + len(report.scoreboard)

    # The commit message tells the story even before any commit is made.
    assert "theses: settle 5 through 2026-27 GW8" in report.commit_message
    assert "3 correct, 1 incorrect" in report.commit_message
    assert "user_chat 1/2" in report.commit_message


def test_resolve_feeds_source_weights_the_oracle_can_use(wh, store):
    _file_the_book(wh, store)
    resolve_theses(wh, season=SEASON, as_of=T_RESOLVE, store=store,
                   dry_run=False, commit=False, sync_registry=False)
    weights = source_weights(store.scoreboard_dir / "sources.json")

    # A 50% source carries no evidence; a measured 1/1 creator carries a little.
    assert weights["user_chat"].weight == 0.0
    assert weights["FPL Harry"].weight > 0.0
    assert weights["FPL Harry"].kind.value == "creator"
    assert weights["points_model"].kind.value == "own_model"
    # Shrinkage: one claim must not buy much influence.
    assert weights["FPL Harry"].weight < 0.05


def test_dry_run_touches_nothing_and_prints_everything(wh, store):
    ids = _file_the_book(wh, store)
    before = {p.name: p.read_text() for _, p in store.load_open()}

    report = resolve_theses(wh, season=SEASON, as_of=T_RESOLVE, store=store,
                            dry_run=True, sync_registry=False)

    after = {p.name: p.read_text() for _, p in store.load_open()}
    assert after == before
    assert not store.resolved_dir.exists() or not any(store.resolved_dir.iterdir())
    assert not (store.scoreboard_dir / "sources.json").exists()
    assert report.committed is None

    text = report.render()
    assert "DRY RUN" in text
    assert ids["buy"] in text
    assert "Would commit:" in text
    assert "theses: settle 5" in text


def test_resolve_is_idempotent(wh, store):
    _file_the_book(wh, store)
    first = resolve_theses(wh, season=SEASON, as_of=T_RESOLVE, store=store,
                           dry_run=False, commit=False, sync_registry=False)
    second = resolve_theses(wh, season=SEASON, as_of=T_RESOLVE, store=store,
                            dry_run=False, commit=False, sync_registry=False)
    assert len(first.graded) == 5
    assert second.graded == ()
    assert len(second.still_open) == 1


def test_half_finished_window_stays_open(wh, store):
    ids = _file_the_book(wh, store)
    # At GW6's finalisation, GW7 has not landed: every GW5-7 thesis must wait.
    # Only the one-week GW5 captaincy call has a complete window.
    report = resolve_theses(wh, season=SEASON, as_of=gw_finalised(6), store=store,
                            dry_run=False, commit=False, sync_registry=False)
    assert [g.thesis.id for g in report.graded] == [ids["captain"]]
    assert len(report.still_open) == 5


def test_resolve_commits_only_thesis_files(wh, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "unrelated.txt").write_text("must never be committed by resolve\n")
    store = ThesesStore(repo / "theses")

    _file_the_book(wh, store)
    report = resolve_theses(wh, season=SEASON, as_of=T_RESOLVE, store=store,
                            dry_run=False, commit=True, sync_registry=False)
    assert report.committed, report.notes

    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%an <%ae>%n%B", "--name-only"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert "Nripesh <nripeshpradhan@gmail.com>" in log
    assert "theses: settle 5 through 2026-27 GW8" in log
    committed_files = [
        line for line in log.splitlines() if line.strip().endswith((".md", ".json", ".csv"))
    ]
    assert committed_files, log
    assert all(f.startswith("theses/") for f in committed_files), committed_files
    assert "unrelated.txt" not in log


def test_registry_ideas_are_mirrored_idempotently(wh, store):
    from fpl_edge.interfaces.inbox import IdeaInbox

    inbox = IdeaInbox(wh)
    sub = inbox.submit("I like Hero", source="cli", now=T_CREATE)
    assert sub.ok, sub.render()

    created = sync_from_registry(wh, season=SEASON, store=store)
    assert len(created) == 1
    thesis, path = created[0]
    assert thesis.idea_id == sub.idea.idea_id
    assert thesis.player_code == 101
    assert thesis.source is ThesisSource.USER_CHAT
    assert thesis.created == sub.idea.created_utc
    # The mirrored file froze the idea's own comparator semantics.
    assert thesis.falsifiable_prediction is not None
    assert thesis.comparator_codes  # price-peer set, frozen
    assert path.exists()

    # Second sync files nothing.
    assert sync_from_registry(wh, season=SEASON, store=store) == []
