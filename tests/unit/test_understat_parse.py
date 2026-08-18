"""Understat parsing and the robots.txt gate.

Fixtures are real bytes fetched 2026-08-18:

* ``match_28778.html`` -- ``GET /match/28778`` (Liverpool 4-2 Bournemouth,
  2025-08-15), HTTP 200, 30,639 bytes.
* ``league_EPL_2026_served_page.html`` -- what ``GET /league/EPL/2026``
  actually served, which is the 2025/26 page. That substitution is the reason
  Understat cannot inform a 2026-27 decision, so it gets a test rather than a
  footnote.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from fpl_edge.ingest.understat import (
    LATEST_SEASON_OBSERVED,
    RobotsDisallowed,
    extract_json_vars,
    fetch_match,
    has_shot_level_data,
    parse_match_info,
    season_is_available,
    season_start_year,
)

FIX = Path(__file__).parents[1] / "fixtures" / "understat"
UTC = dt.timezone.utc


@pytest.fixture(scope="module")
def match_html() -> str:
    return (FIX / "match_28778.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def league_html() -> str:
    return (FIX / "league_EPL_2026_served_page.html").read_text(encoding="utf-8")


# -- season availability -----------------------------------------------------


def test_season_start_year_uses_understats_convention() -> None:
    assert season_start_year("2025-26") == 2025
    assert season_start_year("2026-27") == 2026


def test_season_start_year_rejects_malformed_input() -> None:
    for bad in ("2026", "2026/27", ""):
        with pytest.raises(ValueError):
            season_start_year(bad)


def test_the_current_season_is_not_available_on_understat() -> None:
    """The finding that decides whether Understat can inform GW1 2026-27.

    Understat's own season <select> topped out at 2025 when measured, and
    requesting 2026 silently served the 2025/26 page. So there is no xG for
    2026-27 to ingest before a ball is kicked, at any price.
    """
    assert LATEST_SEASON_OBSERVED == 2025
    assert season_is_available("2025-26")
    assert not season_is_available("2026-27")


def test_requesting_2026_actually_served_the_2025_26_page(league_html: str) -> None:
    """Silent fallback, captured in bytes. This is why the guard exists.

    A caller that trusted the HTTP 200 would have ingested last season's table
    as if it were this season's.
    """
    assert "2025/2026 season" in league_html
    assert "2026/2027" not in league_html
    # The season picker offers 2014..2025 and stops there.
    assert 'value="2025"' in league_html
    assert 'value="2026"' not in league_html


# -- match parsing -----------------------------------------------------------


def test_extracts_the_embedded_json_variables(match_html: str) -> None:
    blobs = extract_json_vars(match_html)
    assert "match_info" in blobs
    assert blobs["match_info"]["team_h"] == "Liverpool"


def test_shot_level_data_is_no_longer_on_the_match_page(match_html: str) -> None:
    """The other decisive finding.

    Understat's match HTML used to embed ``shotsData`` with per-shot xG. It does
    not any more. Anything claiming to give us shot-level xG from this page is
    describing the site as it was, not as it is.
    """
    assert not has_shot_level_data(match_html)
    assert sorted(extract_json_vars(match_html)) == ["match_info"]


def test_parses_one_row_per_team(match_html: str) -> None:
    df = parse_match_info(match_html)
    assert len(df) == 2
    assert set(df["team"]) == {"Liverpool", "Bournemouth"}
    assert df["is_home"].tolist() == [True, False]


def test_recovers_known_match_facts(match_html: str) -> None:
    """Liverpool 4-2 Bournemouth, xG 2.33 - 1.57."""
    df = parse_match_info(match_html).set_index("team")
    lfc = df.loc["Liverpool"]
    assert lfc["goals"] == 4
    assert lfc["goals_conceded"] == 2
    assert lfc["xg"] == pytest.approx(2.33007)
    assert lfc["xg_conceded"] == pytest.approx(1.57303)
    assert lfc["shots"] == 19
    assert lfc["shots_on_target"] == 10
    assert lfc["understat_match_id"] == 28778
    assert lfc["season_start_year"] == 2025


def test_home_and_away_rows_are_mirror_images(match_html: str) -> None:
    """The symmetry that makes the long format worth using."""
    df = parse_match_info(match_html).set_index("team")
    h, a = df.loc["Liverpool"], df.loc["Bournemouth"]
    assert h["xg"] == a["xg_conceded"]
    assert h["xg_conceded"] == a["xg"]
    assert h["goals"] == a["goals_conceded"]
    assert h["opponent"] == "Bournemouth"
    assert a["opponent"] == "Liverpool"


def test_kickoff_is_parsed_as_utc(match_html: str) -> None:
    df = parse_match_info(match_html)
    ko = df.iloc[0]["kickoff_utc"]
    assert ko == dt.datetime(2025, 8, 15, 19, 0, tzinfo=UTC)
    assert ko.tzinfo is not None


def test_explicit_as_of_overrides_the_kickoff_default(match_html: str) -> None:
    """Callers landing this in a PIT table must stamp publication, not kickoff."""
    stamp = dt.datetime(2025, 8, 16, 9, tzinfo=UTC)
    df = parse_match_info(match_html, as_of=stamp)
    assert (df["as_of"] == stamp).all()


def test_a_page_without_match_info_raises_with_the_variables_it_did_find() -> None:
    """Understat changes its page structure; the error should say what changed."""
    with pytest.raises(ValueError, match="page structure has changed"):
        parse_match_info("<html><body>nothing here</body></html>")


def test_parser_tolerates_a_truncated_json_blob() -> None:
    """A half-written archive file must not produce a half-parsed match."""
    broken = "<script>var match_info = JSON.parse('{\\\"id\\\": \\\"1\\\", ');</script>"
    assert extract_json_vars(broken) == {}


# -- the robots gate ---------------------------------------------------------


def test_fetch_refuses_when_robots_disallows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Understat serves ``User-agent: * / Disallow: /``, so the default is no."""
    monkeypatch.setattr("fpl_edge.ingest.understat.robots_allows", lambda *a, **k: False)
    with pytest.raises(RobotsDisallowed, match="Disallow"):
        fetch_match(28778)


def test_robots_check_fails_closed_when_the_site_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable robots.txt must mean "no", never "assume yes"."""
    import fpl_edge.ingest.understat as us

    class Boom:
        def set_url(self, *_a: object) -> None: ...
        def read(self) -> None:
            raise OSError("network down")
        def can_fetch(self, *_a: object) -> bool:
            return True  # would say yes, but read() failed first

    monkeypatch.setattr(us.urllib.robotparser, "RobotFileParser", Boom)
    assert us.robots_allows("/match/1") is False


def test_override_is_explicit_and_does_not_forge_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The override skips our refusal; it must not change how we identify.

    No TLS impersonation, no browser User-Agent, no cookie forgery -- the
    request still goes out as fpl-edge with the polite delay.
    """
    import fpl_edge.ingest.understat as us

    monkeypatch.setattr(us, "robots_allows", lambda *a, **k: False)
    seen: dict[str, object] = {}

    class FakeFetcher:
        def get_text(self, endpoint: str, suffix: str = "") -> str:
            seen["endpoint"] = endpoint
            return "ok"
        def close(self) -> None: ...

    monkeypatch.setattr(us.time, "sleep", lambda _s: seen.setdefault("slept", True))
    out = fetch_match(28778, fetcher=FakeFetcher(), override_robots=True, delay_s=0.0)
    assert out == "ok"
    assert seen["endpoint"] == "match/28778"
    assert seen.get("slept") is True  # the delay is not skipped
    assert us.USER_AGENT.startswith("fpl-edge/")
