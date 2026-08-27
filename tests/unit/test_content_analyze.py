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
    """Haaland/Bruno resolve; 'Pascal Gross' is ambiguous on purpose.

    Carries `lookup` as well as `find_mentions` because every real resolver
    does, and because a structured name is resolved by exact alias first --
    see tests/unit/test_analysis_name_resolution.py for why scanning a name
    like prose once resolved "Martin Odegaard" to David Raya Martin.
    """

    _EXACT = {"erling haaland": (223094, "ok"),
              "bruno fernandes": (141746, "ok")}

    def lookup(self, name):
        return self._EXACT.get(str(name).lower(), (None, "unknown"))

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


# ---------------------------------------------------------------------------
# Thin sources: show notes must not masquerade as a considered take
# ---------------------------------------------------------------------------


def test_show_notes_prompt_says_it_is_not_a_transcript() -> None:
    """A description is 1.2KB of sponsor copy; the model has to be told so.

    Handed marketing copy under a heading that reads "Transcript:", a
    cooperative model manufactures a take out of the title. The preamble is
    the only thing standing between "MY GW2 TEAM 🔥" and a fabricated
    high-conviction call.
    """
    fake = FakeClient(sample_analysis())
    analyze_transcript(title="MY FPL GW2 TEAM", creator="FPL Focal",
                       text="Get premium https://x/y League code: abc",
                       text_source="description", client=fake)
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "NOT A TRANSCRIPT" in sent
    assert "Empty lists are the CORRECT" in sent
    assert "never convert a headline or a question into a call" in sent


def test_transcript_prompt_carries_no_show_notes_preamble() -> None:
    """The caveat is for thin sources only; a transcript is the thing itself."""
    fake = FakeClient(sample_analysis())
    analyze_transcript(title="Ep 1", creator="The FPL Wire", text="hours of speech",
                       text_source="transcript", client=fake)
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "NOT A TRANSCRIPT" not in sent
    assert "Transcript:" in sent


def test_depth_and_scoreability_follow_the_text_source() -> None:
    assert analyze.depth_for("description") == "notes"
    assert analyze.depth_for("transcript") == "transcript"
    assert analyze.depth_for("article") == "article"
    assert analyze.depth_for(None) == "unknown"
    assert analyze.is_thin("description") and not analyze.is_thin("transcript")
    # Show-note calls never reach content_claim: a conviction band read off
    # promotional copy would decalibrate the very channel it feeds.
    assert not analyze.is_scoreable("description")
    assert analyze.is_scoreable("transcript") and analyze.is_scoreable("article")


def test_substantive_text_discounts_links_and_separator_furniture() -> None:
    notes = ("MY GW2 TEAM! Get premium - https://fpl.page/subscribe "
             "━━━━━ Join: www.example.com/join")
    assert analyze.substantive_text(notes) == "MY GW2 TEAM! Get premium - Join:"
    assert len(analyze.substantive_text(notes)) < analyze.MIN_SUBSTANTIVE_CHARS
    assert analyze.substantive_text(None) == ""


def test_stored_analysis_carries_the_depth_of_what_it_read() -> None:
    """The panel renders a notes-summary beside a transcript-summary.

    ``text_source`` travels INSIDE analysis_json so a reader holding the take
    holds the caveat too, with no join back to content_item.
    """
    import json as _json

    written = []

    class _Wh:
        def sql(self, sql, binds):
            written.append(binds)

    store_analysis(_Wh(), "item_1", sample_analysis(),
                   text_source="description", chars=1266, substantive_chars=300)
    payload = _json.loads(written[0][3])
    ev = payload[analyze.EVIDENCE_KEY]
    assert ev == {"text_source": "description", "depth": "notes", "thin": True,
                  "scoreable": False, "chars": 1266, "substantive_chars": 300}
    # The analysis itself is untouched by the stamping.
    assert payload["captaincy"][0]["player"] == "Bruno Fernandes"


def test_evidence_is_an_extra_key_that_load_analysis_ignores() -> None:
    """Stamping must not break the round trip, nor the model's own shape."""
    import json as _json

    payload = sample_analysis().model_dump()
    payload[analyze.EVIDENCE_KEY] = analyze.evidence_block(text_source="article")

    class _Wh:
        def sql(self, sql, binds):
            import pandas as pd
            return pd.DataFrame({"analysis_json": [_json.dumps(payload)]})

    got = analyze.load_analysis(_Wh(), "item_1")
    assert got is not None and got.captaincy[0].player == "Bruno Fernandes"
    assert analyze.load_evidence(_Wh(), "item_1")["depth"] == "article"


def test_store_analysis_without_a_text_source_stamps_nothing() -> None:
    """Absent evidence is left absent, never defaulted to 'transcript'."""
    import json as _json

    written = []

    class _Wh:
        def sql(self, sql, binds):
            written.append(binds)

    store_analysis(_Wh(), "item_1", sample_analysis())
    assert analyze.EVIDENCE_KEY not in _json.loads(written[0][3])


def test_a_barren_read_is_recognised_rather_than_stored_as_a_take() -> None:
    barren = TranscriptAnalysis(summary=[], transfers_in=[], transfers_out=[],
                                captaincy=[], chip_advice=[], differentials=[])
    assert analyze.analysis_is_empty(barren)
    assert not analyze.analysis_is_empty(sample_analysis())
    # A summary alone still counts: the episode was read and said something.
    assert not analyze.analysis_is_empty(
        TranscriptAnalysis(summary=["one idea"], transfers_in=[], transfers_out=[],
                           captaincy=[], chip_advice=[], differentials=[])
    )


# ---------------------------------------------------------------------------
# The bulk step's queue: what a truncated run actually covers
# ---------------------------------------------------------------------------


def _items(rows):
    import pandas as pd

    return pd.DataFrame(
        [{"item_id": i, "creator": c, "text_source": ts,
          "published_at": dt.datetime(2026, 8, d, tzinfo=UTC), "text": txt}
         for i, c, ts, d, txt in rows]
    )


def test_queue_serves_every_creator_before_anyones_second_item() -> None:
    """A budget-limited run is the normal case, so coverage comes first.

    The Creators tab needs *a* take from each of 23 creators far more than it
    needs four takes from the one who publishes daily. If this ordering ever
    reverts to plain recency, a 20-item run spends itself on Scout's blog and
    the tab shows 21 empty states.
    """
    from fpl_edge.ingest.content.pipeline import rank_candidates

    body = "x" * 5000
    ranked = rank_candidates(_items([
        ("a1", "Daily Blog", "article", 27, body),
        ("a2", "Daily Blog", "article", 26, body),
        ("a3", "Daily Blog", "article", 25, body),
        ("b1", "Weekly Pod", "description", 20, body),
        ("c1", "Rare Pod", "description", 19, body),
    ]))
    first_three = list(ranked["creator"].head(3))
    assert sorted(first_three) == ["Daily Blog", "Rare Pod", "Weekly Pod"]
    assert list(ranked["item_id"])[:1] == ["a1"]  # deepest + freshest leads
    assert list(ranked["item_id"])[-2:] == ["a2", "a3"]


def test_queue_prefers_a_transcript_over_fresher_show_notes() -> None:
    """A 1.2KB blurb published today is worth less than an hour of speech."""
    from fpl_edge.ingest.content.pipeline import rank_candidates

    ranked = rank_candidates(_items([
        ("notes_today", "Pod", "description", 27, "y" * 1200),
        ("tape_last_week", "Pod", "transcript", 20, "y" * 60000),
    ]))
    assert list(ranked["item_id"]) == ["tape_last_week", "notes_today"]
    assert list(ranked["depth"]) == ["transcript", "notes"]


def test_queue_measures_prose_not_raw_length() -> None:
    """A 1.2KB description that is all affiliate links has nothing in it."""
    from fpl_edge.ingest.content.pipeline import rank_candidates

    link_farm = ("MY GW2 TEAM! " + "https://fpl.page/subscribe " * 40)
    assert len(link_farm) > 1000
    ranked = rank_candidates(_items([("x", "Pod", "description", 27, link_farm)]))
    assert int(ranked.iloc[0]["substantive_chars"]) < analyze.MIN_SUBSTANTIVE_CHARS


def test_ranking_an_empty_queue_does_not_explode() -> None:
    import pandas as pd

    from fpl_edge.ingest.content.pipeline import rank_candidates

    empty = pd.DataFrame(columns=["item_id", "creator", "text_source",
                                  "published_at", "text"])
    assert rank_candidates(empty).empty


# ---------------------------------------------------------------------------
# Creator identity: link what is verified, guess nothing
# ---------------------------------------------------------------------------


class _IdentityWh:
    """dim_manager + content_source, in the two shapes the matcher asks for."""

    def __init__(self, managers, creators):
        self.managers = managers
        self.creators = creators

    def sql(self, sql, params=()):
        import pandas as pd

        if "dim_manager" in sql:
            allowed = set(params)
            return pd.DataFrame(
                [m for m in self.managers if m["source"].split(":")[0] in allowed],
                columns=["entry_id", "player_name", "entry_name", "source"],
            )
        return pd.DataFrame({"creator": self.creators})


def test_an_exact_api_reported_name_links() -> None:
    from fpl_edge.interfaces.creators import link_creator_entries

    wh = _IdentityWh(
        [{"entry_id": 53517, "player_name": "Ben Crellin",
          "entry_name": "Crellin FC", "source": "elite_named"}],
        ["Ben Crellin"],
    )
    (link,) = link_creator_entries(wh)
    assert link.entry_id == 53517
    assert link.verified and "elite_named" in link.method


def test_a_channel_name_that_merely_resembles_a_person_never_links() -> None:
    """"Let's Talk FPL" is Andy's channel. That is a fact about the world,
    not evidence in this warehouse, and the matcher must not supply it."""
    from fpl_edge.interfaces.creators import link_creator_entries

    wh = _IdentityWh(
        [{"entry_id": 41, "player_name": "Andy LTFPL",
          "entry_name": "LTFPL", "source": "elite_named"}],
        ["Let's Talk FPL", "FPL Harry", "Andy"],
    )
    links = {x.creator: x for x in link_creator_entries(wh)}
    assert all(x.entry_id is None for x in links.values())
    assert "single token" in links["Andy"].reason
    assert "no FPL entry" in links["Let's Talk FPL"].reason


def test_a_curated_but_unverified_name_is_not_evidence() -> None:
    """elite_list and winner names sit next to IDs nobody re-checked.

    FPL reassigns entry IDs every August, so a pinned third-party name is a
    claim about last season, not an identity.
    """
    from fpl_edge.interfaces.creators import link_creator_entries

    wh = _IdentityWh(
        [{"entry_id": 999, "player_name": "Gianni Buttice",
          "entry_name": "GB", "source": "elite_list"}],
        ["Gianni Buttice"],
    )
    (link,) = link_creator_entries(wh)
    assert link.entry_id is None
    assert "no FPL entry whose API-reported name" in link.reason


def test_a_name_two_entries_share_is_ambiguous_not_a_coin_flip() -> None:
    from fpl_edge.interfaces.creators import link_creator_entries

    wh = _IdentityWh(
        [{"entry_id": 1, "player_name": "James Smith", "entry_name": "A",
          "source": "top1k:2026-27:gw1:rank5"},
         {"entry_id": 2, "player_name": "James Smith", "entry_name": "B",
          "source": "mini_league:76109"}],
        ["James Smith"],
    )
    (link,) = link_creator_entries(wh)
    assert link.entry_id is None
    assert "ambiguous: 2 entries" in link.reason
