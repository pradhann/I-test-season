"""Parsing messy, one-handed, half-watching-the-highlights input.

Every case here is a message a person would actually send. The ones that matter
most are the negatives: the parser refusing to name a player it cannot determine.
A parser that always returns an answer is worse than one that sometimes asks,
because the wrong answer is invisible and ends up in the bias analysis.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.interfaces.parsing import (
    MessageParser,
    PlayerResolver,
    classify,
    extract_gw,
    interpret_reply,
    name_fragments,
)
from fpl_edge.interfaces.features import player_universe
from fpl_edge.interfaces.ideas import IdeaKind
from fpl_edge.interfaces.testing import SEASON, seed_warehouse

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 8, 18, 22, 50, tzinfo=UTC)


@pytest.fixture(scope="module")
def resolver(tmp_path_factory) -> PlayerResolver:
    wh = seed_warehouse(tmp_path_factory.mktemp("parse") / "w.duckdb")
    return PlayerResolver(player_universe(wh.snapshot_at(NOW), SEASON))


@pytest.fixture()
def parser(resolver: PlayerResolver) -> MessageParser:
    return MessageParser(resolver, default_gw=1)


# -- resolving a name --------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expect"),
    [
        ("Semenyo", "Semenyo"),
        ("semenyo", "Semenyo"),
        ("SEMENYO", "Semenyo"),
        ("Rashford", "Rashford"),
        ("rashfrod", "Rashford"),          # transposition
        ("rashfor", "Rashford"),           # truncation
        ("antoine semenyo", "Semenyo"),    # full name
        ("haaland", "Haaland"),
        ("haalnd", "Haaland"),             # dropped letter
        ("odegaard", "Odegaard"),          # phone keyboard has no Ø
        ("Ødegaard", "Odegaard"),
        ("oedegaard", "Odegaard"),         # the other common transliteration
        ("van dijk", "Virgil"),            # web_name is "Virgil", not the surname
        ("vvd", "Virgil"),                 # nickname with no shared letters
        ("b.fernandes", "B.Fernandes"),    # initial disambiguates two Fernandes
        ("bruno", "B.Fernandes"),
        ("watkins", "Watkins"),
    ],
)
def test_resolves_messy_names(resolver: PlayerResolver, query: str, expect: str) -> None:
    res = resolver.resolve(query)
    assert not res.ambiguous, f"{query!r} was unexpectedly ambiguous"
    assert res.best is not None, f"{query!r} did not resolve at all"
    assert res.best.label.startswith(expect)


def test_ambiguous_surname_asks_rather_than_guessing(resolver: PlayerResolver) -> None:
    """Two real Palmers. Ownership would break the tie; it must not be allowed to.

    Cole Palmer is twice as owned as Alex Palmer, so a "sensible" tiebreak gets
    this right most of the time and silently wrong the rest. Silently wrong is
    the failure this whole design is built to avoid.
    """
    res = resolver.resolve("palmer")
    assert res.ambiguous
    assert res.best is None
    labels = [c.label for c in res.candidates]
    assert any("Cole" in lbl for lbl in labels)
    assert any("Alex" in lbl for lbl in labels)
    # Ordered for display by ownership, which is fine -- it just has no vote.
    assert "Cole" in labels[0]


def test_near_miss_is_not_a_match(resolver: PlayerResolver) -> None:
    """"salah" must not become "Salahuddin", and "kdb" must not become anyone.

    Both cleared a plain difflib ratio threshold during development. An absolute
    edit-distance gate is what separates a typo from a different person.
    """
    for query in ("salah", "kdb", "trent"):
        res = resolver.resolve(query)
        assert res.best is None, f"{query!r} wrongly resolved to {res.candidates[:1]}"
        assert not res.matched


def test_suggestions_are_offered_but_never_accepted(resolver: PlayerResolver) -> None:
    res = resolver.resolve("kdb")
    assert not res.matched
    assert res.best is None  # suggestions exist but `best` still refuses


# -- gameweeks ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "gw"),
    [
        ("Semenyo captain GW12?", 12),
        ("semenyo captain gw 12", 12),
        ("semenyo gameweek 12", 12),
        ("semenyo game week 12", 12),
        ("semenyo week 12", 12),
        ("semenyo gw-12", 12),
        ("I like Rashford", None),
        ("Semenyo 7.5", None),          # a price, not a gameweek
        ("3 at the back", None),        # a formation, not a gameweek
        ("gw 99", None),                # out of range
    ],
)
def test_extract_gw(text: str, gw: int | None) -> None:
    assert extract_gw(text)[0] == gw


# -- intent ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("I like Rashford", IdeaKind.TRANSFER_IN),
        ("Semenyo captain GW12?", IdeaKind.CAPTAIN),
        ("give semenyo the armband", IdeaKind.CAPTAIN),
        ("sell Wood", IdeaKind.FADE),
        ("avoid Wood", IdeaKind.FADE),
        ("Semenyo is a nice differential", IdeaKind.DIFFERENTIAL),
        ("Odegaard or B.Fernandes", IdeaKind.COMPARE),
        ("Odegaard or B.Fernandes for the armband", IdeaKind.CAPTAIN),
        ("Semenyo", IdeaKind.WATCH),
    ],
)
def test_classify(text: str, kind: IdeaKind) -> None:
    assert classify(text) == kind


# -- fragments ---------------------------------------------------------------


def test_fragments_are_in_the_order_they_were_typed() -> None:
    """Subject is whoever was named first, not whoever has the longer name.

    Sorting fragments by length globally -- the obvious implementation -- swaps
    the two players in "Odegaard or B.Fernandes" and produces an idea claiming
    the exact opposite of what was asked.
    """
    groups = name_fragments("Odegaard or B.Fernandes for the armband")
    assert groups[0][0] == "odegaard"
    assert groups[1][0] == "b fernandes"


def test_fragments_offer_the_specific_span_before_the_bare_surname() -> None:
    groups = name_fragments("antoine semenyo captain")
    assert groups[0][0] == "antoine semenyo"
    assert "semenyo" in groups[0]


# -- whole messages ----------------------------------------------------------


def test_comparison_keeps_the_order_of_mention(parser: MessageParser) -> None:
    parsed = parser.parse("Odegaard or B.Fernandes for the armband")
    assert parsed.kind is IdeaKind.CAPTAIN
    assert parsed.subject is not None and parsed.subject.best is not None
    assert parsed.rival is not None and parsed.rival.best is not None
    assert parsed.subject.best.label.startswith("Odegaard")
    assert parsed.rival.best.label.startswith("B.Fernandes")


def test_one_run_of_text_is_not_read_as_two_players(parser: MessageParser) -> None:
    """"B.Fernandes" is one person, even though "fernandes" also resolves."""
    parsed = parser.parse("thinking about B.Fernandes")
    assert parsed.rival is None


def test_message_with_no_player_yields_no_subject(parser: MessageParser) -> None:
    parsed = parser.parse("what do you reckon then")
    assert parsed.subject is None or parsed.subject.best is None


# -- clarification replies ---------------------------------------------------


def test_interpret_reply_accepts_ordinal_and_name(resolver: PlayerResolver) -> None:
    cands = resolver.resolve("palmer").candidates
    assert interpret_reply("2", cands) is cands[1]
    assert interpret_reply(" 1 ", cands) is cands[0]
    chosen = interpret_reply("Alex Palmer", cands)
    assert chosen is not None and "Alex" in chosen.label


def test_interpret_reply_rejects_a_new_thought(resolver: PlayerResolver) -> None:
    """A user who ignores the question must not have their new idea eaten by it."""
    cands = resolver.resolve("palmer").candidates
    assert interpret_reply("actually captain Haaland", cands) is None
    assert interpret_reply("9", cands) is None      # out of range ordinal
    assert interpret_reply("", cands) is None
