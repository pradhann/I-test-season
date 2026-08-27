"""One effective-ownership definition, pinned across all three call sites.

Effective ownership was computed in three places that disagreed:

* ``sem_elite_ownership`` in ``store/views.sql`` summed the stored FPL
  multipliers over the cohort;
* ``CohortRates.eo()`` added ``start_share + captain_share`` plus the ``3xc``
  chip rate *spread over the captain distribution*, because the per-player
  triple-captain vector was thought unknowable;
* the ``ownership_eo`` panel ran its own query over ``fact_manager_pick`` with
  **no cohort filter at all** and labelled the blended result "elite".

Three numbers, one name. The refactor collapsed them onto the multiplier sum;
this module is the guard rail that stops them drifting apart again, so it
compares the SQL, the model and the panel against each other on one warehouse
rather than against three hand-written constants.

The definition, for the record::

    ownership = sum over m of weight[m] holding p         / sum of weight[all]
    eo        = sum over m of weight[m] * multiplier[m,p] / sum of weight[all]
    captaincy = sum over m of weight[m] captaining p      / sum of weight[all]

Weights are all 1 until the per-manager weight vector arrives, so every
denominator here is the cohort's manager count.

What this module learned the hard way
-------------------------------------
The first version of this guard rail pinned three quantities -- ``own_pct``,
``captain_pct``, ``eo_pct`` -- on one eight-manager fixture with no NULL
multipliers, no unresolvable element ids, no superseded pick rows, no Bench
Boost, and every ``dim_manager`` row stamped strictly before the snapshot. An
adversarial pass mutated the two implementations twenty ways; fifteen of the
twenty survived it. The five columns the refactor had *added* to the macro
(``owned_by``, ``started_by``, ``benched_by``, ``captained_by``, ``eo_units``)
were never compared against Python at all, and the boundary the point-in-time
rule is named after -- rows AT OR BEFORE ``p_as_of`` -- could not be crossed by
any fixture row, so turning ``<=`` into ``<`` changed nothing here.

Two things follow, and they are the structure of this module:

1. **Every column the macro exposes is compared**, and
   :func:`test_every_macro_column_is_compared_against_python` fails if a new
   one appears without a Python twin. A guard rail that lets the surface grow
   past it stops being one.
2. **The fixture is representative, not merely legal.** It carries a re-crawl
   that supersedes earlier rows, a manager row landing EXACTLY on the snapshot
   instant, a Bench Boost, a later crawl that must stay invisible, a superseded
   player name, and -- filed under ``unclassified``, where they cannot disturb
   the denominators being compared -- the crawl's four ways of handing us a
   squad we must refuse. See ``tests/unit/field_fixtures.py``.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registers ownership_eo)
from fpl_edge.models.field.cohorts import measure_cohort
from fpl_edge.models.field.observed import (
    CohortReadError,
    load_observed_squads,
    resolve_cohorts,
)
from fpl_edge.platform.registry import run_script
from fpl_edge.sim.squad import SQUAD_SIZE, XI_SIZE
from tests.unit.field_fixtures import (
    GW2_DEADLINE,
    SEASON,
    T_DECIDE,
    build_warehouse,
    toy,
)

UTC = dt.timezone.utc

#: The macro's key columns: what a row is *about*, rather than what it claims.
KEY_COLUMNS = {"season", "gw", "cohort", "code"}

#: Every other column ``sem_elite_ownership`` exposes, each with a Python twin
#: built in :func:`_model_rows`. Adding a column to the macro without adding it
#: here fails ``test_every_macro_column_is_compared_against_python`` -- which is
#: the point: the five columns the refactor added were invisible to the first
#: version of this file, and all five survived mutation.
MEASURED_COLUMNS = {
    "web_name", "n_managers", "owned_by", "started_by", "benched_by",
    "captained_by", "eo_units", "own_pct", "captain_pct", "eo_pct",
}

#: Columns compared as exact integers rather than within a tolerance. A count
#: that is off by a fraction is not a rounding difference, it is a bug.
COUNT_COLUMNS = ("n_managers", "owned_by", "started_by", "benched_by",
                 "captained_by")


def _macro(wh, cohort: str, as_of=T_DECIDE, *, resolved_only: bool = True):
    """``sem_elite_ownership`` for one cohort, keyed by code."""
    where = " AND code IS NOT NULL" if resolved_only else ""
    return wh.sql(
        f"""
        SELECT * FROM sem_elite_ownership(?)
        WHERE season = ? AND gw = 1 AND cohort = ?{where}
        """,
        [as_of, SEASON, cohort],
    )


def _model_rows(observed, rates, universe) -> dict[int, dict]:
    """The macro's every column, computed from the Python side, keyed by code.

    Deliberately sourced from BOTH Python layers: the four counts and
    ``eo_units`` come off :class:`ObservedSquads` (slots and stored
    multipliers), while the three percentages come off :class:`CohortRates` --
    the object every model consumer actually reads. Drift between those two is
    as real as drift against SQL, and reading both here catches it.
    """
    n, n_players = observed.n, universe.n_players
    slots, mult = observed.slots, observed.multipliers
    owned = np.bincount(slots.ravel(), minlength=n_players)
    started = np.bincount(slots[mult >= 1], minlength=n_players)
    benched = np.bincount(slots[mult == 0], minlength=n_players)
    captained = np.bincount(
        slots[np.arange(n), observed.captain_slot], minlength=n_players
    )
    units = np.bincount(
        slots.ravel(), weights=mult.ravel().astype(float), minlength=n_players
    )
    eo = rates.eo()
    return {
        int(universe.codes[p]): {
            "web_name": str(universe.web_name[p]),
            "n_managers": n,
            "owned_by": int(owned[p]),
            "started_by": int(started[p]),
            "benched_by": int(benched[p]),
            "captained_by": int(captained[p]),
            "eo_units": float(units[p]),
            "own_pct": 100.0 * float(rates.ownership[p]),
            "captain_pct": 100.0 * float(rates.captain_share[p]),
            "eo_pct": 100.0 * float(eo[p]),
        }
        for p in np.flatnonzero(owned)
    }


@pytest.fixture()
def world(tmp_path):
    """One warehouse, carrying every complication both layers must survive.

    ``enriched`` plants what a real crawl contains and eight hand-built squads
    do not; ``with_refused`` plants the four broken shapes, filed under
    ``unclassified`` so the elite and top1k denominators under comparison stay
    exactly the crawl the loader accepted. ``with_malformed`` stays off: it
    puts its 14-pick squad in the *elite* pool, which is the one deliberate
    SQL/Python denominator gap and has its own test below.
    """
    wh, universe, meta = build_warehouse(
        tmp_path, n_managers=8, top1k_manager=2, with_malformed=False,
        enriched=True, with_refused=True,
    )
    return wh, universe, meta


# ---------------------------------------------------------------------------
# the agreement itself
# ---------------------------------------------------------------------------


def test_every_macro_column_is_compared_against_python(world):
    """views.sql and models/field/*, same warehouse, every column, both cohorts.

    Not three quantities on one cohort: everything ``sem_elite_ownership``
    publishes, on every cohort whose squads the loader accepted. The column-set
    assertion at the top is load-bearing -- it is what stops the macro growing
    a column this file does not check.
    """
    wh, universe, _ = world
    snap = wh.snapshot_at(T_DECIDE)

    published = set(_macro(wh, "elite").columns)
    assert published == KEY_COLUMNS | MEASURED_COLUMNS, (
        "sem_elite_ownership's column set moved. A column may be ADDED, but it "
        "must arrive with a Python twin in _model_rows and a name in "
        "MEASURED_COLUMNS -- an uncompared column is an unpinned definition, "
        f"and that is how the last five drifted. Unexpected: "
        f"{sorted(published - (KEY_COLUMNS | MEASURED_COLUMNS))}; missing: "
        f"{sorted((KEY_COLUMNS | MEASURED_COLUMNS) - published)}"
    )

    for cohort, expected_n in (("elite", 6), ("top1k", 2)):
        observed, note = load_observed_squads(snap, SEASON, universe, cohort)
        assert observed is not None, note
        assert observed.dropped == 0, "this fixture's accepted cohorts are clean"
        rates = measure_cohort(snap, SEASON, universe, cohort)
        model = _model_rows(observed, rates, universe)
        sql = _macro(wh, cohort)

        assert rates.n_managers == observed.n == expected_n, (
            f"{cohort}: the Python layers disagree on the DENOMINATOR"
        )
        assert set(sql["code"].astype(int)) == set(model), (
            f"{cohort}: the two layers do not even hold the same players"
        )

        for _, row in sql.iterrows():
            code = int(row["code"])
            want = model[code]
            for col in COUNT_COLUMNS:
                assert int(row[col]) == want[col], (
                    f"{cohort} code {code}: SQL {col}={row[col]} vs model "
                    f"{want[col]}"
                )
            assert str(row["web_name"]) == want["web_name"], (
                f"{cohort} code {code}: the two layers name a different player"
            )
            for col in ("eo_units", "own_pct", "captain_pct", "eo_pct"):
                assert float(row[col]) == pytest.approx(want[col], abs=1e-9), (
                    f"{cohort} code {code}: SQL {col}={row[col]} vs model "
                    f"{want[col]}"
                )


def test_the_shares_sum_to_the_shape_of_a_squad(world):
    """Every squad holds 15, starts 11, captains 1 -- so the shares say so.

    Three one-line identities that no amount of per-player agreement implies,
    because both layers could be wrong the same way. ``start_share`` in
    particular has no column in the macro to be compared against, and its only
    other appearance in this file is a `>` in an inequality that a systematic
    undercount satisfies just as happily.
    """
    wh, universe, _ = world
    snap = wh.snapshot_at(T_DECIDE)
    for cohort in ("elite", "top1k"):
        rates = measure_cohort(snap, SEASON, universe, cohort)
        assert rates.ownership.sum() == pytest.approx(SQUAD_SIZE)
        assert rates.start_share.sum() == pytest.approx(XI_SIZE), (
            f"{cohort}: the cohort does not start eleven players per manager"
        )
        assert rates.captain_share.sum() == pytest.approx(1.0)


def test_bench_boost_is_where_start_share_and_started_by_part_company(world):
    """The macro's ``started_by`` and the model's ``start_share`` differ, exactly.

    ``started_by`` counts a stored multiplier of 1 or more; ``start_share``
    counts the API's starting eleven. They are the same number for every
    manager who did not play Bench Boost and differ by that manager's four
    outfield-bench... by his whole bench, for the one who did. Pinning the gap
    to exactly ``SQUAD_SIZE - XI_SIZE`` is what makes both definitions
    testable at once: widen the XI and the gap shrinks, tighten the multiplier
    test and it grows.
    """
    wh, universe, meta = world
    snap = wh.snapshot_at(T_DECIDE)
    assert meta["chips"][meta["bboost_entry"]] == "bboost"

    # top1k played no chips at all, so the two definitions must coincide there.
    top = measure_cohort(snap, SEASON, universe, "top1k")
    sql_top = _macro(wh, "top1k")
    for _, row in sql_top.iterrows():
        p = universe.index_of(int(row["code"]))
        assert int(row["started_by"]) == pytest.approx(
            top.start_share[p] * top.n_managers
        ), "a chip-free cohort must start exactly the players it plays"

    elite = measure_cohort(snap, SEASON, universe, "elite")
    sql_elite = _macro(wh, "elite")
    gap = int(sql_elite["started_by"].sum()) - round(
        float(elite.start_share.sum()) * elite.n_managers
    )
    assert gap == SQUAD_SIZE - XI_SIZE, (
        "the only cohort member scoring outside the XI is the Bench Boost "
        f"manager, so the gap is his bench and nothing else; got {gap}"
    )


def test_eo_is_the_multiplier_sum_not_the_chip_rate_reconstruction(world):
    """The old model formula and the canonical one are not the same number.

    One fixture manager plays Triple Captain and one plays Bench Boost, and the
    fixture writes the multipliers the API would return (3 for the armband, 1
    across the boosted bench). The retired formula spread the cohort's 3xc
    *rate* over its whole captain distribution -- charging every captained
    player a fraction of a chip nobody played on them -- and had no term at all
    for a bench that scores.
    """
    wh, universe, _ = world
    snap = wh.snapshot_at(T_DECIDE)
    rates = measure_cohort(snap, SEASON, universe, "elite")

    retired = (rates.start_share + rates.captain_share
               + rates.captain_share * float(rates.chip_rates.get("3xc", 0.0)))
    assert not np.allclose(rates.eo(), retired), (
        "the two formulas coincide here, so this fixture cannot tell them apart"
    )
    # The canonical one is exact: total EO units divided by managers.
    observed, _ = load_observed_squads(snap, SEASON, universe, "elite")
    assert rates.eo().sum() == pytest.approx(
        observed.multipliers.sum() / observed.n
    )
    # ...and a benched player carries ownership without scoring exposure.
    benched = np.flatnonzero(rates.ownership > rates.start_share)
    assert benched.size, "no fixture squad benched anyone"
    assert (rates.eo()[benched] < rates.ownership[benched]).any()
    # ...unless Bench Boost paid for it, which is the case the retired formula
    # could not express: owned, outside the XI, and scoring all the same.
    boosted = np.flatnonzero(
        (rates.ownership > rates.start_share)
        & (rates.eo() >= rates.ownership - 1e-12)
    )
    assert boosted.size, "no fixture squad boosted its bench"


def test_the_panel_reports_the_same_eo_as_the_macro(world, tmp_path):
    """The panel is a reader of the definition, never a second implementation."""
    wh, universe, _ = world
    path = wh.path
    wh.close()

    res = run_script(
        "ownership_eo", {"season": SEASON, "cohort": "elite", "limit": 200,
                         "coverage": False}, db=path,
    ).result
    assert res.get("empty") is not True

    wh2 = type(wh)(path)
    snap = wh2.snapshot_at(dt.datetime.now(UTC))
    rates = measure_cohort(snap, SEASON, universe, "elite")
    assert res["cohort"] == "elite"
    assert res["cohort_n"] == rates.n_managers == 6

    eo = rates.eo()
    reported = {r["code"]: r for r in res["rows"] if r["elite_eo_pct"] is not None}
    assert reported, "the panel reported no cohort EO at all"
    for code, row in reported.items():
        p = universe.index_of(int(code))
        assert row["elite_eo_pct"] == pytest.approx(eo[p] * 100.0, abs=0.05), (
            f"panel and model disagree on EO for code {code}"
        )
        assert row["elite_own_pct"] == pytest.approx(
            rates.ownership[p] * 100.0, abs=0.05
        )
    # Every player the cohort actually holds is reported, none invented.
    assert {universe.index_of(int(c)) for c in reported} == set(
        np.flatnonzero(rates.ownership > 0)
    )
    # The panel names the cohort it is reporting and does not blend the others
    # into its denominator -- the bug it was built to stop repeating.
    assert "6 crawled managers" in res["cohort_note"]
    assert "top1k n=2" in res["cohort_note"]
    wh2.close()


# ---------------------------------------------------------------------------
# the point-in-time boundary, which is where the two languages drift
# ---------------------------------------------------------------------------


def test_the_python_and_sql_cohort_rules_assign_the_same_managers(world):
    """B8: one manager, two crawls, one cohort -- in both languages."""
    wh, universe, meta = world
    snap = wh.snapshot_at(T_DECIDE)

    python_side = resolve_cohorts(snap)
    sql_side = {
        int(r["entry_id"]): str(r["cohort"])
        for _, r in wh.sql(
            "SELECT entry_id, cohort FROM sem_manager_cohort(?)", [T_DECIDE]
        ).iterrows()
    }
    assert python_side == sql_side, "the two cohort rules classify differently"
    assert python_side[meta["boundary_entry"]] == "top1k", (
        "curation outranked the standings sample"
    )

    # And the denominators stay disjoint and exhaustive over what loaded.
    elite, _ = load_observed_squads(snap, SEASON, universe, "elite")
    top1k, _ = load_observed_squads(snap, SEASON, universe, "top1k")
    assert set(elite.entry_ids).isdisjoint(top1k.entry_ids)
    assert elite.n + top1k.n == len(meta["entry_ids"])
    for cohort, sample in (("elite", elite), ("top1k", top1k)):
        assert int(_macro(wh, cohort).iloc[0]["n_managers"]) == sample.n


def test_a_manager_row_landing_exactly_on_the_instant_is_visible(world):
    """AT OR BEFORE ``p_as_of``. The boundary is INCLUSIVE, in both languages.

    ``store/views.sql`` says so in prose above ``sem_manager_cohort`` and
    ``Snapshot.table`` implements the same ``as_of <= ?``; neither claim was
    reachable from a fixture whose every ``dim_manager`` row sat strictly
    before the snapshot. Here entry 105 is curated from the start and is found
    by the standings sampler at EXACTLY the decision instant, so ``<`` instead
    of ``<=`` moves him out of top1k and into elite -- in SQL only, because
    Python reads the boundary correctly. One character, two denominators, and
    nothing else in the warehouse changes.
    """
    wh, universe, meta = world
    entry = meta["boundary_entry"]
    assert meta["boundary_as_of"] == T_DECIDE, "the fixture lost the boundary row"

    on_the_instant = wh.sql(
        "SELECT cohort FROM sem_manager_cohort(?) WHERE entry_id = ?",
        [T_DECIDE, entry],
    )
    assert str(on_the_instant.iloc[0]["cohort"]) == "top1k", (
        "a source row stamped exactly at p_as_of must be visible: 'at or "
        "before' is inclusive and every reader in this repo depends on it"
    )
    assert resolve_cohorts(wh.snapshot_at(T_DECIDE))[entry] == "top1k"

    # A microsecond earlier the row genuinely has not happened yet, and BOTH
    # layers say elite. This is the half that proves the assertion above is
    # about the boundary and not about the rule.
    just_before = T_DECIDE - dt.timedelta(microseconds=1)
    assert str(wh.sql(
        "SELECT cohort FROM sem_manager_cohort(?) WHERE entry_id = ?",
        [just_before, entry],
    ).iloc[0]["cohort"]) == "elite"
    assert resolve_cohorts(wh.snapshot_at(just_before))[entry] == "elite"

    # And the denominators move together with it, in both languages.
    for as_of, n_elite, n_top1k in ((T_DECIDE, 6, 2), (just_before, 7, 1)):
        snap = wh.snapshot_at(as_of)
        for cohort, expected in (("elite", n_elite), ("top1k", n_top1k)):
            assert int(_macro(wh, cohort, as_of).iloc[0]["n_managers"]) == expected
            observed, note = load_observed_squads(snap, SEASON, universe, cohort)
            assert observed is not None and observed.n == expected, note


def test_a_re_crawl_supersedes_the_earlier_pick_row(world):
    """Latest wins. The armband moved after the first read, and both layers know.

    Real crawls re-read a locked gameweek -- a captain ruled out late has his
    armband moved by the manager, and the row we already stored is stale. Both
    the macro's ``mp`` dedupe and ``Snapshot.table`` resolve that with
    ``ORDER BY as_of DESC``, and an inverted one is invisible to any fixture
    where each pick appears once.
    """
    wh, universe, meta = world
    snap = wh.snapshot_at(T_DECIDE)
    rc = meta["recrawl"]
    stale, live = int(rc["stale_captain"]), int(rc["live_captain"])

    observed, _ = load_observed_squads(snap, SEASON, universe, "elite")
    i = observed.entry_ids.tolist().index(rc["entry"])
    assert int(observed.slots[i, observed.captain_slot[i]]) == live, (
        "the loader kept the superseded armband"
    )

    sql = _macro(wh, "elite").set_index("code")
    assert int(sql.loc[int(universe.codes[live]), "captained_by"]) >= 1
    # Nobody in the elite cohort captains the stale pick, so the count is zero
    # -- and an ASC dedupe puts it back at one.
    assert int(sql.loc[int(universe.codes[stale]), "captained_by"]) == 0, (
        "SQL is reading the first crawl rather than the last"
    )


def test_a_pick_from_a_later_crawl_is_invisible(world):
    """The GW2 crawl re-read GW1; at the GW1 decision instant we cannot see it.

    A leak here moves effective ownership without moving ownership, which is
    the hardest kind to notice by eye and the exact shape of the leakage the
    warehouse's point-in-time reads exist to prevent. Anchored on both sides of
    the later crawl rather than on agreement, because both layers would leak
    together and agree while doing it.
    """
    wh, universe, meta = world
    fp = meta["future_pick"]
    code = int(universe.codes[fp["player"]])

    def units(as_of) -> float:
        row = _macro(wh, "elite", as_of).set_index("code").loc[code]
        return float(row["eo_units"])

    before = units(T_DECIDE)
    after = units(fp["as_of"])
    assert after - before == pytest.approx(
        fp["mult_later"] - fp["mult_at_t_decide"]
    ), (
        "the later crawl's multiplier is either leaking backwards into the "
        "decision instant or never arriving at all"
    )

    snap = wh.snapshot_at(T_DECIDE)
    observed, _ = load_observed_squads(snap, SEASON, universe, "elite")
    i = observed.entry_ids.tolist().index(fp["entry"])
    assert int(observed.multipliers[i, fp["slot0"]]) == fp["mult_at_t_decide"], (
        "the Python loader read a pick row stamped after the snapshot"
    )


def test_the_reader_sees_the_latest_player_identity(world):
    """``dim_player`` is deduped latest-wins too, and web_name is what is read."""
    wh, universe, meta = world
    code = meta["stale_name_code"]
    name = _macro(wh, "elite").set_index("code").loc[code, "web_name"]
    assert str(name) == str(universe.web_name[universe.index_of(code)])
    assert "Stale" not in str(name), "the macro reported a superseded identity"


# ---------------------------------------------------------------------------
# holes in the crawl: counted, visible, never invented
# ---------------------------------------------------------------------------


def test_the_loader_refuses_every_broken_shape_and_says_so(world):
    """Four ways a crawled squad can be broken; four refusals, and a reason.

    Each of these is a squad SQL counts (an entry with stored pick rows) and
    the loader must not serve: short, holed, mis-slotted, unresolvable. They
    carry no ``dim_manager`` row on purpose, so they arrive as ``unclassified``
    and the elite/top1k comparisons above are unaffected by them.

    Two of the four exist to catch a *repair*: deleting the bench-keeper
    position check, or filling a missing multiplier with a zero, each turns one
    of these into an accepted squad and this assertion into a failure.
    """
    wh, universe, meta = world
    snap = wh.snapshot_at(T_DECIDE)
    refused = meta["refused"]
    assert len(refused) == 4

    observed, reason = load_observed_squads(snap, SEASON, universe, "unclassified")
    assert observed is None, (
        "the loader served a squad it cannot lay into the 15-slot contract: "
        f"{None if observed is None else observed.entry_ids.tolist()}"
    )
    assert f"all {len(refused)} crawled squads" in reason, reason

    # SQL sees them, counts them, and names the cohort -- the halves must not
    # silently drift into each other's job.
    sql = _macro(wh, "unclassified", resolved_only=False)
    assert int(sql.iloc[0]["n_managers"]) == len(refused)
    entries = wh.sql(
        """
        SELECT DISTINCT entry_id FROM fact_manager_pick
        WHERE as_of <= ? AND entry_id NOT IN (
            SELECT entry_id FROM sem_manager_cohort(?))
        """,
        [T_DECIDE, T_DECIDE],
    )
    assert sorted(int(e) for e in entries["entry_id"]) == sorted(refused.values())


def test_a_missing_multiplier_is_a_hole_not_a_zero(world):
    """The crawl lost one multiplier. Neither layer may invent a number for it.

    Python refuses the squad (covered above); SQL keeps the row visible and
    reads the unknown as no scoring exposure, which it states. The player in
    the holed slot is owned by nobody else in the warehouse, so his
    ``(cohort, code)`` group is that single NULL row and nothing else -- which
    is what makes a ``sum()`` that has lost its ``coalesce`` return NULL where
    the contract promises a number.
    """
    wh, universe, meta = world
    code = meta["null_multiplier_code"]
    row = _macro(wh, "unclassified").set_index("code").loc[code]

    assert int(row["owned_by"]) == 1, "the holed pick stopped being owned"
    assert pd.notna(row["eo_units"]), (
        "eo_units went NULL. A missing multiplier is a hole in the crawl, and "
        "the macro's answer to a hole is a visible zero -- a NULL here "
        "propagates into every consumer's arithmetic as a silent absence"
    )
    assert float(row["eo_units"]) == 0.0
    assert pd.notna(row["eo_pct"]) and float(row["eo_pct"]) == 0.0
    assert int(row["benched_by"]) == 1 and int(row["started_by"]) == 0

    # And the loader really did refuse that squad rather than fill the hole.
    snap = wh.snapshot_at(T_DECIDE)
    for cohort in ("elite", "top1k", "unclassified"):
        observed, _ = load_observed_squads(snap, SEASON, universe, cohort)
        if observed is not None:
            assert meta["refused"]["null_multiplier"] not in observed.entry_ids


def test_owned_by_counts_managers_not_pick_rows(world):
    """One manager holding two unresolvable ids is ONE owner of the NULL code.

    A pick whose ``element_id`` resolves to no code groups under a NULL code --
    counted, visible, excludable, never dropped. That group is the one place in
    the warehouse where a single entry contributes more than one row to a
    single ``(cohort, code)``, so it is the one place ``owned_by``'s DISTINCT
    does any work, and the only place dropping it can be seen.
    """
    wh, universe, meta = world
    sql = _macro(wh, "unclassified", resolved_only=False)
    null_code = sql[sql["code"].isna()]
    assert len(null_code) == 1, "the unresolvable picks were dropped, not grouped"
    row = null_code.iloc[0]

    ghosts = meta["ghost_elements"]
    assert len(ghosts) == 2, "the fixture no longer plants two ghosts on one entry"
    assert int(row["owned_by"]) == 1, (
        f"{len(ghosts)} unresolvable picks belonging to one manager were "
        "counted as that many owners; owned_by counts managers, not rows"
    )
    assert float(row["own_pct"]) == pytest.approx(
        100.0 / int(row["n_managers"])
    )


def test_the_only_disagreement_is_a_squad_the_loader_refused(tmp_path):
    """A 14-pick squad in the ELITE pool: SQL counts it, the loader refuses it.

    Pinned because it is the ONE way the two denominators can differ for a
    cohort under comparison, and a silent difference here would look exactly
    like the drift this module exists to catch.
    """
    wh, universe, meta = build_warehouse(
        tmp_path, n_managers=8, top1k_manager=2, with_malformed=True
    )
    snap = wh.snapshot_at(T_DECIDE)
    observed, note = load_observed_squads(snap, SEASON, universe, "elite")
    sql_n = int(_macro(wh, "elite").iloc[0]["n_managers"])

    assert observed.dropped == 1 and "1 dropped" in note
    assert sql_n == observed.n + observed.dropped, (
        "the difference between the two denominators is not fully explained"
    )


# ---------------------------------------------------------------------------
# an unreadable crawl is not an empty one
# ---------------------------------------------------------------------------


class _UnreadableSnapshot:
    """A Snapshot whose ``dim_manager`` read fails, as a locked file's would."""

    as_of = T_DECIDE

    def table(self, *args, **kwargs):
        raise RuntimeError(
            "IO Error: Could not set lock on file 'fpl.duckdb': "
            "held by another process"
        )


def test_an_unreadable_dim_manager_is_not_an_empty_world():
    """A broken read must fail loudly instead of reporting an empty crawl.

    ``resolve_cohorts`` used to answer a failed read with ``{}``, which
    ``load_observed_squads`` then reported to the user as "no crawled 'elite'
    squads for <season> GW<g>" and the ownership panel's ``cohort_note``
    repeated verbatim: a broken read wearing an empty world's clothes. Worse,
    for ``cohort='unclassified'`` the ``~isin({})`` mask is all-True, so every
    crawled squad in the warehouse would be reported as a crawl bug. The SQL
    twin has no such fallback and raises, so the swallow was drift the
    agreement tests above cannot see.
    """
    with pytest.raises(CohortReadError) as exc:
        resolve_cohorts(_UnreadableSnapshot())
    assert "dim_manager" in str(exc.value)
    assert "unreadable" in str(exc.value)


def test_a_genuinely_empty_world_still_reports_as_empty(tmp_path):
    """Nobody tracked yet is a real state of the season, and stays a quiet one."""
    wh, universe, _ = build_warehouse(
        tmp_path, n_managers=0, with_flow=False, with_malformed=False
    )
    snap = wh.snapshot_at(T_DECIDE)
    assert resolve_cohorts(snap) == {}
    observed, reason = load_observed_squads(snap, SEASON, universe, "elite")
    assert observed is None
    assert "has no rows" in reason, reason
    assert measure_cohort(snap, SEASON, universe, "elite").measured is False


def test_the_provenance_string_does_not_depend_on_the_thread_count(world):
    """``sources`` is compared for equality by consumers, so it must be stable.

    ``string_agg`` has no order of its own: over a parallelised scan it returns
    its parts in whatever order the threads finished in, so an entry with more
    than one source returned a different provenance string at ``threads=8``
    than at ``threads=1``. ``Snapshot.table`` documents and fixes exactly this
    trap for row order (``store/warehouse.py``); this is the aggregate twin.

    The fixture writes this entry's nine sources in REVERSE lexical order, so
    the assertion does not depend on catching the scheduler in the act: an
    aggregate with no ORDER BY reports them in scan order, which is backwards.
    """
    wh, universe, meta = world
    entry = meta["multi_source_entry"]
    expected = "|".join(meta["multi_sources"])

    seen = set()
    for threads in (1, 4, 8):
        wh.sql(f"SET threads TO {threads}")
        row = wh.sql(
            "SELECT sources, n_sources FROM sem_manager_cohort(?) WHERE entry_id = ?",
            [T_DECIDE, entry],
        ).iloc[0]
        assert int(row["n_sources"]) == len(meta["multi_sources"])
        seen.add(str(row["sources"]))
    assert seen == {expected}, (
        "provenance is not in a defined order, so it will not compare equal to "
        f"itself between runs: {sorted(seen)}"
    )


def test_the_fixture_still_carries_every_complication(world):
    """The fixture is the guard rail. If it goes boring again, say so here.

    Every assertion above is only as strong as the world it runs on, and the
    previous version of this module failed precisely because its fixture had
    none of these: the mutations had nothing to bite on. Each check below is a
    complication some later edit could quietly drop while every other test in
    this file kept passing.
    """
    wh, universe, meta = world

    def scalar(sql, params=()):
        return wh.sql(sql, list(params)).iloc[0, 0]

    # A dim_manager row landing EXACTLY on the decision instant.
    assert int(scalar(
        "SELECT count(*) FROM dim_manager WHERE as_of = ?", [T_DECIDE]
    )) == 1, "nothing sits on the point-in-time boundary any more"

    # A pick seen twice, and a pick from a crawl later than the decision.
    assert int(scalar(
        """
        SELECT count(*) FROM (
            SELECT entry_id FROM fact_manager_pick
            GROUP BY entry_id, season, gw, element_id HAVING count(*) > 1)
        """
    )) >= 1, "no pick row is superseded, so latest-wins is untested"
    assert int(scalar(
        "SELECT count(*) FROM fact_manager_pick WHERE as_of > ?", [T_DECIDE]
    )) >= 1, "no pick lies in the future, so the leak guard is untested"

    # A hole where a multiplier should be, and ids the universe cannot resolve.
    assert int(scalar(
        "SELECT count(*) FROM fact_manager_pick WHERE multiplier IS NULL"
    )) == 1
    assert int(scalar(
        """
        SELECT count(*) FROM fact_manager_pick p
        WHERE NOT EXISTS (SELECT 1 FROM dim_player d
                          WHERE d.season = p.season AND d.element_id = p.element_id)
        """
    )) == len(meta["ghost_elements"])

    # A superseded player identity, and a Bench Boost that really boosts.
    assert int(scalar(
        "SELECT count(*) FROM dim_player WHERE web_name = 'Stale Identity'"
    )) == 1
    boosted = wh.sql(
        "SELECT multiplier FROM fact_manager_pick "
        "WHERE entry_id = ? AND slot > ? AND as_of <= ?",
        [meta["bboost_entry"], XI_SIZE, T_DECIDE],
    )
    assert len(boosted) == SQUAD_SIZE - XI_SIZE
    assert (boosted["multiplier"].astype(int) == 1).all(), (
        "the Bench Boost squad no longer scores off its bench, so start-share "
        "and effective ownership cannot part company anywhere in this fixture"
    )
    assert int(scalar(
        "SELECT count(*) FROM fact_manager_pick WHERE multiplier = 3"
    )) == 1, "no Triple Captain, so the retired chip-rate formula agrees again"

    # And the universe both layers speak is still the toy one.
    assert universe.n_players == toy()[0].n_players
    assert GW2_DEADLINE > T_DECIDE, "the later-crawl fixture is not in the future"
