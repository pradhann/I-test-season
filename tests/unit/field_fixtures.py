"""Shared fixture builder for the field-model tests.

Builds a real (temporary) DuckDB warehouse holding a toy universe and a small
crawled cohort, because the thing under test is precisely the seam between the
warehouse's point-in-time reads and the sampler: a mocked frame would test the
mock. The squads are constructed to be *legal* -- 2/5/5/3, one GK starting,
valid formation, 3-per-club -- so legality assertions downstream are meaningful.
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
GW2_DEADLINE = dt.datetime(2026, 8, 28, 17, 30, tzinfo=UTC)
#: First ownership/state poll, pre-GW1.
T_POOL = GW1_DEADLINE - dt.timedelta(days=3)
#: The GW2 decision instant: after GW1 locked, before GW2 does.
T_DECIDE = GW1_DEADLINE + dt.timedelta(days=4)

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


def build_warehouse(
    tmp_path,
    *,
    n_managers: int = 8,
    with_flow: bool = True,
    with_malformed: bool = True,
    top1k_manager: int | None = None,
):
    """A warehouse with the toy universe and ``n_managers`` crawled GW1 squads.

    Returns ``(wh, universe, meta)`` where ``meta`` records what was planted:
    entry ids, the flow's sold/bought player indices, chip assignments.
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
        for s, p in enumerate(slots):
            picks.append({
                "entry_id": eid, "season": SEASON, "gw": 1,
                "element_id": p + 1, "slot": s + 1,
                "multiplier": (2 if s == cap_slot else (1 if s < 11 else 0)),
                "is_captain": s == cap_slot, "is_vice_captain": s == vice_slot,
                "as_of": GW1_DEADLINE,
            })
        chip = meta["chips"].get(eid)
        if chip:
            chips.append({"entry_id": eid, "season": SEASON, "gw": 1,
                          "chip": chip, "as_of": GW1_DEADLINE})

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
    wh.append("fact_manager_pick", pd.DataFrame(picks))
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
