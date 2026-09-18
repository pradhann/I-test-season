"""The LiveFPL "Best 1000 Managers of All Time" list, captured once and pinned.

Provenance
----------
Source: ``https://plan.livefpl.net/elite`` -- a public, no-login leaderboard that
ranks managers by their record across 2014/15 to 2025/26 and links each name to
``fantasy.premierleague.com/entry/<id>/history``.

Fetched %Y-%m-%d. Raw page archived under ``data/raw/rivals/`` with
sha256 ``e6fa77e82692dd078a975f1cf691f7d2278b0d945cfa511bad224d014089a527``, so the list a given analysis used can be
reproduced even after the site updates.

Why pin it rather than fetch it live
------------------------------------
Three reasons, in order of importance. It is a **third-party ranking**, so its
methodology is not ours and could change without notice; pinning means a
re-run reproduces, and a change is a visible diff rather than a silent shift in
the pool. It is **someone else's server**, and re-fetching a 2MB page on every
run to extract numbers that change a few times a season is rude. And a crawl
that depends on an external site being up will fail at the deadline, which is
the one time it must not.

What this list is and is not
----------------------------
It is a **sampling frame**, not an answer. These IDs enter the candidate pool on
exactly the same footing as every other candidate, and
:mod:`fpl_edge.models.copying.skill` scores them from their own FPL history with
no credit for appearing here. That matters because the list's own ranking is
built from the same finishing ranks we are about to analyse, so treating its
order as evidence would be circular. Its only role is to point the crawl at
managers with long records instead of at random entries.

The names are user-set FPL display names -- arbitrary third-party text, carried
verbatim for identification and never interpreted.

Where the list lives
--------------------
The 1000 rows are in ``data/reference/elite_1000.json``, not in this file. They
are captured third-party data that this repo does not compute, so a change to
them should read as a data diff and not as a thousand-line code diff
(ARCHITECTURE_REVIEW.md Section 4 row 14). This module is the reader: ``top``,
``names``, and the two module attributes the previous constants were called.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

#: The captured list, one row per manager, in the source's ranking order.
#: Committed data rather than a thousand-line literal: it is third-party text
#: that this repo does not compute, and a diff to it should read as a data
#: change and not as a code change (ARCHITECTURE_REVIEW.md Section 4 row 14).
DATA_PATH = Path(__file__).resolve().parents[3] / "data" / "reference" / "elite_1000.json"


@lru_cache(maxsize=1)
def _load() -> tuple[tuple[int, str], ...]:
    """Read the pinned list once per process. Order is the file's order.

    No fallback and no default. A missing or malformed file is a broken
    checkout, and a crawl that silently sampled an empty frame would produce a
    candidate pool with no elite entries in it and say nothing.
    """
    payload = json.loads(DATA_PATH.read_text())
    return tuple((int(r["entry_id"]), str(r["name"]))
                 for r in payload["managers"])


def top(n: int) -> list[int]:
    """The first ``n`` entry IDs from the list."""
    return [e for e, _ in _load()[:n]]


def names() -> dict[int, str]:
    """entry_id -> the user-set display name the source carried."""
    return {e: n for e, n in _load()}


def __getattr__(name: str):
    """``ELITE_1000`` and ``NAMES`` stay readable as module attributes.

    They were module-level constants before the list moved to
    ``data/reference/elite_1000.json``. Serving them lazily here keeps
    ``roster.py:74`` and ``models/copying/report.py:26`` unchanged and keeps the
    file read off import time, so importing this module costs nothing until
    something actually wants the list.
    """
    if name == "ELITE_1000":
        return _load()
    if name == "NAMES":
        return names()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
