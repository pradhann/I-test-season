"""Extraction, name resolution and feed parsing, against fixtures on disk.

Nothing here touches the network. The fixtures under ``tests/fixtures/content/``
are trimmed captures of the real feeds -- an actual Megaphone RSS item with its
``-0000`` timestamp, real FPL video titles -- so a regression in the parser shows
up as a test failure rather than as a quietly smaller corpus.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest.content.claims import (
    ExtractionStats,
    GameweekCalendar,
    extract_from_item,
    segment,
)
from fpl_edge.ingest.content.feeds import UnparsableDate, parse_feed, parse_feed_date, strip_html
from fpl_edge.ingest.content.fetch import Response
from fpl_edge.ingest.content.loaders import load_feed_source, load_source
from fpl_edge.ingest.content.models import Action, ContentItem
from fpl_edge.ingest.content.resolve import PlayerResolver, ResolutionStats, SeasonResolvers
from fpl_edge.ingest.content.sources import ProbeReport, Source, SourceKind

UTC = dt.UTC
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "content"

SEASON = "2026-27"
GW1_DEADLINE = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
GW2_DEADLINE = dt.datetime(2026, 8, 28, 17, 30, tzinfo=UTC)
PUBLISHED = dt.datetime(2026, 8, 19, 9, 0, tzinfo=UTC)

PLAYERS = pd.DataFrame([
    {"season": SEASON, "code": 223094, "web_name": "Haaland",
     "first_name": "Erling", "second_name": "Haaland", "position": 4},
    {"season": SEASON, "code": 118748, "web_name": "M.Salah",
     "first_name": "Mohamed", "second_name": "Salah", "position": 3},
    {"season": SEASON, "code": 244851, "web_name": "Palmer",
     "first_name": "Cole", "second_name": "Palmer", "position": 3},
    {"season": SEASON, "code": 178301, "web_name": "Watkins",
     "first_name": "Ollie", "second_name": "Watkins", "position": 4},
    # Two Wilsons in one season: the ambiguity case, on purpose.
    {"season": SEASON, "code": 100001, "web_name": "Wilson",
     "first_name": "Callum", "second_name": "Wilson", "position": 4},
    {"season": SEASON, "code": 100002, "web_name": "H.Wilson",
     "first_name": "Harry", "second_name": "Wilson", "position": 3},
    # Surname that is an ordinary English word.
    {"season": SEASON, "code": 204480, "web_name": "Rice",
     "first_name": "Declan", "second_name": "Rice", "position": 3},
])

CALENDAR = GameweekCalendar([(SEASON, 1, GW1_DEADLINE), (SEASON, 2, GW2_DEADLINE)])


@pytest.fixture
def resolver() -> PlayerResolver:
    from fpl_edge.ingest.content.resolve import resolver_for

    return resolver_for(PLAYERS)


def _item(text: str, *, title: str = "", published: dt.datetime = PUBLISHED,
          source: str = "description") -> ContentItem:
    return ContentItem(
        item_id="item-1", source_key="pod_test", creator="Test Creator",
        kind="podcast", title=title, url="https://example.invalid/ep1",
        published_at=published, text=f"{title}.\n{text}" if title else text,
        fetched_at=published, text_source=source,
    )


class TestResolution:
    def test_full_and_short_names_resolve(self, resolver: PlayerResolver) -> None:
        assert resolver.lookup("Erling Haaland")[0] == 223094
        assert resolver.lookup("haaland")[0] == 223094
        assert resolver.lookup("Mohamed Salah")[0] == 118748

    def test_a_shared_surname_is_refused_not_guessed(self, resolver: PlayerResolver) -> None:
        """Two Wilsons. Picking one welds two careers into one feature."""
        code, reason = resolver.lookup("Wilson")
        assert code is None
        assert reason == "ambiguous"

    def test_the_shared_surname_resolves_when_disambiguated(
        self, resolver: PlayerResolver
    ) -> None:
        assert resolver.lookup("Callum Wilson")[0] == 100001
        assert resolver.lookup("Harry Wilson")[0] == 100002

    def test_an_english_word_surname_is_refused_as_a_bare_token(
        self, resolver: PlayerResolver
    ) -> None:
        """'worth the price' must not become a Declan Rice mention."""
        stats = ResolutionStats()
        mentions = resolver.find_mentions("the rice is worth the price", stats)
        assert [m for m in mentions if m.code == 204480] == []
        assert stats.risky_refused == 1

    def test_the_same_surname_resolves_with_a_first_name(
        self, resolver: PlayerResolver
    ) -> None:
        stats = ResolutionStats()
        mentions = resolver.find_mentions("I am buying Declan Rice", stats)
        assert [m.code for m in mentions] == [204480]

    def test_longest_match_wins(self) -> None:
        """'Gabriel' must not swallow 'Gabriel Martinelli'."""
        from fpl_edge.ingest.content.resolve import resolver_for

        frame = pd.DataFrame([
            {"season": SEASON, "code": 1, "web_name": "Gabriel",
             "first_name": "Gabriel", "second_name": "Magalhaes", "position": 2},
            {"season": SEASON, "code": 2, "web_name": "Martinelli",
             "first_name": "Gabriel", "second_name": "Martinelli", "position": 3},
        ])
        local = resolver_for(frame)
        mentions = local.find_mentions("captaining gabriel martinelli this week")
        assert [m.code for m in mentions] == [2]

    def test_diacritics_fold(self) -> None:
        from fpl_edge.ingest.content.resolve import resolver_for

        frame = pd.DataFrame([{
            "season": SEASON, "code": 9, "web_name": "Gyökeres",
            "first_name": "Viktor", "second_name": "Gyökeres", "position": 4,
        }])
        assert resolver_for(frame).lookup("Gyokeres")[0] == 9

    def test_season_scoping_recovers_a_cross_season_ambiguity(self) -> None:
        """One Wilson per season is unambiguous; five seasons of them are not."""
        frame = pd.DataFrame([
            {"season": "2024-25", "code": 100001, "web_name": "Wilson",
             "first_name": "Callum", "second_name": "Wilson", "position": 4},
            {"season": "2025-26", "code": 100002, "web_name": "Wilson",
             "first_name": "Harry", "second_name": "Wilson", "position": 3},
        ])
        resolvers = SeasonResolvers(frame)
        assert resolvers.for_season("2024-25").lookup("Wilson")[0] == 100001
        assert resolvers.for_season("2025-26").lookup("Wilson")[0] == 100002
        assert resolvers.for_season(None).lookup("Wilson")[0] is None


class TestExtraction:
    def test_a_captain_claim_is_extracted_with_its_gameweek(
        self, resolver: PlayerResolver
    ) -> None:
        stats = ExtractionStats()
        claims = extract_from_item(
            _item("I am captaining Haaland.", title="GW1 Captain Picks"),
            resolver, CALENDAR, stats,
        )
        assert len(claims) == 1
        claim = claims[0]
        assert claim.action is Action.CAPTAIN
        assert int(claim.player_code) == 223094
        assert int(claim.gameweek) == 1
        assert claim.gw_inferred is False
        assert claim.published_at == PUBLISHED

    def test_negation_inverts_the_action(self, resolver: PlayerResolver) -> None:
        """Reading 'not bringing in Watkins' as a buy is worse than extracting nothing."""
        stats = ExtractionStats()
        claims = extract_from_item(
            _item("I am not bringing in Watkins this week.", title="GW2 Transfers"),
            resolver, CALENDAR, stats,
        )
        assert [c.action for c in claims] == [Action.AVOID]
        assert stats.negations_applied == 1

    def test_gameweek_is_inferred_from_publication_when_unstated(
        self, resolver: PlayerResolver
    ) -> None:
        stats = ExtractionStats()
        claims = extract_from_item(
            _item("Selling Salah.", title="Transfer Thoughts"), resolver, CALENDAR, stats
        )
        assert [int(c.gameweek) for c in claims] == [1]
        assert all(c.gw_inferred for c in claims)
        assert stats.claims_gw_inferred == 1

    def test_inference_moves_to_the_next_gameweek_after_the_deadline(
        self, resolver: PlayerResolver
    ) -> None:
        """Published after GW1 locks -> it can only be about GW2."""
        stats = ExtractionStats()
        claims = extract_from_item(
            _item("Selling Salah.", published=GW1_DEADLINE + dt.timedelta(hours=1)),
            resolver, CALENDAR, stats,
        )
        assert [int(c.gameweek) for c in claims] == [2]

    def test_a_cue_with_no_nearby_player_is_dropped(
        self, resolver: PlayerResolver
    ) -> None:
        stats = ExtractionStats()
        text = ("Haaland had a good pre-season and looked sharp in the friendly "
                "and the manager was pleased with the whole squad overall "
                "and separately we should talk about who to captain")
        claims = extract_from_item(_item(text, title="GW1"), resolver, CALENDAR, stats)
        assert stats.cues_unbound >= 1
        assert all(c.action is not Action.CAPTAIN for c in claims) or claims == []

    def test_triple_captain_beats_captain(self, resolver: PlayerResolver) -> None:
        stats = ExtractionStats()
        claims = extract_from_item(
            _item("Triple captain Haaland.", title="GW1 Chips"), resolver, CALENDAR, stats
        )
        assert [c.action for c in claims] == [Action.TRIPLE_CAPTAIN]

    def test_hedging_lowers_confidence_below_commitment(
        self, resolver: PlayerResolver
    ) -> None:
        stats = ExtractionStats()
        hedged = extract_from_item(
            _item("I might maybe possibly captain Haaland.", title="GW1"),
            resolver, CALENDAR, stats,
        )[0]
        firm = extract_from_item(
            _item("I am definitely absolutely captaining Haaland, locked in.", title="GW1"),
            resolver, CALENDAR, ExtractionStats(),
        )[0]
        assert hedged.confidence < firm.confidence

    def test_an_unresolvable_name_produces_no_claim(
        self, resolver: PlayerResolver
    ) -> None:
        """Two Wilsons: the claim is dropped loudly, not attributed to a coin flip."""
        stats = ExtractionStats()
        claims = extract_from_item(
            _item("Captaining Wilson this week.", title="GW1"), resolver, CALENDAR, stats
        )
        assert claims == []
        assert stats.resolution.ambiguous == 1

    def test_duplicate_cues_in_one_item_collapse(self, resolver: PlayerResolver) -> None:
        stats = ExtractionStats()
        claims = extract_from_item(
            _item("Captaining Haaland. Definitely captaining Haaland. Captain Haaland.",
                  title="GW1"),
            resolver, CALENDAR, stats,
        )
        assert len(claims) == 1

    def test_claim_ids_are_stable_across_runs(self, resolver: PlayerResolver) -> None:
        first = extract_from_item(
            _item("Captaining Haaland.", title="GW1"), resolver, CALENDAR, ExtractionStats()
        )
        second = extract_from_item(
            _item("Captaining Haaland.", title="GW1"), resolver, CALENDAR, ExtractionStats()
        )
        assert [c.claim_id for c in first] == [c.claim_id for c in second]

    def test_real_video_titles_yield_claims(self, resolver: PlayerResolver) -> None:
        """Titles are the densest claim text in the corpus; verify on real ones."""
        titles = [
            "MY GW1 CAPTAIN PICK IS HAALAND 🔥 FPL 2026/27",
            "WHY I'M SELLING SALAH FOR GAMEWEEK 2 | FPL Tips",
            "GW1 TRANSFER TIPS: BRINGING IN PALMER",
        ]
        actions = []
        for title in titles:
            claims = extract_from_item(
                _item("", title=title), resolver, CALENDAR, ExtractionStats()
            )
            actions.extend(c.action for c in claims)
        assert Action.CAPTAIN in actions
        assert Action.SELL in actions
        assert Action.BUY in actions

    def test_the_nearest_cue_wins_when_two_bind_to_one_player(
        self, resolver: PlayerResolver
    ) -> None:
        """A real false positive from the corpus, now a regression test.

        AllAboutFPL wrote "Haaland is a must-have to avoid early rank losses".
        Two cues -- "must have" (buy) and "avoid" -- both bound to the only
        player in the sentence, producing a buy AND an avoid claim about
        Haaland from one utterance. The avoid is simply wrong: "avoid" governs
        "rank losses", not the player. Contradictory claims from one sentence
        are worse than one imperfect claim -- they cancel in the consensus map
        and add a coin flip to the hit rate.
        """
        claims = extract_from_item(
            _item("Haaland is a must-have to avoid early rank losses.", title="GW1"),
            resolver, CALENDAR, ExtractionStats(),
        )
        assert [c.action for c in claims] == [Action.BUY]

    def test_two_players_in_one_sentence_keep_their_own_cues(
        self, resolver: PlayerResolver
    ) -> None:
        """Nearest-cue-wins is per player, not per segment."""
        claims = extract_from_item(
            _item("Captaining Haaland and selling Salah.", title="GW1"),
            resolver, CALENDAR, ExtractionStats(),
        )
        by_code = {int(c.player_code): c.action for c in claims}
        assert by_code == {223094: Action.CAPTAIN, 118748: Action.SELL}


class TestSegmentation:
    def test_transcript_windows_overlap(self) -> None:
        words = " ".join(f"w{i}" for i in range(120))
        chunks = segment(words, is_transcript=True)
        assert len(chunks) > 1
        assert chunks[0].split()[20] == chunks[1].split()[0]

    def test_prose_splits_on_sentences(self) -> None:
        chunks = segment("Captaining Haaland. Selling Salah. Holding Palmer.",
                         is_transcript=False)
        assert len(chunks) == 3


class TestFeedParsing:
    def test_megaphone_minus_zero_offset_is_utc_not_naive(self) -> None:
        """RFC 5322 says -0000 is UTC-with-unknown-local. Python returns naive.

        Treating that as "no offset" rejected 777 of 777 Let's Talk FPL episodes,
        and every other Megaphone-hosted feed in the registry, before this was
        handled. Regression-guarded because the failure was silent: the feed
        returned 200, parsed, and produced zero items.
        """
        parsed = parse_feed_date("Tue, 18 Aug 2026 08:20:00 -0000")
        assert parsed == dt.datetime(2026, 8, 18, 8, 20, tzinfo=UTC)

    def test_a_date_with_no_offset_at_all_is_still_refused(self) -> None:
        with pytest.raises(UnparsableDate):
            parse_feed_date("Tue, 18 Aug 2026 08:20:00")

    def test_iso_and_rfc822_both_parse(self) -> None:
        assert parse_feed_date("2026-08-18T08:20:00Z").hour == 8
        assert parse_feed_date("Tue, 18 Aug 2026 09:20:00 +0100").hour == 8

    def test_fixture_feed_parses(self) -> None:
        entries, dropped = parse_feed((FIXTURES / "podcast_feed.xml").read_bytes())
        assert len(entries) == 3
        assert dropped == 1, "the deliberately dateless item should be dropped"
        assert entries[0].published_at.tzinfo is not None
        assert "GW1" in entries[0].title

    def test_block_tags_become_sentence_breaks(self) -> None:
        """Without the break, two bullets fuse into a phrase nobody said."""
        text = strip_html("<li>Captaining Haaland</li><li>Salah is out</li>")
        assert "Haaland. Salah" in text.replace("\n", " ").replace("  ", " ")


class _CannedFetcher:
    """A ContentFetcher stand-in that answers one body and records nothing."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def get(self, url: str) -> Response:
        return Response(
            url=url, status=200, body=self._body, fetched_at=PUBLISHED,
            sha256="", body_path=None,
        )


class TestLoaderReceipt:
    """What the ingest receipt is obliged to tell you about a fetch."""

    def test_entries_dropped_for_a_bad_date_reach_the_receipt(self) -> None:
        """A silent drop here is indistinguishable from an empty feed.

        feeds.py is right to refuse to guess a missing offset -- a day of drift
        moves a claim across a deadline. But the loader used to compute the
        count and throw it away, so a feed that lost every item to unparsable
        dates reported HTTP 200, no error, and zero items: exactly what a
        healthy but quiet feed looks like. The number is the only thing that
        distinguishes them.
        """
        source = Source("pod_test", "Creator", SourceKind.PODCAST,
                        "https://example.invalid/feed")
        items, result = load_feed_source(
            _CannedFetcher((FIXTURES / "podcast_feed.xml").read_bytes()), source
        )

        assert len(items) == 3
        assert result.bad_dates == 1, (
            "the dateless entry was dropped without appearing anywhere in the receipt"
        )

        report = ProbeReport()
        report.add(result)
        assert report.bad_dates == 1
        assert "1" in report.render().splitlines()[-1]

    def test_load_source_refuses_a_keyword_it_cannot_honour(self) -> None:
        """The `--no-transcripts` class of bug, closed at the signature.

        load_source used to take **kwargs and forward only the names it knew,
        so `transcripts=False` -- passed by both callers and advertised in
        --help -- was accepted and dropped. A flag that silently does nothing
        is worse than a missing flag, because the operator believes it worked.
        """
        source = Source("pod_test", "Creator", SourceKind.PODCAST,
                        "https://example.invalid/feed")
        with pytest.raises(TypeError):
            load_source(_CannedFetcher(b""), source, transcripts=False)
