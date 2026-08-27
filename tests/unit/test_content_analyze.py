"""Semantic transcript analysis: schema, claim conversion, honest fallbacks.

No network anywhere: the Claude client is faked at the parse() seam, which is
the exact surface the real SDK exposes.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.ingest.content import analyze
from fpl_edge.ingest.content.analyze import (
    CONVICTION_CONF,
    AnalysisUnavailable,
    ChipCall,
    PlayerCall,
    TranscriptAnalysis,
    analyze_transcript,
    claims_from_analysis,
    store_analysis,
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


def _no_backends(monkeypatch, tmp_path) -> None:
    """Both backends unavailable, on any machine.

    Deleting the env var and chdir-ing away from .env only closes the SDK
    door. The CLI door is opened by ``_find_claude_cli``, which looks at the
    OPERATOR'S filesystem -- so on a laptop with Claude Code installed the
    "no backend" test used to shell out to a live CLI and get a real answer,
    passing on CI and failing here. The backend probe is a seam; close it at
    the seam so the test asserts the same thing everywhere.
    """
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(analyze, "_find_claude_cli", lambda: None)
    # Nothing may reach the network: if the code ever gets as far as building
    # an SDK client, that is the bug this test exists to catch.
    monkeypatch.setattr(analyze, "_analyze_via_cli", _must_not_run)


def _must_not_run(*args, **kwargs):
    raise AssertionError("no backend should have been invoked")


def test_no_api_key_raises_unavailable_not_a_fake_answer(monkeypatch, tmp_path) -> None:
    """With NO backend, the answer is an error -- never an invented analysis."""
    _no_backends(monkeypatch, tmp_path)
    with pytest.raises(AnalysisUnavailable, match="ANTHROPIC_API_KEY"):
        analyze_transcript(title="t", creator="c", text="x")


def test_no_backend_message_names_both_missing_doors(monkeypatch, tmp_path) -> None:
    """The error says WHY, so the caller can say so rather than guess."""
    _no_backends(monkeypatch, tmp_path)
    with pytest.raises(AnalysisUnavailable) as exc:
        analyze_transcript(title="t", creator="c", text="x")
    assert "No Claude Code CLI found" in str(exc.value)
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_broken_cli_without_key_still_refuses_rather_than_falling_through(
    monkeypatch, tmp_path,
) -> None:
    """A CLI that exists but fails is not a backend: no key, no answer.

    The CLI's own reason is carried forward -- a revoked login must not read
    as "nothing installed".
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(analyze, "_find_claude_cli", lambda: "/nowhere/claude")

    def _broken(*args, **kwargs):
        raise AnalysisUnavailable("the Claude Code CLI login is revoked")

    monkeypatch.setattr(analyze, "_analyze_via_cli", _broken)
    with pytest.raises(AnalysisUnavailable) as exc:
        analyze_transcript(title="t", creator="c", text="x")
    assert "revoked" in str(exc.value)
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_store_analysis_refuses_a_model_string_that_is_not_a_model_id() -> None:
    """(item_id, model) is a primary key; a backend label silently breaks it.

    The live row stamped ``max-plan:claude-fable-5-session`` keyed differently
    from the real model id, so a later genuine run did not dedupe against it.
    """
    class _Wh:
        def sql(self, *a, **k):  # pragma: no cover - must never be reached
            raise AssertionError("a bogus model id must not reach the warehouse")

    for bogus in ("max-plan:claude-fable-5-session", "", "opus", "Claude-Opus-5",
                  "claude-opus-5 (max plan)"):
        with pytest.raises(ValueError, match="model id"):
            store_analysis(_Wh(), "item_1", sample_analysis(), model=bogus)


def test_store_analysis_accepts_real_model_ids() -> None:
    """The validator must not be so tight that a real id is rejected."""
    for good in ("claude-opus-5", "claude-sonnet-4-6", "claude-opus-4-5-20251101"):
        assert analyze.validate_model_id(good) == good
    assert analyze.validate_model_id(analyze.MODEL) == analyze.MODEL

    written = []

    class _Wh:
        def sql(self, sql, binds):
            written.append(binds)

    store_analysis(_Wh(), "item_1", sample_analysis())
    assert written[0][1] == analyze.MODEL


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
