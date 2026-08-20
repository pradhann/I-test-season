"""FPL's own ``ep_next``: the zero-cost baseline every other provider must beat.

What the fields actually are
----------------------------
``bootstrap-static`` carries two projection-shaped strings per element:

* ``ep_next`` -- the game's own expected points for the NEXT gameweek. It is
  form-derived (recent points-per-match blended toward the fixture) and then
  scaled by the player's availability: a player flagged at 25% has his
  ``ep_next`` multiplied down. It is crude, but it is the only projection whose
  publisher also sets the prices, and it updates continuously -- every price
  change poll we already run sees a fresh value.
* ``ep_this`` -- the same quantity for the CURRENT gameweek. Once a gameweek is
  underway it stops being a projection at all, which is why this module does
  not ingest it: at any instant before the deadline, ``ep_next`` is the number
  that refers to the decision being made.

``chance_of_playing_next_round`` is the availability factor itself: an integer
percentage for flagged players and ``null`` for unflagged ones. ``null`` means
"no flag", which the game treats as fully available; it is stored here as
``p_appear = 1.0`` **only** when ``status == 'a'`` -- for any other status a
null chance is genuine ignorance and is stored as NULL.

Why ingest a number we could recompute
--------------------------------------
Because the ensemble weights providers by measured track record, and a
track record needs a stored, timestamped prediction. ``ep_next`` read live is
a projection; ``ep_next`` recomputed later from archived bootstraps is a
temptation to look at the answer. Rows land in ``fact_projection`` under
provider ``fpl_ep`` exactly like any third party, and earn (or lose) ensemble
weight the same way.

Licence: the official API, already polled by this repo for prices and flags.
No robots question arises beyond what ``fpl_api.py`` already settled.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from fpl_edge.ingest.http import Fetched, Fetcher

BASE = "https://fantasy.premierleague.com/api"
SOURCE = "projections_fpl_ep"


class FplEpError(RuntimeError):
    """bootstrap-static did not carry what this module needs."""


def fetch_bootstrap(fetcher: Fetcher | None = None) -> Fetched:
    """One GET of bootstrap-static, archived under this provider's own source."""
    owned = fetcher is None
    fetcher = fetcher or Fetcher(SOURCE, base_url=BASE)
    try:
        return fetcher.get_json("bootstrap-static/")
    finally:
        if owned:
            fetcher.close()


def next_event(bootstrap: dict[str, Any]) -> int:
    """The gameweek ``ep_next`` refers to: the event flagged ``is_next``."""
    for ev in bootstrap.get("events", ()):
        if ev.get("is_next"):
            return int(ev["id"])
    raise FplEpError(
        "no event has is_next=true; between seasons or at season end ep_next "
        "refers to nothing, and rows written anyway would be about no gameweek."
    )


def to_projection_rows(
    bootstrap: dict[str, Any], *, season: str, as_of: dt.datetime
) -> pd.DataFrame:
    """Every playable element's ``ep_next`` -> ``fact_projection`` rows.

    Keyed on the element's own ``code`` straight from the same payload -- no
    name matching, no element_id crossing between documents fetched at
    different instants.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware UTC")
    gw = next_event(bootstrap)
    rows: list[dict[str, object]] = []
    for el in bootstrap.get("elements", ()):
        if el.get("element_type") not in (1, 2, 3, 4):
            continue  # manager elements are not players
        raw = el.get("ep_next")
        if raw in (None, ""):
            continue
        xp = float(raw)
        chance = el.get("chance_of_playing_next_round")
        if chance is not None:
            p_appear: float | None = float(chance) / 100.0
        elif el.get("status") == "a":
            p_appear = 1.0
        else:
            p_appear = None
        rows.append({
            "provider": "fpl_ep", "season": season, "gw": gw,
            "code": int(el["code"]), "xp": xp,
            "xp_if_appears": None, "p_appear": p_appear, "as_of": as_of,
        })
    if not rows:
        raise FplEpError("bootstrap-static carried no elements with ep_next")
    frame = pd.DataFrame(rows)
    frame["as_of"] = pd.to_datetime(frame["as_of"], utc=True)
    return frame
