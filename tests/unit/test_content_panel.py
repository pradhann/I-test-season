"""The person layer: the roster, the scope, and what may NOT be attributed.

The corpus is keyed by show. "The FPL Wire" is three people, and the newest
episode in the live warehouse is titled "Free Hit or Wildcard? - Zophar Gameweek
2 Team" -- one host's team filed under a three-host label. These tests hold the
line on the two ways that can go wrong:

* **Fabrication.** A person with no known FPL entry id must not acquire one, and
  a null must arrive with a reason. The loader rejects the person rather than
  writing an unexplained blank.
* **Guessing.** An episode whose host cannot be established gets NO
  ``item_person`` row -- not a default, not the first-listed host, not the most
  prolific one. The unattributed state is a first-class answer and the tests
  below assert it is reachable, representable and preserved by the read path.

Hermetic: every test builds its own DuckDB in tmp_path and writes its own YAML.
Nothing reads ``data/warehouse/fpl.duckdb`` or the real panel file.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.ingest.content import panel as panel_mod
from fpl_edge.ingest.content.models import Action, Claim
from fpl_edge.ingest.content.panel import (
    BASIS_SOLE_HOST,
    BASIS_TITLE,
    attribute_items,
    load_panel,
    panel_scope,
    person_claims_visible_at,
    upsert_panel,
)
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse
from fpl_edge.types import GwId, PlayerCode

UTC = dt.UTC
SEASON = "2026-27"
DEADLINE = dt.datetime(2026, 8, 28, 17, 30, tzinfo=UTC)
BEFORE = DEADLINE - dt.timedelta(days=1)
AFTER = DEADLINE + dt.timedelta(days=1)

#: Two real shows from the registry: one with several hosts, one whose podcast
#: and YouTube channel share a creator label. Using registry names on purpose --
#: load_panel refuses a show that could never join to a content_item row.
WIRE = "The FPL Wire"
HARRY = "FPL Harry"
FOCAL = "FPL Focal"

PANEL_YAML = f"""
season: "{SEASON}"
as_of: 2026-08-27T00:00:00Z
people:
  - person_key: zophar
    display_name: Zophar
    aliases: [Zophar]
    handles:
      twitter: zopharfpl
    entry_id: 1234567
    entry_verified: true
    entry_source_url: https://example.invalid/zophar-states-his-id
    entry_api_name: Zophar XI
    entry_checked_utc: 2026-08-26T10:00:00Z
    top10k_finishes: 8
    active: true
    shows:
      - creator: {WIRE}
        role: host
  - person_key: lateriser
    display_name: Lateriser
    aliases: [Lateriser, Lateriser12]
    entry_reason: "never stated publicly; no verifiable source found"
    shows:
      - creator: {WIRE}
        role: host
  - person_key: harry
    display_name: FPL Harry
    aliases: [Harry]
    entry_reason: "not looked for yet"
    shows:
      - creator: {HARRY}
        role: host
"""


@pytest.fixture
def warehouse(tmp_path):
    with Warehouse(tmp_path / "panel.duckdb") as wh:
        ContentStore(wh)  # applies content_004_panel_people.sql
        yield wh


@pytest.fixture
def panel_file(tmp_path):
    path = tmp_path / "creator_panel_2026_27.yaml"
    path.write_text(PANEL_YAML)
    return path


def _item(wh, *, item_id: str, creator: str, title: str, source_key: str,
          published_at: dt.datetime = BEFORE) -> None:
    wh.sql(
        "INSERT INTO content_item (item_id, source_key, creator, kind, title, url, "
        "published_at, fetched_at, text_source, text, text_sha256) "
        "VALUES (?, ?, ?, 'podcast', ?, 'https://example.invalid/ep', ?, ?, "
        "'description', 'body', 'sha')",
        [item_id, source_key, creator, title, published_at, BEFORE],
    )


class TestLoadPanel:
    def test_an_absent_file_is_a_panel_not_an_exception(self, tmp_path) -> None:
        """The file is produced by another process. Its absence is handled."""
        result = load_panel(tmp_path / "nope.yaml")
        assert result.people == ()
        assert result.missing_reason is not None
        assert not result.ok

    def test_a_valid_file_loads_every_person_and_show(self, panel_file) -> None:
        result = load_panel(panel_file)
        assert result.problems == ()
        assert {p.person_key for p in result.people} == {"zophar", "lateriser", "harry"}
        assert result.season == SEASON
        assert result.as_of_basis == "yaml"
        zophar = next(p for p in result.people if p.person_key == "zophar")
        assert zophar.entry_id == 1234567
        assert zophar.entry_verified is True
        assert [s.show_creator for s in zophar.shows] == [WIRE]

    def test_a_null_entry_id_without_a_reason_is_rejected(self, tmp_path) -> None:
        """A null gets a reason. An unexplained blank reads as 'nobody looked'
        when it may mean 'looked, and it is not public'."""
        path = tmp_path / "p.yaml"
        path.write_text(
            f"people:\n  - person_key: x\n    display_name: X\n"
            f"    shows: [{{creator: {WIRE}}}]\n"
        )
        result = load_panel(path)
        assert result.people == ()
        assert any("entry_reason" in p for p in result.problems)

    def test_a_verified_entry_with_no_source_url_is_rejected(self, tmp_path) -> None:
        path = tmp_path / "p.yaml"
        path.write_text(
            f"people:\n  - person_key: x\n    display_name: X\n"
            f"    entry_id: 42\n    entry_verified: true\n"
            f"    shows: [{{creator: {WIRE}}}]\n"
        )
        result = load_panel(path)
        assert result.people == ()
        assert any("entry_source_url" in p for p in result.problems)

    def test_an_unregistered_show_is_rejected(self, tmp_path) -> None:
        """A show that is not in the registry can never join to an item."""
        path = tmp_path / "p.yaml"
        path.write_text(
            "people:\n  - person_key: x\n    display_name: X\n"
            "    entry_reason: unknown\n"
            "    shows: [{creator: A Podcast That Does Not Exist}]\n"
        )
        result = load_panel(path)
        assert result.people == ()
        assert any("not a registered creator" in p for p in result.problems)

    def test_a_two_character_alias_is_refused(self, tmp_path) -> None:
        """It would match inside ordinary words and nothing would notice."""
        path = tmp_path / "p.yaml"
        path.write_text(
            f"people:\n  - person_key: x\n    display_name: Xavier\n"
            f"    aliases: [Xavier, Xa]\n    entry_reason: unknown\n"
            f"    shows: [{{creator: {WIRE}}}]\n"
        )
        result = load_panel(path)
        assert result.people[0].aliases == ("Xavier",)
        assert any("shorter than" in p for p in result.problems)


class TestUpsert:
    def test_people_and_shows_reach_the_warehouse(self, warehouse, panel_file) -> None:
        write = upsert_panel(warehouse, load_panel(panel_file))
        assert write.people_written == 3
        assert write.shows_written == 3
        rows = warehouse.sql("SELECT * FROM panel_person ORDER BY person_key")
        assert list(rows["person_key"]) == ["harry", "lateriser", "zophar"]
        lateriser = rows[rows["person_key"] == "lateriser"].iloc[0]
        assert pd.isna(lateriser["entry_id"]), "a null entry id stays null"
        assert "never stated publicly" in lateriser["entry_reason"]

    def test_reloading_replaces_the_roster_rather_than_accumulating(
        self, warehouse, panel_file
    ) -> None:
        """A person who leaves a show must lose the mapping."""
        upsert_panel(warehouse, load_panel(panel_file))
        panel_file.write_text(
            f"as_of: 2026-08-28T00:00:00Z\npeople:\n  - person_key: zophar\n"
            f"    display_name: Zophar\n    entry_reason: withdrawn\n"
            f"    shows: [{{creator: {WIRE}}}]\n"
        )
        upsert_panel(warehouse, load_panel(panel_file))
        assert list(
            warehouse.sql("SELECT person_key FROM panel_person")["person_key"]
        ) == ["zophar"]
        assert int(
            warehouse.sql("SELECT count(*) c FROM panel_person_show").iloc[0]["c"]
        ) == 1

    def test_an_absent_panel_writes_nothing_and_says_why(self, warehouse, tmp_path):
        write = upsert_panel(warehouse, load_panel(tmp_path / "nope.yaml"))
        assert write.people_written == 0
        assert write.skipped_reason is not None


class TestScope:
    def test_the_scope_is_the_panel_shows_sources(self, warehouse, panel_file) -> None:
        upsert_panel(warehouse, load_panel(panel_file))
        scope = panel_scope(warehouse)
        keys = {s.key for s in scope.apply()}
        assert keys == {"pod_fplwire", "pod_fplharry", "yt_fplharry"}, (
            "a show publishes through several sources and the scope must reach "
            "all of them"
        )

    def test_the_other_sources_are_narrowed_not_deleted(self, warehouse, panel_file):
        from fpl_edge.ingest.content.sources import BY_KEY, fetchable

        upsert_panel(warehouse, load_panel(panel_file))
        scope = panel_scope(warehouse)
        assert len(scope.apply()) < len(fetchable())
        assert "pod_letstalkfpl" in BY_KEY, "the registry still holds every source"

    def test_an_empty_panel_degrades_to_everything_with_a_reason(self, warehouse):
        """A scope that silently selected zero sources is indistinguishable
        from a fetcher that is broken."""
        scope = panel_scope(warehouse)
        assert scope.keys is None
        assert "panel is empty" in scope.label

    def test_an_unknown_source_key_is_reported_not_swallowed(self) -> None:
        from fpl_edge.ingest.content.sources import Scope

        scope = Scope.from_keys(["pod_fplwire", "pod_typo"], label="test")
        assert scope.unknown_keys == ("pod_typo",)
        assert {s.key for s in scope.apply()} == {"pod_fplwire"}


class TestAttribution:
    def test_a_single_host_show_attributes_by_sole_host(self, warehouse, panel_file):
        upsert_panel(warehouse, load_panel(panel_file))
        _item(warehouse, item_id="h1", creator=HARRY,
              title="GW2 team reveal", source_key="pod_fplharry")

        report = attribute_items(warehouse)

        rows = warehouse.sql("SELECT * FROM item_person WHERE item_id = 'h1'")
        assert list(rows["person_key"]) == ["harry"]
        assert list(rows["basis"]) == [BASIS_SOLE_HOST]
        assert rows.iloc[0]["evidence"] is None, (
            "sole_host is structural; inventing a quote for it would be the "
            "only dishonest thing in this table"
        )
        assert report.unattributed == 0

    def test_a_named_host_on_a_multi_host_show_attributes_by_title(
        self, warehouse, panel_file
    ) -> None:
        """The episode this whole layer exists for."""
        upsert_panel(warehouse, load_panel(panel_file))
        _item(warehouse, item_id="w1", creator=WIRE,
              title="Free Hit or Wildcard? - Zophar Gameweek 2 Team",
              source_key="pod_fplwire")

        attribute_items(warehouse)

        rows = warehouse.sql("SELECT * FROM item_person WHERE item_id = 'w1'")
        assert list(rows["person_key"]) == ["zophar"]
        assert list(rows["basis"]) == [BASIS_TITLE]
        assert rows.iloc[0]["evidence"] == "Zophar", "the verbatim span, for audit"

    def test_an_unnamed_multi_host_episode_is_attributed_to_nobody(
        self, warehouse, panel_file
    ) -> None:
        """THE test. A round table belongs to the show, and that is the answer.

        No default host, no first-listed host, no most-prolific host. The
        absence of a row is the representation of 'we do not know', and code
        that turns it into a name is the failure this layer was built to stop.
        """
        upsert_panel(warehouse, load_panel(panel_file))
        _item(warehouse, item_id="w2", creator=WIRE,
              title="Gameweek 2 Preview and Captaincy", source_key="pod_fplwire")

        report = attribute_items(warehouse)

        assert warehouse.sql(
            "SELECT count(*) c FROM item_person WHERE item_id = 'w2'"
        ).iloc[0]["c"] == 0
        assert report.unattributed == 1

    def test_an_alias_never_matches_across_shows(self, warehouse, panel_file) -> None:
        """"Harry" in an FPL Focal title is not FPL Harry.

        The join to panel_person_show is the only thing standing between a
        person-level track record and a fortnight of someone else's picks.
        """
        upsert_panel(warehouse, load_panel(panel_file))
        _item(warehouse, item_id="f1", creator=FOCAL,
              title="Why Harry Kane is not coming back", source_key="pod_fplfocal")

        attribute_items(warehouse)

        assert warehouse.sql(
            "SELECT count(*) c FROM item_person WHERE item_id = 'f1'"
        ).iloc[0]["c"] == 0

    def test_an_alias_matches_whole_words_only(self, warehouse, panel_file) -> None:
        upsert_panel(warehouse, load_panel(panel_file))
        _item(warehouse, item_id="w3", creator=WIRE,
              title="Zopharian tactics explained", source_key="pod_fplwire")

        attribute_items(warehouse)

        assert warehouse.sql(
            "SELECT count(*) c FROM item_person WHERE item_id = 'w3'"
        ).iloc[0]["c"] == 0

    def test_attribution_is_idempotent(self, warehouse, panel_file) -> None:
        upsert_panel(warehouse, load_panel(panel_file))
        _item(warehouse, item_id="w1", creator=WIRE,
              title="Zophar Gameweek 2 Team", source_key="pod_fplwire")

        first = attribute_items(warehouse)
        second = attribute_items(warehouse)

        assert first.written == 1
        assert second.written == 0

    def test_dry_run_writes_nothing(self, warehouse, panel_file) -> None:
        upsert_panel(warehouse, load_panel(panel_file))
        _item(warehouse, item_id="h1", creator=HARRY,
              title="GW2 team", source_key="pod_fplharry")

        report = attribute_items(warehouse, dry_run=True)

        assert report.written == 1
        assert int(
            warehouse.sql("SELECT count(*) c FROM item_person").iloc[0]["c"]
        ) == 0


class TestPersonRead:
    """The read path must stay the sanctioned one with a column added."""

    def _seed_claim(self, store, *, claim_id: str, item_id: str,
                    published_at: dt.datetime) -> None:
        store.insert_claims([
            Claim(
                claim_id=claim_id, item_id=item_id, creator=WIRE,
                source_key="pod_fplwire", player_code=PlayerCode(223094),
                player_name="Erling Haaland", surface_form="Haaland",
                action=Action.CAPTAIN, season=SEASON, gameweek=GwId(2),
                confidence=0.8, rationale="stated",
                source_url="https://example.invalid/ep",
                published_at=published_at,
            )
        ])

    def test_a_claim_published_after_the_deadline_is_still_invisible(
        self, tmp_path, panel_file
    ) -> None:
        """Adding a person column must not open a second read path.

        published_at is load-bearing and claims_visible_at is the only place
        that filters on it. If this function grew its own SELECT, the leakage
        guard would have to be re-proved here -- and the next such function
        would not bother.
        """
        with Warehouse(tmp_path / "read.duckdb") as wh:
            store = ContentStore(wh)
            upsert_panel(wh, load_panel(panel_file))
            _item(wh, item_id="w1", creator=WIRE, title="Zophar GW2 Team",
                  source_key="pod_fplwire", published_at=AFTER)
            self._seed_claim(store, claim_id="c-late", item_id="w1",
                             published_at=AFTER)
            attribute_items(wh)

            visible = person_claims_visible_at(store, DEADLINE, season=SEASON)

            assert visible.empty, "a post-deadline claim reached a deadline read"

    def test_an_attributed_claim_carries_its_person(self, tmp_path, panel_file):
        with Warehouse(tmp_path / "read.duckdb") as wh:
            store = ContentStore(wh)
            upsert_panel(wh, load_panel(panel_file))
            _item(wh, item_id="w1", creator=WIRE, title="Zophar GW2 Team",
                  source_key="pod_fplwire")
            self._seed_claim(store, claim_id="c1", item_id="w1",
                             published_at=BEFORE)
            attribute_items(wh)

            visible = person_claims_visible_at(store, DEADLINE, season=SEASON)

            assert list(visible["person_key"]) == ["zophar"]
            assert list(visible["basis"]) == [BASIS_TITLE]

    def test_an_unattributed_claim_survives_the_join(self, tmp_path, panel_file):
        """LEFT JOIN. It is still the show's claim and still counts for it.

        An inner join here would silently delete the majority of the corpus --
        every round-table episode -- while every count still looked plausible.
        """
        with Warehouse(tmp_path / "read.duckdb") as wh:
            store = ContentStore(wh)
            upsert_panel(wh, load_panel(panel_file))
            _item(wh, item_id="w2", creator=WIRE, title="Gameweek 2 Preview",
                  source_key="pod_fplwire")
            self._seed_claim(store, claim_id="c2", item_id="w2",
                             published_at=BEFORE)
            attribute_items(wh)

            visible = person_claims_visible_at(store, DEADLINE, season=SEASON)

            assert list(visible["claim_id"]) == ["c2"]
            assert visible.iloc[0]["person_key"] is None
            assert visible.iloc[0]["creator"] == WIRE

    def test_two_bases_do_not_double_count_one_claim(self, tmp_path, panel_file):
        """Both bases are kept in item_person; the READ collapses them.

        Corroboration is worth storing. Letting it through the read would
        double this claim's weight in any consensus built on it -- the exact
        inflation the person layer exists to remove.
        """
        with Warehouse(tmp_path / "read.duckdb") as wh:
            store = ContentStore(wh)
            upsert_panel(wh, load_panel(panel_file))
            _item(wh, item_id="h1", creator=HARRY, title="FPL Harry GW2 team",
                  source_key="pod_fplharry")
            store.insert_claims([
                Claim(
                    claim_id="c3", item_id="h1", creator=HARRY,
                    source_key="pod_fplharry", player_code=PlayerCode(223094),
                    player_name="Erling Haaland", surface_form="Haaland",
                    action=Action.CAPTAIN, season=SEASON, gameweek=GwId(2),
                    confidence=0.8, rationale="stated",
                    source_url="https://example.invalid/ep",
                    published_at=BEFORE,
                )
            ])
            attribute_items(wh)

            bases = wh.sql(
                "SELECT basis FROM item_person WHERE item_id = 'h1' ORDER BY basis"
            )
            assert list(bases["basis"]) == [BASIS_SOLE_HOST, BASIS_TITLE]

            visible = person_claims_visible_at(store, DEADLINE, season=SEASON)
            assert len(visible) == 1
            assert visible.iloc[0]["basis"] == BASIS_SOLE_HOST, (
                "the higher-confidence basis is the one that survives"
            )


def test_default_panel_path_is_where_the_other_agent_writes_it() -> None:
    assert panel_mod.DEFAULT_PANEL_PATH.as_posix() == (
        "data/panels/creator_panel_2026_27.yaml"
    )
