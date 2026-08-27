"""Accent-folded name matching, used wherever a curated entry ID is verified.

FPL entry IDs are assigned per season in registration order, so **a curated
list of IDs rots every August, and it rots silently**: last season's ID does
not 404, it resolves to a different real person. The only defence is to fetch
``/api/entry/{id}/`` and check that the account holder's name still matches the
name we wrote down.

This module exists so there is exactly ONE implementation of that check.
:mod:`fpl_edge.ingest.rivals.elite` had it first (and still re-exports
:func:`norm` under its old private name for
:mod:`fpl_edge.interfaces.qa`); :mod:`fpl_edge.ingest.rivals.roster` now
verifies its expert seeds through the same function rather than growing a
second, subtly different one. Two name matchers that disagree is how one
cohort ends up trusting an ID the other rejects.

The folding is deliberately aggressive in one direction only. Norwegian and
Icelandic names are common at the top of FPL, and ``unicodedata.NFKD`` does not
decompose the stroke in ``ø`` or the ligature in ``æ`` -- those are part of the
codepoint, not combining marks -- so they are transliterated explicitly before
the NFKD pass. Nothing here ever *invents* a match: a blank name on either side
fails, because "we could not read their name" must never be recorded as
"the name agrees".
"""

from __future__ import annotations

import unicodedata

#: Latin letters that NFKD does NOT decompose. See the module docstring.
_NON_DECOMPOSABLE = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "å": "a", "Å": "a",
    "ð": "d", "Ð": "d", "þ": "th", "Þ": "th", "ß": "ss", "đ": "d", "Đ": "d",
    "ł": "l", "Ł": "l",
})


def norm(s: str | None) -> str:
    """Casefold and strip accents so 'Jesper Øiestad' matches 'jesper oiestad'."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s.translate(_NON_DECOMPOSABLE))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().strip()


def name_matches(claimed: str | None, actual: str | None) -> bool:
    """Does the live profile name still support the name we wrote down?

    Containment in either direction, so a curated "Andy LTFPL" matches a
    profile whose account holder is plainly "Andy", and a curated "Ben
    Crellin" matches "Benjamin Crellin". An empty name on either side is a
    failure, never a pass: an unreadable profile is unverified, not verified.
    """
    a, b = norm(claimed), norm(actual)
    return bool(a) and bool(b) and (a in b or b in a)
