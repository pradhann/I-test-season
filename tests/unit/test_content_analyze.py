"""Semantic transcript analysis: schema, claim conversion, honest fallbacks.

No network anywhere: the Claude client is faked at the parse() seam, which is
the exact surface the real SDK exposes.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.ingest.content.analyze import (
    CONVICTION_CONF,
    AnalysisUnavailable,
    ChipCall,
    PlayerCall,
    TranscriptAnalysis,
    analyze_transcript,
    claims_from_analysis,
)

UTC = dt.timezone.utc


def sample_analysis() -> TranscriptAnalysis:
    return TranscriptAnalysis(
        summary=["Bruno is the standout captain pick",
                 "Sell Gross before his price drops"],
        transfers_in=[PlayerCall(player="Erling Haaland", stance="buy",
                                 conviction="high", gameweek=1,
                                 reasoning="Best fixture on the slate",
                                 quote="Haaland is just nailed for me")],
        transfers_out=[PlayerCall(player="Pascal Gross", stance="sell",
                                  conviction="medium", gameweek=None,
                                  reasoning="Lost set pieces",
                                  quote="I think Gross has to go")],
        captaincy=[PlayerCall(player="Bruno Fernandes", stance="captain",
                              conviction="high", gameweek=1,
                              reasoning="Penalties plus form",
                              quote="Bruno captain, lock it in")],
        chip_advice=[ChipCall(chip="bench_boost", stance="hold", gameweek=None,
                              reasoning="Save for a double",
                              quote="I'd never boost in week one")],
        differentials=[],
    )


class FakeParsed:
    def __init__(self, analysis):
        self.parsed_output = analysis


class FakeMessages:
    def __init__(self, analysis):
        self._a = analysis
        self.calls = []

    def parse(self, **kw):
        self.calls.append(kw)
        return FakeParsed(self._a)


class FakeClient:
    def __init__(self, analysis):
        self.messages = FakeMessages(analysis)


def test_analysis_carries_the_transcript_and_schema() -> None:
    fake = FakeClient(sample_analysis())
    got = analyze_transcript(title="Ep 1", creator="The FPL Wire",
                             text="long transcript text", client=fake)
    assert got.captaincy[0].player == "Bruno Fernandes"
    call = fake.messages.calls[0]
    assert call["output_format"] is TranscriptAnalysis
    assert "long transcript text" in call["messages"][0]["content"]
    assert "verbatim" in call["system"] or "actually take" in call["system"]


def test_no_api_key_raises_unavailable_not_a_fake_answer(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AnalysisUnavailable, match="ANTHROPIC_API_KEY"):
        analyze_transcript(title="t", creator="c", text="x")


class _Mention:
    def __init__(self, code):
        self.code = code


class _Resolver:
    """Haaland/Bruno resolve; 'Pascal Gross' is ambiguous on purpose."""

    def find_mentions(self, text, stats):
        table = {"erling haaland": [_Mention(223094)],
                 "bruno fernandes": [_Mention(141746)],
                 "pascal gross": [_Mention(1), _Mention(2)]}
        return table.get(text.lower(), [])


class _Item:
    item_id = "link_test"
    creator = "user-shared"
    source_key = "user_link"
    url = "https://youtu.be/x"
    published_at = dt.datetime(2026, 8, 19, tzinfo=UTC)


def test_claims_carry_conviction_bands_and_llm_extractor() -> None:
    claims, dropped = claims_from_analysis(
        sample_analysis(), item=_Item(), resolver=_Resolver(),
        default_gw=1, season="2026-27",
    )
    by_player = {c.player_name: c for c in claims}
    assert by_player["Erling Haaland"].confidence == CONVICTION_CONF["high"] == 0.8
    assert by_player["Erling Haaland"].extractor.startswith("llm:")
    assert "quote:" in by_player["Erling Haaland"].rationale
    # The ambiguous name is DROPPED and reported, never guessed.
    assert dropped == ["Pascal Gross"]
    assert "Pascal Gross" not in by_player
    # Bruno's captain call resolves with its stated gameweek.
    assert by_player["Bruno Fernandes"].action.value == "captain"
    assert int(by_player["Bruno Fernandes"].gameweek) == 1
