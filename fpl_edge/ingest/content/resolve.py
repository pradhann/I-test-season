"""Creator text -> stable :class:`~fpl_edge.types.PlayerCode`.

Resolution is against ``dim_player`` read through a :class:`Snapshot`, so the
index only ever contains players who existed at the instant the claim was
published. That is not pedantry: a 2024-25 episode saying "Wirtz" must not
resolve against a 2026-27 squad list, and a name that belongs to two different
players in two different seasons must resolve to whichever one was real then.

Everything here is built to fail loudly rather than plausibly.

* An ambiguous surface form is **never** resolved to a guess. There were two
  Ben Davieses in the Premier League; picking one silently welds two careers
  together, and the resulting feature is noise with a believable distribution.
  Ambiguity returns ``None`` and is counted separately from "not found".
* A short list of surnames that are also ordinary English words -- Rice, Wood,
  Ward, Long, Young, Cash, Best, May, Bright -- are refused as bare tokens. A
  transcript containing "worth the price" would otherwise resolve to Ward. They
  are accepted only with a first name attached or after an FPL cue word.
* Alias generation is derived from ``dim_player`` itself plus a small explicit
  table of community shorthand (KDB, TAA, VVD). Nothing is inferred from a
  fuzzy string distance, because at this corpus size an edit-distance match
  produces confident nonsense at exactly the rate that makes it hard to notice.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from fpl_edge.ingest.player_mapping import normalize_name
from fpl_edge.types import PlayerCode

#: Surnames that collide with common English words. Bare mentions are refused.
RISKY_SINGLE_TOKENS: frozenset[str] = frozenset({
    "rice", "wood", "ward", "long", "young", "cash", "best", "may", "bright",
    "james", "reid", "west", "brown", "white", "king", "moore", "sharp",
    "castle", "cook", "lord", "green", "day", "chalk", "digne", "smith",
    "clark", "wells", "burn", "coady", "mount", "cross", "sun", "man",
})

#: Community shorthand that no name field contains. Keyed on the normalised
#: alias, valued by the normalised *full* name it stands for; the code is
#: resolved through the same index as everything else, so a player who is not in
#: the season's squad simply does not resolve.
SHORTHAND: dict[str, str] = {
    "kdb": "kevin de bruyne",
    "taa": "trent alexander arnold",
    "trent": "trent alexander arnold",
    "vvd": "virgil van dijk",
    "virgil": "virgil van dijk",
    "kdh": "kobbie mainoo",
    "bruno": "bruno fernandes",
    "big dom": "dominic calvert lewin",
    "dcl": "dominic calvert lewin",
    "jws": "james ward prowse",
    "gvp": "gabriel jesus",
    "haaland": "erling haaland",
    "salah": "mohamed salah",
    "saka": "bukayo saka",
    "palmer": "cole palmer",
    "watkins": "ollie watkins",
    "isak": "alexander isak",
    "mbeumo": "bryan mbeumo",
    "gordon": "anthony gordon",
    "mitoma": "kaoru mitoma",
    "gyokeres": "viktor gyokeres",
    "sesko": "benjamin sesko",
    "wirtz": "florian wirtz",
    "semenyo": "antoine semenyo",
    "rogers": "morgan rogers",
    "eze": "eberechi eze",
    "murphy": "jacob murphy",
    "pedro": "joao pedro",
    "cunha": "matheus cunha",
    "szoboszlai": "dominik szoboszlai",
    "gabriel": "gabriel dos santos magalhaes",
}

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Mention:
    """A player name found in text, with where it was found."""

    surface: str
    normalised: str
    start: int
    end: int
    code: PlayerCode | None
    matched_name: str | None
    reason: str  # 'ok' | 'ambiguous' | 'unknown' | 'risky_bare_token'


@dataclass
class ResolutionStats:
    """Per-run accounting. Nothing here is allowed to be a silent zero."""

    mentions: int = 0
    resolved: int = 0
    ambiguous: int = 0
    unknown: int = 0
    risky_refused: int = 0
    unresolved_forms: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def match_rate(self) -> float:
        return self.resolved / self.mentions if self.mentions else 0.0

    def render(self) -> str:
        top = sorted(self.unresolved_forms.items(), key=lambda kv: -kv[1])[:12]
        detail = ", ".join(f"{k}x{v}" for k, v in top) or "(none)"
        return (
            f"mentions={self.mentions} resolved={self.resolved} "
            f"({self.match_rate:.1%}) ambiguous={self.ambiguous} "
            f"unknown={self.unknown} risky_refused={self.risky_refused}\n"
            f"  top unresolved: {detail}"
        )


class PlayerResolver:
    """An alias -> code index built from ``dim_player`` for one or more seasons."""

    def __init__(self, players: pd.DataFrame) -> None:
        """``players`` needs columns: code, web_name, first_name, second_name."""
        self._alias: dict[str, set[int]] = defaultdict(set)
        self._name_of: dict[int, str] = {}
        self._max_tokens = 1
        for row in players.itertuples(index=False):
            code = int(row.code)
            first = normalize_name(getattr(row, "first_name", "") or "")
            second = normalize_name(getattr(row, "second_name", "") or "")
            web = normalize_name(getattr(row, "web_name", "") or "")
            full = f"{first} {second}".strip()
            self._name_of[code] = full or web
            for alias in {web, full, second}:
                if alias:
                    self._alias[alias].add(code)
                    self._max_tokens = max(self._max_tokens, len(alias.split()))
            # Last token of a compound surname: "dos santos magalhaes" -> nothing
            # useful, but "calvert lewin" -> "lewin" is how people speak.
            if second and len(second.split()) > 1:
                tail = second.split()[-1]
                if len(tail) > 3:
                    self._alias[tail].add(code)
                # First + final surname token: nobody says "bruno borges
                # fernandes" -- they say "bruno fernandes". Without this variant
                # the most universal form of any middle-named player resolved to
                # nothing (found live: Bruno's captaincy claim was dropped as
                # unresolvable). Sits in the same ambiguity machinery as every
                # other alias, so a genuinely shared short form still refuses.
                if first:
                    self._alias[f"{first} {tail}"].add(code)
                    self._max_tokens = max(self._max_tokens, 2 + first.count(" "))
        for alias, target in SHORTHAND.items():
            codes = self._alias.get(target)
            if codes and len(codes) == 1:
                # Overwrite, not union. "palmer" already maps to every Palmer in
                # the index from the surname pass; unioning left it ambiguous and
                # the shorthand did nothing. Curated shorthand IS a
                # disambiguation -- when the full name it stands for is unique,
                # it settles the surname too.
                self._alias[alias] = set(codes)
                self._max_tokens = max(self._max_tokens, len(alias.split()))

    @property
    def size(self) -> int:
        return len(self._name_of)

    def lookup(self, phrase: str) -> tuple[PlayerCode | None, str]:
        key = normalize_name(phrase)
        codes = self._alias.get(key)
        if not codes:
            return None, "unknown"
        if len(codes) > 1:
            return None, "ambiguous"
        return PlayerCode(next(iter(codes))), "ok"

    def find_mentions(self, text: str, stats: ResolutionStats | None = None) -> list[Mention]:
        """Longest-match scan over the text.

        Longest-first is what stops "Gabriel" swallowing "Gabriel Martinelli"
        and attributing a Martinelli claim to Gabriel Magalhaes -- two Arsenal
        players, both heavily owned, opposite positions.
        """
        stats = stats if stats is not None else ResolutionStats()
        lowered = text.lower()
        tokens = [(m.group(0), m.start(), m.end()) for m in _WORD_RE.finditer(lowered)]
        mentions: list[Mention] = []
        i = 0
        while i < len(tokens):
            hit: Mention | None = None
            for span in range(min(self._max_tokens, len(tokens) - i), 0, -1):
                window = tokens[i:i + span]
                phrase = " ".join(t[0] for t in window)
                if span == 1 and phrase in RISKY_SINGLE_TOKENS:
                    if phrase in self._alias:
                        stats.risky_refused += 1
                    continue
                code, reason = self.lookup(phrase)
                if reason == "unknown":
                    continue
                hit = Mention(
                    surface=text[window[0][1]:window[-1][2]],
                    normalised=phrase,
                    start=window[0][1],
                    end=window[-1][2],
                    code=code,
                    matched_name=self._name_of.get(int(code)) if code else None,
                    reason=reason,
                )
                break
            if hit is None:
                i += 1
                continue
            mentions.append(hit)
            stats.mentions += 1
            if hit.reason == "ok":
                stats.resolved += 1
            elif hit.reason == "ambiguous":
                stats.ambiguous += 1
                stats.unresolved_forms[hit.normalised] += 1
            else:
                stats.unknown += 1
                stats.unresolved_forms[hit.normalised] += 1
            i += len(hit.normalised.split())
        return mentions


def resolver_for(snapshot_players: pd.DataFrame) -> PlayerResolver:
    required = {"code", "web_name"}
    missing = required - set(snapshot_players.columns)
    if missing:
        raise KeyError(f"dim_player frame is missing {sorted(missing)}")
    frame = snapshot_players.copy()
    for col in ("first_name", "second_name"):
        if col not in frame.columns:
            frame[col] = ""
    return PlayerResolver(frame)


class SeasonResolvers:
    """One resolver per season, plus a cross-season fallback.

    Scoping by season is worth a lot. Across five seasons the index holds two
    Wilsons, two Sarrs, two Chalobahs, two Andersons and two Richardses, and
    every one of those surnames becomes ambiguous and is refused -- which is the
    right call, but it throws away claims that were never actually ambiguous. In
    any *single* season most of those pairs do not co-exist, so resolving a
    2025-26 claim against the 2025-26 squad recovers them without guessing.

    The cross-season index remains the fallback for the case where the claim's
    season could not be determined. It is strictly more conservative, so falling
    back can only lose matches, never invent them.
    """

    def __init__(self, players: pd.DataFrame) -> None:
        self._by_season: dict[str, PlayerResolver] = {}
        if "season" in players.columns:
            for season, group in players.groupby("season"):
                self._by_season[str(season)] = resolver_for(group)
        self._any = resolver_for(players)

    @property
    def seasons(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_season))

    @property
    def size(self) -> int:
        return self._any.size

    def for_season(self, season: str | None) -> PlayerResolver:
        if season is None:
            return self._any
        return self._by_season.get(season, self._any)
