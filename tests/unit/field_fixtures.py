"""Shared fixture builder for the field-model tests.

Builds a real (temporary) DuckDB warehouse holding a toy universe and a small
crawled cohort, because the thing under test is precisely the seam between the
warehouse's point-in-time reads and the sampler: a mocked frame would test the
mock. The squads are constructed to be *legal* -- 2/5/5/3, one GK starting,
valid formation, 3-per-club -- so legality assertions downstream are meaningful.

Two opt-in modes make the warehouse *representative* rather than merely legal.
Both default off so the sampler and drift tests keep the small, predictable
world they assert exact counts against.

``enriched=True`` adds the things a real crawl contains and a hand-built eight
squads do not -- a re-crawl that supersedes earlier pick rows, a manager row
stamped EXACTLY on the snapshot instant, a Bench Boost (benched picks scoring
1, so start-share and multiplier-share genuinely part company), a pick row from
a LATER crawl that must stay invisible, and a superseded ``dim_player`` name.
Each one exists because a definition that only has to agree with itself on
boring data is not pinned at all: ``tests/unit/test_field_eo_agreement.py``
compares the SQL macro and the Python model column by column on this world, and
every item above is a place the two can drift while a boring fixture reports
agreement.

``with_refused=True`` adds four squads the Python loader must REFUSE, none of
them carrying a ``dim_manager`` row -- so they land in the ``unclassified``
cohort and leave the elite/top1k denominators the agreement test compares
untouched. One is short, one has a hole where a multiplier should be, one has a
non-keeper in the API's bench-keeper slot, and one names element ids the
universe has never heard of. They are the crawl's four ways of being broken,
and the loader's contract is to drop each and say so rather than repair it.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from fpl_edge.ingest.rivals import schema as rivals_schema
from fpl_edge.sim.synthetic import toy_world
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc
SEASON = "2026-27"
GW1_DEADLINE = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
# GW2 must be in the FUTURE for this fixture to mean what it says: GW1 locked,
# GW2 upcoming, picks seeded for GW1 only. It was hardcoded to 2026-08-28
# 17:30Z, and at 17:30:01 that stopped being true -- `last_locked_gw` started
# answering 2, found no GW2 picks, and every test comparing a panel (which has
# no as_of parameter and so always reads the real clock) against the Python
# layer began failing. A fixture that encodes "hasn't happened yet" as a fixed
# date is a test that passes until it doesn't, for reasons no diff explains.
GW2_DEADLINE = max(
    dt.datetime(2026, 8, 28, 17, 30, tzinfo=UTC),
    dt.datetime.now(UTC) + dt.timedelta(days=30),
)
#: First ownership/state poll, pre-GW1.
T_POOL = GW1_DEADLINE - dt.timedelta(days=3)
#: The GW2 decision instant: after GW1 locked, before GW2 does.
T_DECIDE = GW1_DEADLINE + dt.timedelta(days=4)
#: A re-crawl of the SAME gameweek two hours after the deadline. Real crawls
#: re-read squads (armbands move when a captain is ruled out late), so the same
#: (entry, season, gw, element_id) exists twice with different `as_of` and the
#: newest one is the truth. Both layers dedupe latest-wins; ``enriched=True``
#: plants the rows that make an inverted ORDER BY visible.
T_RECRAWL = GW1_DEADLINE + dt.timedelta(hours=2)

N_TEAMS = 8
PER_TEAM = 14  # toy_world layout: j 0-1 GK, 2-6 DEF, 7-11 MID, 12-13 FWD


def toy():
    """(universe, ownership, captaincy, xp) from the sim's toy world."""
    u, _, eo, cap, xp = toy_world(seed=7)
    return u, eo, cap, xp


def _idx(team: int, j: int) -> int:
    return (team % N_TEAMS) * PER_TEAM + j


def state_frame(universe, ownership, as_of, transfers_in, transfers_out) -> pd.DataFrame:
    """One ``fact_player_state`` poll. Module-level so tests can add polls.

    The transfer counters are per-gameweek and reset at every deadline, so a
    test that wants to exercise the two-poll difference regime has to plant two
    polls on the same side of a deadline -- which it cannot do without this.
    """
    return pd.DataFrame([
        {"season": SEASON, "code": int(universe.codes[p]), "element_id": p + 1,
         "price_tenths": int(universe.price_tenths[p]),
         "selected_by_pct": float(ownership[p] * 100.0), "status": "a",
         "chance_of_playing_next_round": None,
         "transfers_in_event": int(transfers_in[p]),
         "transfers_out_event": int(transfers_out[p]),
         "as_of": as_of}
        for p in range(universe.n_players)
    ])


def manager_squad(i: int) -> tuple[list[int], int, int]:
    """A legal squad for manager ``i`` in FPL slot order (0-based indices).

    Slots 0-10 are a 4-4-2 XI (GK, 4 DEF, 4 MID, 2 FWD), slot 11 the bench
    keeper, 12-14 the outfield bench. Spread across clubs so no club exceeds 2.
    Squads vary with ``i`` so the cohort has real co-ownership structure.
    """
    gk_a, gk_b = _idx(i, 0), _idx(i + 1, 1)
    defs = [_idx(i + k, 2 + (i + k) % 5) for k in range(5)]
    mids = [_idx(i + 2 + k, 7 + (i + k) % 5) for k in range(5)]
    fwds = [_idx(i + 5, 12 + i % 2), _idx(i + 6, 12 + (i + 1) % 2), _idx(i + 7, 12)]
    slots = [gk_a] + defs[:4] + mids[:4] + fwds[:2] + [gk_b, defs[4], mids[4], fwds[2]]
    captain_slot, vice_slot = 5, 9  # first MID, first FWD -- both starters
    return slots, captain_slot, vice_slot


def _pick_row(
    eid: int,
    slot0: int,
    player: int,
    *,
    multiplier,
    captain: bool,
    vice: bool,
    as_of=GW1_DEADLINE,
    element_id: int | None = None,
) -> dict:
    """One ``fact_manager_pick`` row. ``slot0`` is 0-based; the API is 1-based.

    ``element_id`` overrides the universe-derived id so a fixture can plant a
    pick the universe cannot resolve, and ``multiplier`` may be ``None`` so it
    can plant a hole where the crawl lost one.
    """
    return {
        "entry_id": eid, "season": SEASON, "gw": 1,
        "element_id": player + 1 if element_id is None else element_id,
        "slot": slot0 + 1, "multiplier": multiplier,
        "is_captain": captain, "is_vice_captain": vice, "as_of": as_of,
    }


def build_warehouse(
    tmp_path,
    *,
    n_managers: int = 8,
    with_flow: bool = True,
    with_malformed: bool = True,
    top1k_manager: int | None = None,
    enriched: bool = False,
    with_refused: bool = False,
):
    """A warehouse with the toy universe and ``n_managers`` crawled GW1 squads.

    Returns ``(wh, universe, meta)`` where ``meta`` records what was planted:
    entry ids, the flow's sold/bought player indices, chip assignments, and --
    under the two opt-in modes documented in the module docstring -- the exact
    entry ids and player indices of every complication, so a test can assert
    against them by name instead of rediscovering them.
    """
    u, eo, cap, xp = toy()
    wh = Warehouse(tmp_path / "field_test.duckdb")
    rivals_schema.migrate(wh)

    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 1, "deadline_utc": GW1_DEADLINE,
         "is_finished": False, "as_of": T_POOL},
        {"season": SEASON, "gw": 2, "deadline_utc": GW2_DEADLINE,
         "is_finished": False, "as_of": T_POOL},
    ]))
    wh.append("dim_player", pd.DataFrame([
        {"season": SEASON, "code": int(u.codes[p]), "element_id": p + 1,
         "web_name": str(u.web_name[p]), "position": int(u.position[p]),
         "team_code": int(u.team_code[p]), "as_of": T_POOL}
        for p in range(u.n_players)
    ]))

    def state(as_of, ti, to):
        return state_frame(u, eo, as_of, ti, to)

    zeros = np.zeros(u.n_players, dtype=int)
    wh.append("fact_player_state", state(T_POOL, zeros, zeros))

    # -- the cohort -----------------------------------------------------------
    squads = {i: manager_squad(i) for i in range(n_managers)}
    meta = {"entry_ids": [100 + i for i in range(n_managers)],
            "squads": squads, "chips": {100: "wildcard", 101: "3xc"}}

    if enriched:
        if n_managers < 7:
            raise ValueError("enriched=True needs at least 7 managers to plant "
                             "its complications on distinct entries")
        # Bench Boost is the chip that makes ownership, start-share and
        # effective ownership three genuinely different numbers: its benched
        # picks score 1, so they carry scoring exposure while sitting outside
        # the XI. Without it in the fixture, EO == start + captain everywhere
        # and a start-share bug reads as agreement.
        meta["chips"][103] = "bboost"
        meta["bboost_entry"] = 103

    managers, picks, chips = [], [], []
    for i in range(n_managers):
        eid = 100 + i
        source = ("top1k:2026-27:gw1:rank1"
                  if top1k_manager is not None and i == top1k_manager
                  else "snowball:1")
        managers.append({"entry_id": eid, "player_name": f"M{i}",
                         "entry_name": f"Team {i}", "region": None,
                         "years_active": None, "favourite_team_id": None,
                         "started_event": 1, "source": source, "as_of": T_POOL})
        slots, cap_slot, vice_slot = squads[i]
        chip_played = meta["chips"].get(eid)
        # The API resolves the chip into the multiplier it returns: Triple
        # Captain makes the armband 3, Bench Boost lifts the bench to 1. The
        # fixture writes what the API would write, so multiplier-derived
        # effective ownership and the chip table cannot contradict each other.
        cap_mult = 3 if chip_played == "3xc" else 2
        bench_mult = 1 if chip_played == "bboost" else 0
        for s, p in enumerate(slots):
            picks.append(_pick_row(
                eid, s, p,
                multiplier=(cap_mult if s == cap_slot
                            else (1 if s < 11 else bench_mult)),
                captain=s == cap_slot, vice=s == vice_slot,
            ))
        chip = chip_played
        if chip:
            chips.append({"entry_id": eid, "season": SEASON, "gw": 1,
                          "chip": chip, "as_of": GW1_DEADLINE})

    if enriched:
        # (a) A LATE CROSSING OF THE POINT-IN-TIME BOUNDARY. Entry 105 is in
        #     the curated pool from the start; the standings sampler finds him
        #     at EXACTLY T_DECIDE. Both layers read "any source row AT OR
        #     BEFORE p_as_of", so at T_DECIDE he is top1k in SQL and in Python
        #     alike -- and the moment either side turns that <= into a <, they
        #     disagree about which denominator he belongs to. Every other
        #     manager row in this fixture sits strictly before the snapshot,
        #     which is exactly why the boundary went untested.
        boundary = 105
        managers.append({
            "entry_id": boundary, "player_name": f"M{boundary - 100}",
            "entry_name": f"Team {boundary - 100}", "region": None,
            "years_active": None, "favourite_team_id": None,
            "started_event": 1, "source": "top1k:2026-27:gw1:rank7",
            "as_of": T_DECIDE,
        })
        meta["boundary_entry"] = boundary
        meta["boundary_as_of"] = T_DECIDE

        # (b) A RE-CRAWL THAT SUPERSEDES EARLIER ROWS. Entry 104's captain was
        #     ruled out after the first read, so the armband moved to his vice.
        #     The same (entry, season, gw, element_id) now exists twice and the
        #     NEWER row is the truth -- which is the only reason the dedupe
        #     needs an ORDER BY direction at all.
        recrawl = 104
        r_slots, r_cap, r_vice = squads[recrawl - 100]
        picks.append(_pick_row(recrawl, r_cap, r_slots[r_cap], multiplier=1,
                               captain=False, vice=True, as_of=T_RECRAWL))
        picks.append(_pick_row(recrawl, r_vice, r_slots[r_vice], multiplier=2,
                               captain=True, vice=False, as_of=T_RECRAWL))
        meta["recrawl"] = {
            "entry": recrawl, "as_of": T_RECRAWL,
            "stale_captain": r_slots[r_cap], "live_captain": r_slots[r_vice],
        }

        # (c) A ROW FROM A LATER CRAWL, which must stay invisible at T_DECIDE.
        #     The GW2 crawl re-read GW1 once the autosubs had resolved and
        #     returned 1 for a player who was benched at the deadline. Both
        #     layers filter as_of <= p_as_of, so at T_DECIDE the answer is
        #     still 0 -- a leak here moves EO without moving ownership, which
        #     is the hardest kind to notice.
        future = 106
        f_slots, _, _ = squads[future - 100]
        f_slot = 12  # an outfield bench slot: benched at the deadline
        picks.append(_pick_row(future, f_slot, f_slots[f_slot], multiplier=1,
                               captain=False, vice=False, as_of=GW2_DEADLINE))
        meta["future_pick"] = {
            "entry": future, "player": f_slots[f_slot], "slot0": f_slot,
            "as_of": GW2_DEADLINE, "mult_at_t_decide": 0, "mult_later": 1,
        }

        # (d) AN ENTRY WITH MANY SOURCES, written in REVERSE lexical order.
        #     string_agg has no order of its own: over a parallelised scan it
        #     returns its parts in whatever order the threads finished in, so
        #     `sources` compared unequal to itself between runs. One entry with
        #     two sources is not enough to provoke that reliably -- with nine,
        #     written backwards, an unordered aggregate reports them backwards.
        chatty = 107
        extra_sources = [f"mini_league:{k}" for k in range(9, 1, -1)]
        for k, src in enumerate(extra_sources):
            managers.append({
                "entry_id": chatty, "player_name": f"M{chatty - 100}",
                "entry_name": f"Team {chatty - 100}", "region": None,
                "years_active": None, "favourite_team_id": None,
                "started_event": 1, "source": src,
                "as_of": T_POOL + dt.timedelta(minutes=k + 1),
            })
        meta["multi_source_entry"] = chatty
        meta["multi_sources"] = sorted(extra_sources + ["snowball:1"])

        # (e) A SUPERSEDED PLAYER IDENTITY. dim_player is deduped latest-wins
        #     too, and web_name is the column a reader actually sees; an
        #     inverted dedupe there reports last month's name for every player
        #     and nothing else changes.
        stale = int(u.codes[0])
        wh.append("dim_player", pd.DataFrame([{
            "season": SEASON, "code": stale, "element_id": 1,
            "web_name": "Stale Identity", "position": int(u.position[0]),
            "team_code": int(u.team_code[0]),
            "as_of": T_POOL - dt.timedelta(days=7),
        }]))
        meta["stale_name_code"] = stale

    if with_refused:
        # Four squads the loader must REFUSE. None gets a dim_manager row, so
        # SQL files them under 'unclassified' and the elite/top1k denominators
        # the agreement test compares are untouched -- while the Python loader,
        # asked for 'unclassified', must come back with nothing and a reason.
        owned = set()
        for s_, _c, _v in squads.values():
            owned.update(s_)
        refused: dict[str, int] = {}

        # 990 -- fourteen picks. A squad with a slot missing is a squad we do
        # not have; there is nothing to interpolate.
        t_slots, t_cap, t_vice = manager_squad(3)
        for s_i, p_i in enumerate(t_slots[:14]):
            picks.append(_pick_row(990, s_i, p_i, multiplier=1,
                                   captain=s_i == t_cap, vice=s_i == t_vice))
        refused["short"] = 990

        # 991 -- a HOLE where a multiplier should be. Complete, legal, ordered,
        # every id resolvable: the ONLY thing wrong with it is that the crawl
        # lost one multiplier. Filling that hole with a zero would invent
        # "benched" out of "unknown". The holed slot holds a player nobody else
        # in the warehouse owns, so its (cohort, code) group is that one NULL
        # row and nothing else -- which is what makes a sum() that has lost its
        # coalesce return NULL instead of 0.
        t_slots, t_cap, t_vice = manager_squad(1)
        t_slots = list(t_slots)
        lonely = next(int(x) for x in np.flatnonzero(u.position == 3)
                      if int(x) not in owned and int(x) not in t_slots)
        hole = 13  # an outfield bench slot
        t_slots[hole] = lonely
        for s_i, p_i in enumerate(t_slots):
            picks.append(_pick_row(
                991, s_i, p_i,
                multiplier=(None if s_i == hole
                            else (2 if s_i == t_cap else (1 if s_i < 11 else 0))),
                captain=s_i == t_cap, vice=s_i == t_vice,
            ))
        refused["null_multiplier"] = 991
        meta["null_multiplier_player"] = lonely
        meta["null_multiplier_code"] = int(u.codes[lonely])

        # 992 -- the API's slot 12 is the bench KEEPER and the sim's layout
        # depends on it. Here it holds a defender, so the squad cannot be laid
        # into the 15-slot contract even though it has fifteen of everything.
        t_slots, t_cap, t_vice = manager_squad(5)
        t_slots = list(t_slots)
        t_slots[11], t_slots[12] = t_slots[12], t_slots[11]
        for s_i, p_i in enumerate(t_slots):
            picks.append(_pick_row(
                992, s_i, p_i,
                multiplier=(2 if s_i == t_cap else (1 if s_i < 11 else 0)),
                captain=s_i == t_cap, vice=s_i == t_vice,
            ))
        refused["bench_keeper"] = 992

        # 993 -- two element ids the universe has never heard of. SQL keeps
        # them, visible, under a NULL code (counted, never dropped); the loader
        # refuses the squad rather than serve a 13-man one. Two of them on ONE
        # entry is deliberate: it is what makes owned_by's DISTINCT do work.
        t_slots, t_cap, t_vice = manager_squad(6)
        ghosts = {12: 90_001, 13: 90_002}
        for s_i, p_i in enumerate(t_slots):
            picks.append(_pick_row(
                993, s_i, p_i,
                multiplier=(2 if s_i == t_cap else (1 if s_i < 11 else 0)),
                captain=s_i == t_cap, vice=s_i == t_vice,
                element_id=ghosts.get(s_i),
            ))
        refused["unresolvable"] = 993
        meta["ghost_elements"] = sorted(ghosts.values())

        meta["refused"] = refused

    if with_malformed:
        # 14 picks: must be dropped by the loader, never repaired.
        eid = 999
        managers.append({"entry_id": eid, "player_name": "Broken",
                         "entry_name": "Broken", "region": None,
                         "years_active": None, "favourite_team_id": None,
                         "started_event": 1, "source": "snowball:1",
                         "as_of": T_POOL})
        slots, cap_slot, vice_slot = manager_squad(3)
        for s, p in enumerate(slots[:14]):
            picks.append({"entry_id": eid, "season": SEASON, "gw": 1,
                          "element_id": p + 1, "slot": s + 1, "multiplier": 1,
                          "is_captain": s == cap_slot,
                          "is_vice_captain": s == vice_slot,
                          "as_of": GW1_DEADLINE})
        meta["malformed_entry"] = eid

    wh.append("dim_manager", pd.DataFrame(managers))
    picks_df = pd.DataFrame(picks)
    if not picks_df.empty:
        # A missing multiplier must reach the warehouse as SQL NULL, not as a
        # float NaN silently rounded into a BIGINT. The nullable dtype is the
        # whole point of the 991 fixture: the hole has to survive the round
        # trip, or the test of what the loader does with it tests nothing.
        picks_df["multiplier"] = pd.array(picks_df["multiplier"], dtype="Int64")
    wh.append("fact_manager_pick", picks_df)
    if chips:
        wh.append("fact_manager_chip", pd.DataFrame(chips))

    # -- transfer flow after GW1 locked --------------------------------------
    if with_flow:
        owned = set()
        for slots, _, _ in squads.values():
            owned.update(slots)
        mid_mask = u.position == 3
        # the most co-owned midfielder gets sold hard...
        counts = np.zeros(u.n_players, dtype=int)
        for slots, _, _ in squads.values():
            for p in slots:
                counts[p] += 1
        sold = int(np.argmax(np.where(mid_mask, counts, -1)))
        # ...for a midfielder nobody holds, priced within tolerance of him.
        afford = u.price_tenths <= u.price_tenths[sold] + 3
        cands = [p for p in np.flatnonzero(mid_mask & afford) if p not in owned]
        bought = int(cands[0])
        ti, to = zeros.copy(), zeros.copy()
        to[sold] = 200_000
        ti[bought] = 200_000
        wh.append("fact_player_state", state(T_DECIDE, ti, to))
        meta["sold"], meta["bought"] = sold, bought

    return wh, u, meta
