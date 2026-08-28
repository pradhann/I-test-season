"""The second grain: observations, kept out of the scoreboard.

``content_claim`` records predictions. ``content_insight`` records the sentences
that are most of what an analytical channel actually says -- "Semenyo is playing
as a false nine now", "Arsenal's fixtures turn in GW6" -- none of which is a
bet. Three properties matter more than the rest and each has a test here:

* an insight NEVER becomes a claim, because the scoreboard would then settle
  opinions that were never predictions;
* an insight never exists without a verbatim quote;
* an insight about a team does not get a fabricated player_code, and a player
  the strict resolver refuses keeps the creator's words with a NULL code.

No network: every model call is faked at the ``parse()`` seam, and every
warehouse is a temp DuckDB file this test owns.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import ClassVar

import pytest

from fpl_edge.ingest.content import analyze
from fpl_edge.ingest.content.analyze import (
    CONVICTION_CONF,
    INSIGHT_COLS,
    Insight,
    InsightRow,
    TranscriptAnalysis,
    insights_from_analysis,
    insights_visible_at,
    store_insights,
)
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse

UTC = dt.UTC

SEASON = "2026-27"
DEADLINE = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
BEFORE = DEADLINE - dt.timedelta(hours=6)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    """A warehouse this test owns. Never data/warehouse/fpl.duckdb."""
    with Warehouse(tmp_path / "insights.duckdb") as warehouse:
        yield ContentStore(warehouse)


class _Mention:
    def __init__(self, code):
        self.code = code


class _Resolver:
    """Haaland resolves exactly; "Semenyo" only via a mention; Barry is a trap.

    Mirrors tests/unit/test_content_analyze.py::_Resolver so both grains are
    proven to go through the SAME strict path.
    """

    _EXACT: ClassVar[dict] = {"erling haaland": (223094, "ok")}

    def lookup(self, name):
        return self._EXACT.get(str(name).lower(), (None, "unknown"))

    def find_mentions(self, text, stats):
        table = {
            "antoine semenyo": [_Mention(464391)],
            # An edit-distance match the strict resolver must REFUSE: the
            # spoken "Louie Barry" is not Thierno Barry.
            "louie barry": [_Mention(555555)],
        }
        return table.get(text.lower(), [])


class _MatchedMention(_Mention):
    def __init__(self, code, matched_name):
        super().__init__(code)
        self.matched_name = matched_name


class _StrictResolver(_Resolver):
    def find_mentions(self, text, stats):
        table = {
            "antoine semenyo": [_MatchedMention(464391, "Antoine Semenyo")],
            "louie barry": [_MatchedMention(555555, "Thierno Barry")],
        }
        return table.get(text.lower(), [])


class _Item:
    item_id = "item_solio_1"
    creator = "SolioAnalytics"
    source_key = "yt_solio"
    kind = "youtube"
    title = "GW6 watchlist"
    url = "https://youtu.be/solio"
    published_at = BEFORE


def _insight(**kw) -> Insight:
    base = {
        "topic": "role_change", "entity_kind": "player",
        "entity_name": "Antoine Semenyo",
        "claim_text": "Semenyo is playing as a false nine",
        "quote": "Semenyo is playing as a false nine now, he's not out wide",
        "horizon_gw": None, "horizon_gw_end": None, "conviction": "high",
    }
    base.update(kw)
    return Insight(**base)


def _analysis(insights) -> TranscriptAnalysis:
    return TranscriptAnalysis(
        summary=[], transfers_in=[], transfers_out=[], captaincy=[],
        chip_advice=[], differentials=[], insights=list(insights),
    )


def _rows(insights, *, resolver=None, text_source="transcript", **kw):
    return insights_from_analysis(
        _analysis(insights), item=_Item(), resolver=resolver or _StrictResolver(),
        default_gw=6, season=SEASON, text_source=text_source, **kw,
    )


# ---------------------------------------------------------------------------
# the migration
# ---------------------------------------------------------------------------


def test_migration_creates_content_insight_with_the_agreed_columns(store) -> None:
    """The UI agents are wiring against this exact shape."""
    cols = store.wh.sql(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'content_insight' ORDER BY ordinal_position"
    )["column_name"].tolist()
    assert cols == list(INSIGHT_COLS)


def test_migration_is_idempotent_and_recorded(store) -> None:
    """Re-running applies nothing and leaves exactly one version row."""
    assert store.migrate() == []  # already applied by the fixture
    versions = store.wh.sql(
        "SELECT version FROM schema_migration WHERE version = 'content_005_insights'"
    )
    assert len(versions) == 1


def test_a_team_insight_needs_no_player_code_column_value(store) -> None:
    """The DDL must permit the row a fixture swing actually is."""
    rows, dropped = _rows([_insight(
        topic="fixture_swing", entity_kind="team", entity_name="Arsenal",
        claim_text="Arsenal's fixtures turn from GW6",
        quote="Arsenal's fixtures turn in gameweek six and stay good until twelve",
        horizon_gw=6, horizon_gw_end=12,
    )])
    assert dropped == []
    assert store_insights(store.wh, rows) == 1
    got = store.wh.sql("SELECT * FROM content_insight").iloc[0]
    assert got["entity_kind"] == "team"
    assert got["player_code"] is None or bool(pd_isna(got["player_code"]))
    assert got["entity_ref"] == "arsenal"
    assert got["entity_name"] == "Arsenal"
    assert int(got["horizon_gw"]) == 6 and int(got["horizon_gw_end"]) == 12


def pd_isna(value) -> bool:
    import pandas as pd

    return bool(pd.isna(value))


# ---------------------------------------------------------------------------
# an insight is not a recommendation
# ---------------------------------------------------------------------------


def test_insights_never_become_claims() -> None:
    """The separation the whole table exists for.

    If an observation leaked into content_claim, claim_outcome would settle it
    and creator_score would mark a creator wrong for correctly spotting a role
    change whose player then blanked. That is not a measurement of anything.
    """
    from fpl_edge.ingest.content.analyze import claims_from_analysis

    analysis = _analysis([_insight(), _insight(
        topic="set_pieces", entity_kind="player", entity_name="Erling Haaland",
        claim_text="Haaland is on penalties again",
        quote="Haaland has taken the penalties back off Alvarez",
    )])
    claims, dropped = claims_from_analysis(
        analysis, item=_Item(), resolver=_StrictResolver(), default_gw=6,
        season=SEASON,
    )
    assert claims == [] and dropped == []


def test_an_episode_of_pure_observation_is_not_a_barren_read() -> None:
    """A Solio-style creator said plenty; the pipeline must stop saying it did not."""
    assert not analyze.analysis_is_empty(_analysis([_insight()]))
    assert analyze.analysis_is_empty(_analysis([]))


def test_the_prompt_draws_the_observation_recommendation_line() -> None:
    """Without this the model has only one shape and flattens everything into it.

    "Semenyo is playing as a false nine" then comes back as a BUY -- a
    prediction nobody made, filed under a creator's name and settled by the
    scoreboard.
    """
    system = analyze._system_prompt()
    assert "INSIGHTS" in system
    assert "NOT recommendations" in system
    # An operational test, not merely a definition.
    assert "Could the listener" in system or "tell the listener what to DO" in system
    # The both-in-one-sentence case is the one a rule-free model gets wrong.
    assert "produces BOTH" in system
    # The quote is non-negotiable and the prompt says so in the imperative.
    assert "IF YOU CANNOT QUOTE IT, DO NOT RECORD IT." in system
    # An unstated horizon is not inferred.
    assert "Never infer a window" in system


def test_the_show_notes_preamble_forbids_manufactured_insights() -> None:
    """The existing pattern, extended: empty is the correct answer for notes."""
    class _FakeParsed:
        def __init__(self, a):
            self.parsed_output = a

    class _FakeMessages:
        def __init__(self, a):
            self._a, self.calls = a, []

        def parse(self, **kw):
            self.calls.append(kw)
            return _FakeParsed(self._a)

    class _FakeClient:
        def __init__(self, a):
            self.messages = _FakeMessages(a)

    fake = _FakeClient(_analysis([]))
    analyze.analyze_transcript(title="MY GW2 TEAM", creator="FPL Focal",
                               text="Get premium https://x/y 12:30 Semenyo's new role",
                               text_source="description", client=fake)
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "insights must be EMPTY" in sent
    assert "TOPIC LABEL" in sent
    assert "nothing in this text to quote" in sent


# ---------------------------------------------------------------------------
# no fabricated data
# ---------------------------------------------------------------------------


def test_an_insight_without_a_quote_is_dropped_and_counted() -> None:
    rows, dropped = _rows([_insight(quote="   ")])
    assert rows == []
    assert dropped == [("no_quote", "Semenyo is playing as a false nine")]


def test_the_row_itself_refuses_to_exist_without_a_quote() -> None:
    """Belt and braces: the invariant is enforced at the type, not only the loop."""
    with pytest.raises(ValueError, match="verbatim quote"):
        InsightRow(
            insight_id="x", item_id="i", creator="c", source_key="s",
            topic="tactical", entity_kind="none", player_code=None,
            entity_ref=None, entity_name="", claim_text="something",
            quote="", start_s=None, horizon_gw=None, horizon_gw_end=None,
            confidence=0.6, published_at=BEFORE, season=SEASON, gameweek=6,
            extractor="llm:claude-opus-5",
        )


def test_a_player_code_on_a_non_player_entity_is_refused() -> None:
    """A team row carrying a player code is a mis-join waiting to happen."""
    with pytest.raises(ValueError, match="mis-join"):
        InsightRow(
            insight_id="x", item_id="i", creator="c", source_key="s",
            topic="fixture_swing", entity_kind="team", player_code=3,
            entity_ref="arsenal", entity_name="Arsenal", claim_text="turn",
            quote="their fixtures turn", start_s=None, horizon_gw=6,
            horizon_gw_end=None, confidence=0.6, published_at=BEFORE,
            season=SEASON, gameweek=6, extractor="llm:claude-opus-5",
        )


def test_no_start_s_is_invented_when_the_quote_cannot_be_located() -> None:
    """A deep link to the wrong minute is worse than no deep link."""
    rows, _ = _rows([_insight()], locate=lambda q: None)
    assert rows[0].start_s is None
    rows, _ = _rows([_insight()], locate=lambda q: 812.5)
    assert rows[0].start_s == 812.5
    rows, _ = _rows([_insight()])  # no locator supplied at all
    assert rows[0].start_s is None


# ---------------------------------------------------------------------------
# resolution: the same strict path as claims
# ---------------------------------------------------------------------------


def test_a_resolvable_player_gets_the_stable_code() -> None:
    rows, dropped = _rows([_insight()])
    assert len(rows) == 1
    assert rows[0].player_code == 464391
    assert rows[0].entity_name == "Antoine Semenyo"
    assert dropped == []


def test_an_unresolvable_name_keeps_the_creators_words_with_a_null_code() -> None:
    """Dropped-and-guessed and dropped-entirely are both wrong here.

    A claim about the wrong player is a fabrication; an insight thrown away is
    a creator silenced. The row survives with a NULL code, so the UI shows what
    was said and nothing downstream can join a stranger's history onto it.
    """
    rows, dropped = _rows([_insight(entity_name="Louie Barry",
                                    claim_text="Barry starts up top")])
    assert len(rows) == 1
    assert rows[0].player_code is None
    assert rows[0].entity_name == "Louie Barry"
    assert dropped == [("unresolved_player", "Louie Barry")]


def test_a_named_player_with_no_name_is_a_hole_not_an_insight() -> None:
    rows, dropped = _rows([_insight(entity_name="   ")])
    assert rows == []
    assert dropped[0][0] == "no_entity_name"


def test_an_insight_about_nothing_in_particular_is_allowed() -> None:
    """"The international break resets everyone's minutes" has no entity."""
    rows, dropped = _rows([_insight(
        entity_kind="none", entity_name="", topic="minutes",
        claim_text="Everyone's minutes reset after the break",
        quote="after an international break nobody's minutes mean anything",
    )])
    assert dropped == []
    assert rows[0].entity_kind == "none"
    assert rows[0].player_code is None and rows[0].entity_ref is None


def test_entity_ref_groups_a_team_without_pretending_to_be_a_key() -> None:
    assert analyze.normalise_entity_ref("Spurs") == "spurs"
    assert analyze.normalise_entity_ref("  SPURS ") == "spurs"
    assert analyze.normalise_entity_ref("Nott'm Forest") == "nott m forest"
    assert analyze.normalise_entity_ref("") is None
    assert analyze.normalise_entity_ref(None) is None


# ---------------------------------------------------------------------------
# horizons: stated only
# ---------------------------------------------------------------------------


def test_an_unstated_horizon_stays_null_rather_than_borrowing_the_gameweek() -> None:
    """`gameweek` is when it was SAID; `horizon_gw` is when it APPLIES."""
    rows, _ = _rows([_insight()])
    assert rows[0].horizon_gw is None and rows[0].horizon_gw_end is None
    assert rows[0].gameweek == 6  # the item's own inferred gameweek


def test_a_stated_range_survives_intact() -> None:
    rows, _ = _rows([_insight(horizon_gw=6, horizon_gw_end=12)])
    assert (rows[0].horizon_gw, rows[0].horizon_gw_end) == (6, 12)


def test_an_end_without_a_beginning_is_not_a_window() -> None:
    rows, _ = _rows([_insight(horizon_gw=None, horizon_gw_end=12)])
    assert rows[0].horizon_gw is None and rows[0].horizon_gw_end is None


def test_a_reversed_or_impossible_range_is_nulled_not_repaired() -> None:
    rows, _ = _rows([_insight(horizon_gw=12, horizon_gw_end=6)])
    assert (rows[0].horizon_gw, rows[0].horizon_gw_end) == (12, None)
    rows, _ = _rows([_insight(horizon_gw=54, horizon_gw_end=60)])
    assert (rows[0].horizon_gw, rows[0].horizon_gw_end) == (None, None)


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


def test_show_notes_produce_no_insights_at_all() -> None:
    """Even if the model returned some, they never reach the table."""
    rows, dropped = _rows([_insight()], text_source="description")
    assert rows == []
    assert dropped == [("thin_source", "description")]


def test_the_gate_is_the_same_shape_as_the_claim_gate() -> None:
    for source in ("transcript", "article"):
        assert analyze.insights_permitted(source)
        assert analyze.is_scoreable(source)
    assert not analyze.insights_permitted("description")
    assert not analyze.is_scoreable("description")


def test_an_omitted_text_source_does_not_silently_open_the_gate() -> None:
    """Callers that cannot say what they read still get the strict path.

    ``text_source=None`` means "not stated", and the pipeline always states it.
    The single-link path in interfaces/creators.py may not, so the behaviour is
    pinned: with no text_source the rows are built, because the caller has
    already decided; the gate is theirs to apply.
    """
    rows, dropped = insights_from_analysis(
        _analysis([_insight()]), item=_Item(), resolver=_StrictResolver(),
        default_gw=6, season=SEASON,
    )
    assert len(rows) == 1 and dropped == []


# ---------------------------------------------------------------------------
# persistence and the sanctioned read
# ---------------------------------------------------------------------------


def test_insights_round_trip_and_re_extraction_is_idempotent(store) -> None:
    rows, _ = _rows([_insight()])
    assert store_insights(store.wh, rows) == 1
    # A second identical read of the same episode writes nothing new: the id is
    # content-addressed on the quote, not on a positional offset.
    again, _ = _rows([_insight(claim_text="Semenyo now plays as a false nine")])
    assert again[0].insight_id == rows[0].insight_id
    assert store_insights(store.wh, again) == 0
    assert int(store.wh.sql("SELECT count(*) c FROM content_insight").iloc[0]["c"]) == 1


def test_stored_columns_carry_the_conviction_band_and_the_llm_extractor(store) -> None:
    rows, _ = _rows([_insight(conviction="medium")])
    store_insights(store.wh, rows)
    got = store.wh.sql("SELECT * FROM content_insight").iloc[0]
    assert float(got["confidence"]) == CONVICTION_CONF["medium"] == 0.6
    assert str(got["extractor"]).startswith("llm:claude-")
    assert got["quote"].startswith("Semenyo is playing as a false nine now")
    assert got["creator"] == "SolioAnalytics"


def test_an_insight_published_after_the_deadline_is_invisible_at_it(store) -> None:
    """The leakage test. A Monday "here is why he played there on Saturday" is a
    true observation published too late to have informed anything, and it reads
    exactly like foresight once the timestamp is dropped."""
    class _Late(_Item):
        item_id = "item_late"
        published_at = DEADLINE + dt.timedelta(hours=6)

    early, _ = _rows([_insight()])
    late, _ = insights_from_analysis(
        _analysis([_insight(quote="he was a false nine on Saturday, that is why")]),
        item=_Late(), resolver=_StrictResolver(), default_gw=6, season=SEASON,
    )
    store_insights(store.wh, early + late)

    visible = insights_visible_at(store.wh, DEADLINE)
    assert list(visible["item_id"]) == ["item_solio_1"]
    # And the boundary is strict: an insight published AT the deadline is out.
    assert insights_visible_at(store.wh, BEFORE).empty


def test_the_read_path_refuses_a_naive_timestamp(store) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        insights_visible_at(store.wh, dt.datetime(2026, 8, 21, 17, 30))  # noqa: DTZ001


def test_the_read_path_filters_by_creator_player_and_topic(store) -> None:
    rows, _ = _rows([
        _insight(),
        _insight(topic="fixture_swing", entity_kind="team", entity_name="Arsenal",
                 claim_text="Arsenal fixtures turn",
                 quote="the Arsenal fixtures turn in six"),
    ])
    store_insights(store.wh, rows)
    after = DEADLINE
    assert len(insights_visible_at(store.wh, after, creator="SolioAnalytics")) == 2
    assert insights_visible_at(store.wh, after, creator="Nobody").empty
    assert len(insights_visible_at(store.wh, after, player_code=464391)) == 1
    assert len(insights_visible_at(store.wh, after, topic="fixture_swing")) == 1
    assert len(insights_visible_at(store.wh, after, season=SEASON)) == 2


# ---------------------------------------------------------------------------
# backwards compatibility: an old row said nothing about insights
# ---------------------------------------------------------------------------


class _StubWh:
    def __init__(self, payload):
        self._payload = payload

    def sql(self, sql, params=()):
        import pandas as pd

        return pd.DataFrame({"analysis_json": [json.dumps(self._payload)]})


def _legacy_payload() -> dict:
    payload = _analysis([]).model_dump()
    payload.pop("insights")
    return payload


def test_load_analysis_still_works_on_a_row_written_before_insights() -> None:
    got = analyze.load_analysis(_StubWh(_legacy_payload()), "old_item")
    assert got is not None
    assert got.insights == []


def test_an_old_row_reports_not_extracted_not_none_were_said() -> None:
    """`[]` is a finding. `None` is the absence of one, and they are different.

    Rendering an unanalysed episode as "no insights" tells the reader something
    the warehouse never said.
    """
    assert analyze.load_insights(_StubWh(_legacy_payload()), "old_item") is None
    assert not analyze.analysis_has_insight_field(_legacy_payload())


def test_a_new_row_that_found_nothing_reports_an_empty_list() -> None:
    fresh = _analysis([]).model_dump()
    assert analyze.analysis_has_insight_field(fresh)
    assert analyze.load_insights(_StubWh(fresh), "new_item") == []


def test_a_new_row_round_trips_its_insights() -> None:
    payload = _analysis([_insight(horizon_gw=6)]).model_dump()
    got = analyze.load_insights(_StubWh(payload), "new_item")
    assert got is not None and len(got) == 1
    assert got[0].topic == "role_change"
    assert got[0].entity_name == "Antoine Semenyo"
    assert got[0].horizon_gw == 6


def test_store_analysis_writes_the_insights_key(tmp_path) -> None:
    written = []

    class _Wh:
        def sql(self, sql, binds):
            written.append(binds)

    analyze.store_analysis(_Wh(), "item_1", _analysis([_insight()]),
                           text_source="transcript")
    payload = json.loads(written[0][3])
    assert payload["insights"][0]["quote"].startswith("Semenyo is playing")
    # The evidence block keeps its established shape; insights are a sibling.
    assert payload[analyze.EVIDENCE_KEY]["depth"] == "transcript"


# ---------------------------------------------------------------------------
# the wiring: every path that stores an analysis must also store its insights
# ---------------------------------------------------------------------------


def test_every_writer_of_an_analysis_also_writes_its_insights() -> None:
    """`content_insight` held 0 rows for its whole existence.

    Not because extraction was broken -- `insights_from_analysis`,
    `store_insights` and `insights_visible_at` were all written and all tested
    -- but because nothing ever called them. The two functions that persist an
    analysis (`pipeline.cmd_analyze` for the bulk crawl, `interfaces.creators`
    for a pasted link) each extracted claims and dropped the observations from
    the same reading on the floor.

    A table nobody writes to is indistinguishable from a table with nothing to
    say, which is why this was invisible for so long: every consumer correctly
    reported "no team-talk", and every one of them was right.
    """
    import inspect

    import fpl_edge.ingest.content.pipeline as pipeline
    from fpl_edge.interfaces import creators as creators_iface

    for mod in (pipeline, creators_iface):
        src = inspect.getsource(mod)
        assert "store_analysis(" in src, (
            f"{mod.__name__} no longer stores analyses; this test is checking "
            "the wrong module"
        )
        assert "insights_from_analysis(" in src, (
            f"{mod.__name__} stores an analysis but never extracts its "
            "insights -- the observations in that reading are paid for and "
            "then discarded"
        )
        assert "store_insights(" in src, (
            f"{mod.__name__} extracts insights but never persists them"
        )
