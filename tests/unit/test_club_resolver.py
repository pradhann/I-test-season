"""The club resolver refuses rather than guesses.

Team names in creator transcripts are ASR output. This warehouse really holds
``forester``, ``suddenland``, ``ipsswitch`` and ``leads``. The obvious fix --
nearest club by edit distance over a closed set of twenty -- is what these tests
exist to prevent, because on the actual twenty clubs of 2026-27 the nearest club
to ``forester`` is BRENTFORD and the nearest to ``hull`` is FULHAM.

An insight attributed to the wrong club does not look wrong. It renders in that
club's fixture drawer, in the creator's own words, with a verbatim quote and a
confidence. It is indistinguishable from a true one.
"""

from __future__ import annotations

import pytest

from fpl_edge.ingest.content.clubs import ClubResolver

#: The twenty clubs of 2026-27, exactly as dim_team carries them.
CLUBS = [
    {"team_code": 3, "name": "Arsenal", "short_name": "ARS"},
    {"team_code": 7, "name": "Aston Villa", "short_name": "AVL"},
    {"team_code": 91, "name": "Bournemouth", "short_name": "BOU"},
    {"team_code": 94, "name": "Brentford", "short_name": "BRE"},
    {"team_code": 36, "name": "Brighton", "short_name": "BHA"},
    {"team_code": 8, "name": "Chelsea", "short_name": "CHE"},
    {"team_code": 9, "name": "Coventry City", "short_name": "COV"},
    {"team_code": 31, "name": "Crystal Palace", "short_name": "CRY"},
    {"team_code": 11, "name": "Everton", "short_name": "EVE"},
    {"team_code": 54, "name": "Fulham", "short_name": "FUL"},
    {"team_code": 88, "name": "Hull City", "short_name": "HUL"},
    {"team_code": 40, "name": "Ipswich Town", "short_name": "IPS"},
    {"team_code": 2, "name": "Leeds", "short_name": "LEE"},
    {"team_code": 14, "name": "Liverpool", "short_name": "LIV"},
    {"team_code": 43, "name": "Man City", "short_name": "MCI"},
    {"team_code": 1, "name": "Man Utd", "short_name": "MUN"},
    {"team_code": 4, "name": "Newcastle", "short_name": "NEW"},
    {"team_code": 17, "name": "Nott'm Forest", "short_name": "NFO"},
    {"team_code": 6, "name": "Spurs", "short_name": "TOT"},
    {"team_code": 56, "name": "Sunderland", "short_name": "SUN"},
]
BY_NAME = {c["name"]: c["team_code"] for c in CLUBS}


@pytest.fixture
def resolver() -> ClubResolver:
    return ClubResolver(CLUBS, "2026-27")


@pytest.mark.parametrize("spoken,expected", [
    ("Arsenal", "Arsenal"),
    ("arsenal", "Arsenal"),
    ("ARS", "Arsenal"),
    ("Crystal Palace", "Crystal Palace"),
    ("Spurs", "Spurs"),
    ("Man City", "Man City"),
    ("Man Utd", "Man Utd"),
])
def test_a_name_the_club_actually_uses_resolves_exactly(resolver, spoken, expected):
    code, basis = resolver.lookup(spoken)
    assert code == BY_NAME[expected], f"{spoken} -> {code} via {basis}"
    assert basis == "exact"


@pytest.mark.parametrize("spoken,expected", [
    ("Hull", "Hull City"),            # the speaker drops the competition suffix
    ("Ipswich", "Ipswich Town"),
    ("Coventry", "Coventry City"),
    ("Forest", "Nott'm Forest"),
    ("Forester", "Nott'm Forest"),    # ASR adds a syllable to a real token
])
def test_a_shortened_or_extended_form_resolves_by_containment(resolver, spoken, expected):
    code, basis = resolver.lookup(spoken)
    assert code == BY_NAME[expected], f"{spoken} -> {code} via {basis}"
    assert basis == "token"


@pytest.mark.parametrize("spoken", ["Suddenland", "Ipsswitch", "leads", "Wolves", ""])
def test_a_name_it_cannot_place_is_refused_not_approximated(resolver, spoken):
    """Every one of these is a real string from the live warehouse or a club
    that is not in this league. None may resolve to anything."""
    code, basis = resolver.lookup(spoken)
    assert code is None, f"{spoken!r} was attributed to {code} via {basis}"
    assert basis, "a refusal must carry a reason"


def test_it_never_picks_the_nearest_club_by_edit_distance(resolver):
    """The load-bearing test. These are the two cases that make nearest-match
    unsafe on this exact set of twenty clubs."""
    # "forester" is edit distance 6 from Brentford and 7 from Nott'm Forest, so
    # a nearest-match resolver picks Brentford. Containment picks Forest.
    code, _ = resolver.lookup("Forester")
    assert code == BY_NAME["Nott'm Forest"]
    assert code != BY_NAME["Brentford"], (
        "'forester' resolved to Brentford -- this is the edit-distance failure "
        "the module exists to avoid")

    # "hull" is edit distance 4 from BOTH Fulham and Hull City. A nearest-match
    # resolver has no principled way to break that tie.
    code, _ = resolver.lookup("Hull")
    assert code == BY_NAME["Hull City"]
    assert code != BY_NAME["Fulham"]


def test_two_clubs_sharing_a_word_resolve_to_neither(resolver):
    """`City` is Coventry and Hull here, and `Man` is City and Utd. A token
    that does not identify one club must identify none -- resolving to
    whichever was inserted last is the silent-wrong-answer failure."""
    for shared in ("City", "Man"):
        code, basis = resolver.lookup(shared)
        assert code is None, f"{shared!r} resolved to a single club ({basis})"


def test_every_club_resolves_from_its_own_name_and_short_name(resolver):
    """A resolver that cannot round-trip its own inputs is broken regardless of
    how it handles damaged ones."""
    for club in CLUBS:
        for label in (club["name"], club["short_name"]):
            code, basis = resolver.lookup(label)
            assert code == club["team_code"], (
                f"{label!r} -> {code} via {basis}, expected {club['team_code']}")
