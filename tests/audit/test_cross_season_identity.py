"""Cross-season identity corruption.

Hunt list item 2. ``element_id`` is a per-season row number, reassigned every
summer. ``code`` is stable. The codebase says so in three places and the schema
enforces it in the primary keys, so the obvious version of this bug is absent.
These tests go after the versions that are still open:

* two distinct players colliding on one code, and the collision being SILENT;
* one player splitting across two codes when FPL reissues a temporary code;
* an element_id that resolves to different players in different seasons.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.store import PIT_KEYS
from fpl_edge.types import ElementId, PlayerCode

from .conftest import TODAY, UTC, frame, player_row, result_row

AS_OF = dt.datetime(2026, 8, 1, tzinfo=UTC)


def test_no_point_in_time_table_is_keyed_on_element_id() -> None:
    """GUARDS: the per-season row id becoming a cross-season key.

    ``PIT_KEYS`` decides what "the latest row for this entity" means. If
    ``element_id`` ever appears there, the snapshot's ROW_NUMBER partition
    welds together whichever players happened to share a row number.
    """
    offenders = {t: k for t, k in PIT_KEYS.items() if "element_id" in k}
    assert not offenders, f"element_id used as a PIT entity key in {offenders}"


def test_two_players_claiming_one_code_is_loud_not_silent(wh) -> None:
    """GUARDS: two players collapsing into one code without a word.

    ``Warehouse.append`` deduplicates on the PRIMARY KEY, not on row equality,
    despite its docstring saying "dropping exact duplicates already present".
    Appending a second ``dim_player`` row with the same (season, code, as_of)
    but a different element_id and a different name returns 0 and reports
    success. Two careers merge into one and nothing is raised, logged or
    counted.

    ``fpl_edge/ingest/player_mapping.py`` raises ``IdentityCollisionError`` for
    exactly this case -- but only for data flowing through its own index. The
    warehouse itself, which every other loader writes to, does not.
    """
    first = player_row(season="2026-27", code=204480, element_id=21,
                       as_of=AS_OF, web_name="Rice")
    impostor = player_row(season="2026-27", code=204480, element_id=88,
                          as_of=AS_OF, web_name="SomebodyElse")

    assert wh.append("dim_player", frame([first])) == 1
    with pytest.raises((ValueError, RuntimeError), match="(?i)collision|conflict|duplicate|differ"):
        wh.append("dim_player", frame([impostor]))


def test_a_changed_value_at_the_same_as_of_is_not_silently_discarded(wh) -> None:
    """GUARDS: a corrected fact being thrown away without notice.

    Same mechanism, different consequence. FPL corrects match data -- an assist
    is reassigned, a red card is rescinded. If a corrected row arrives carrying
    the same ``as_of`` as the original (which any date-granular backfill will
    produce), ``append`` matches on the PK, sees "already present", and keeps the
    WRONG one. It returns 0, which the caller reads as "idempotent, nothing to
    do".
    """
    original = result_row(season="2026-27", code=1, fixture_id=1, gw=1,
                          as_of=AS_OF, assists=1, total_points=5)
    corrected = dict(original, assists=0, total_points=2)

    assert wh.append("fact_player_fixture", frame([original])) == 1
    with pytest.raises((ValueError, RuntimeError), match="(?i)conflict|differ|exist|correct"):
        wh.append("fact_player_fixture", frame([corrected]))


def test_same_element_id_in_two_seasons_stays_two_players(wh) -> None:
    """GUARDS: history welded together by row number.

    Regression guard, currently correct. Element 21 is Declan Rice in 2025-26
    and somebody else entirely in 2026-27. A join on element_id gives one
    player with a fabricated two-season history; a join on code gives two.
    """
    rows = [
        player_row(season="2025-26", code=204480, element_id=21, as_of=AS_OF, web_name="Rice"),
        player_row(season="2026-27", code=999999, element_id=21, as_of=AS_OF, web_name="Newcomer"),
    ]
    wh.append("dim_player", frame(rows))
    got = wh.snapshot_at(TODAY).table("dim_player")

    by_element = got.groupby("element_id")["code"].nunique()
    assert int(by_element.loc[21]) == 2, "element 21 must resolve to two distinct codes"

    by_code = got.groupby("code").size()
    assert set(by_code.values) == {1}, "each code must be one player-season row"


def test_a_transferred_player_keeps_one_code_across_clubs(wh) -> None:
    """GUARDS: a mid-career club move splitting a player in two.

    Rice is 204480 at West Ham and at Arsenal. Keying features on
    (code, team_code) rather than code silently restarts his history at the
    transfer, which is the failure mode that makes every summer signing look
    like a debutant.
    """
    rows = [
        player_row(season="2025-26", code=204480, element_id=21, as_of=AS_OF,
                   web_name="Rice", team_code=3),
        player_row(season="2026-27", code=204480, element_id=40, as_of=AS_OF,
                   web_name="Rice", team_code=3),
    ]
    wh.append("dim_player", frame(rows))
    got = wh.snapshot_at(TODAY).table("dim_player")
    assert got["code"].nunique() == 1
    assert got["element_id"].nunique() == 2


def test_temporary_codes_are_flagged_at_ingest() -> None:
    """GUARDS: FPL's own admission that a code is not yet stable.

    The bootstrap payload carries ``has_temporary_code`` on every element. A
    True value means the code WILL be reissued once the Premier League registry
    catches up with the transfer -- so any history keyed on it splits into two
    players at the moment it changes, and the split is invisible because both
    halves look like valid codes.

    ``fpl_edge/ingest/fpl_api.py:85`` reads ``el["code"]`` and never looks at the
    flag. Today's payload happens to have zero temporary codes, which is why
    this is a synthetic payload rather than a live assertion: the bug is latent
    and fires in January, not in August.
    """
    import inspect

    from fpl_edge.ingest import fpl_api
    from fpl_edge.ingest.fpl_api import ingest_bootstrap  # noqa: F401  (import proves module shape)

    source = inspect.getsource(fpl_api)
    assert "has_temporary_code" in source, (
        "ingest_bootstrap stores el['code'] without consulting "
        "el['has_temporary_code']. A temporarily-coded player is written into "
        "dim_player under an identifier FPL has already said it will replace, "
        "and no later ingest can tell the two halves of that career apart"
    )


def test_newtypes_do_not_actually_prevent_a_mixup() -> None:
    """DOCUMENTS: the type-level defence is documentation, not enforcement.

    ``fpl_edge/types.py:14`` says PlayerCode and ElementId are "deliberately
    distinct NewTypes so a mix-up is a type error". At runtime both are ``int``
    and the mix-up is free. The defence is real only if mypy runs in CI over
    every module that touches an id, and ``make lint`` runs mypy with
    ``--ignore-missing-imports`` over ``fpl_edge`` only -- not over tests, not
    over scripts.

    Recorded as a test so the claim in the docstring is not mistaken for a
    guarantee.
    """
    code = PlayerCode(204480)
    element = ElementId(204480)
    assert code == element, "at runtime these are the same int; only mypy separates them"
    assert isinstance(code, int) and isinstance(element, int)


def test_season_label_format_is_consistent_between_rules_and_data(live_wh) -> None:
    """GUARDS: a join that silently returns nothing.

    ``fpl_edge/rules/registry.yaml`` declares ``season: "2026/27"`` with a
    slash. ``fpl_edge/ingest/fpl_api.py:33`` derives ``"2026-27"`` with a
    hyphen, and that is what every warehouse row carries. Any code that filters
    warehouse rows by ``rules().season`` matches zero rows and produces an empty
    frame rather than an error -- which downstream looks like "no data yet"
    rather than "wrong key".
    """
    from fpl_edge.rules import rules

    declared = rules().season
    stored = set(live_wh.sql("SELECT DISTINCT season FROM dim_player")["season"])
    if not stored:
        pytest.skip("no players ingested")

    assert declared in stored, (
        f"rules().season is {declared!r} but the warehouse stores {sorted(stored)}. "
        "These are the same season written two ways; a filter on the registry "
        "value silently matches nothing"
    )
