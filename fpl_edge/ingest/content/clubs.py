"""Resolve a club name as spoken into a ``dim_team.team_code``, or refuse.

Creator team names arrive through ASR and arrive damaged. This warehouse holds
``suddenland``, ``ipsswitch`` and ``leads`` for Sunderland, Ipswich and Leeds,
alongside clean ones like ``arsenal`` and ``crystal palace``.

The temptation is a nearest-match by edit distance over twenty clubs, which
feels safe because twenty is a small closed set. It is not safe, and the
counter-examples are in this season's own data:

    forester  -> Brentford   (d=6; Nott'm Forest is d=7)
    hull      -> Fulham      (d=4, TIED with Hull City)

Attributing a creator's words about Forest to Brentford -- on Brentford's own
fixture page, rendered identically to a real quote -- is a fabrication. So this
module does exact, then containment on the club's own tokens, and then it stops.
An unresolved name keeps its verbatim text, resolves to ``None``, and is counted
by the caller. A named shortfall is information; a confident wrong answer is not.
"""

from __future__ import annotations

import re

#: Tokens that identify a competition-level suffix rather than the club. They
#: are stripped only when a distinctive token survives -- "Man City" and
#: "Man Utd" both reduce to "man" otherwise, which is a collision, not a match.
_GENERIC = {"city", "town", "united", "utd", "fc", "afc", "wanderers",
            "albion", "hotspur", "county", "rovers"}

#: Minimum length of a token allowed to match by containment. Three characters
#: match far too much ("man" is inside "manchester" and also "mancity").
_MIN_TOKEN = 4


def _norm(value: object) -> str:
    return re.sub(r"[^a-z]", "", str(value or "").lower())


def _tokens(name: object) -> list[str]:
    return [t for t in re.split(r"[^a-z]+", str(name or "").lower()) if t]


class ClubResolver:
    """Names -> team_code for one season, built from ``dim_team``.

    ``lookup`` returns ``(team_code, basis)``. ``basis`` names HOW the match was
    made -- ``exact``, ``short_name``, ``token`` -- so a surprising attribution
    can be traced without re-running the resolver. A refusal is
    ``(None, reason)``.
    """

    __slots__ = ("_exact", "_tokens", "season")

    def __init__(self, rows: list[dict], season: str) -> None:
        self.season = season
        self._exact: dict[str, int] = {}
        #: distinctive token -> {team_code}. A set, because a token shared by
        #: two clubs must resolve to neither rather than to whichever was
        #: inserted last.
        self._tokens: dict[str, set[int]] = {}

        for r in rows:
            code = int(r["team_code"])
            for label in (r.get("name"), r.get("short_name")):
                key = _norm(label)
                if key:
                    self._exact.setdefault(key, code)
            toks = _tokens(r.get("name"))
            distinctive = [t for t in toks if t not in _GENERIC]
            # Strip generics ONLY if something distinctive is left; otherwise
            # the club is named entirely by generic words and keeps them all.
            for t in (distinctive or toks):
                if len(t) >= _MIN_TOKEN:
                    self._tokens.setdefault(t, set()).add(code)

    def lookup(self, spoken: object) -> tuple[int | None, str]:
        key = _norm(spoken)
        if not key:
            return None, "empty"

        code = self._exact.get(key)
        if code is not None:
            return code, "exact"

        # Containment either way, on tokens only. "hull" is inside "hullcity";
        # "forester" starts with the token "forest". Both are the speaker being
        # loose or the ASR adding a syllable, not a different club.
        hits: set[int] = set()
        basis = "token"
        for token, codes in self._tokens.items():
            if key == token or key.startswith(token) or token.startswith(key):
                if len(key) < _MIN_TOKEN:
                    continue
                hits |= codes
        if len(hits) == 1:
            return next(iter(hits)), basis
        if len(hits) > 1:
            return None, f"ambiguous ({len(hits)} clubs share this name)"
        return None, "no club matches this name"


def club_resolver(wh, season: str) -> ClubResolver:
    """Build a resolver from ``dim_team`` for one season."""
    rows = wh.sql(
        "SELECT DISTINCT team_code, name, short_name FROM dim_team "
        "WHERE season = ?", [season],
    ).to_dict("records")
    return ClubResolver(rows, season)
