"""Cross-season player identity for historical FPL data.

The problem this module exists to solve
---------------------------------------

FPL's ``element`` id is a per-season row number. It is reassigned every summer.
Declan Rice is element 467 in 2022-23, 540 in 2023-24, 16 in 2024-25 and 21 in
2025-26. Any historical join on ``element`` therefore silently welds four
different players' careers together, and the resulting "form" feature is noise
with a plausible-looking distribution. This is the single most common way an FPL
backtest becomes worthless.

The stable key is ``code`` (FPL's own cross-season player code, sourced from the
Premier League's player registry). Rice is 204480 in every season, before and
after his West Ham -> Arsenal move.

vaastav's archive does not carry ``code`` uniformly:

* ``data/<season>/players_raw.csv``  -- has BOTH ``id`` (element) and ``code``.
  This is the authority for a season's element -> code map.
* ``data/<season>/gws/merged_gw.csv`` -- has ``element`` and ``name`` but **no
  ``code``** in any season from 2016-17 to 2025-26. Column sets also drift
  between seasons (2018-19 carries an extra ``id``; 2024-25 adds ``mng_*``;
  2025-26 adds the defensive-contribution columns).
* ``data/<season>/player_idlist.csv`` -- ``first_name, second_name, id`` only.
  No code. Useless for cross-season identity on its own.

So resolution is a lookup chain, tried in this order and *never* silently
falling through to a guess:

1. an explicit ``code`` column, if the file happens to have one;
2. ``(season, element)`` -> code, from that season's ``players_raw.csv``;
3. ``(season, normalised name)`` -> code, **only when that name is unique within
   the season**.

Step 3 is deliberately refused when ambiguous. In 2022-23 there were two Premier
League players called Ben Davies -- Liverpool's (code 152898) and Tottenham's
(code 115556) -- with identical first and second names *and* identical
``web_name``. A name-keyed join collapses them into one player. This module
returns "unresolved" for that case rather than picking one, and unresolved rows
are dropped loudly by the caller with their count reported.

Manager elements
----------------

``element_type == 5`` ("Manager") existed for exactly one season. Those rows
cannot score points under the 2026/27 rules, so they are excluded here rather
than mapped. :meth:`PlayerCodeIndex.add_season` routes every element type
through :meth:`fpl_edge.types.Position.from_api`, which raises on 5 by design;
the exception is caught, the element is recorded as excluded, and every later
row for it is classified as ``dropped_manager`` -- a separate counter from
``dropped_unmatched``, so a genuine mapping failure can never hide inside the
manager count.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from fpl_edge.types import ElementId, PlayerCode, Position, Season

#: element_type values that are real, scoring players in 2026/27.
PLAYABLE_ELEMENT_TYPES = frozenset(int(p) for p in Position)

#: ``merged_gw.csv`` spells positions as strings. "AM" is the 2024-25 manager
#: label; "MNG" appears in some downstream mirrors. Both map to element_type 5,
#: which ``Position.from_api`` rejects on purpose.
POSITION_STRINGS: Mapping[str, int] = {
    "GK": 1, "GKP": 1,
    "DEF": 2,
    "MID": 3,
    "FWD": 4,
    "AM": 5, "MNG": 5, "MGR": 5,
}


class IdentityCollisionError(RuntimeError):
    """Two distinct elements in one season claim the same stable code.

    This would mean the cross-season key is not a key. It is fatal rather than
    a warning: every downstream feature is keyed on ``code``.
    """


def normalize_name(value: object) -> str:
    """Fold a name to a comparable form.

    Strips diacritics (``Gyökeres`` -> ``gyokeres``), lowercases, drops
    punctuation (``Kesler-Hayden`` -> ``kesler hayden``) and collapses
    whitespace. Deliberately does *not* reorder tokens: vaastav writes some East
    Asian names surname-first in one season and given-name-first in another
    (``Tomiyasu Takehiro`` / ``Takehiro Tomiyasu``), and
    :func:`shares_name_token` handles that case explicitly instead.
    """
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Stroke and ligature letters have no NFKD decomposition, so the combining
    # filter leaves them intact and the [a-z] strip then DELETES them:
    # "Ødegaard" became "degaard" and silently failed every match against the
    # ASCII "Odegaard" bookmakers publish. Translate them explicitly.
    text = text.translate(_NON_DECOMPOSING)
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


#: Letters NFKD cannot decompose, mapped to their ASCII conventions.
_NON_DECOMPOSING = str.maketrans({
    "Ø": "O", "ø": "o",
    "Đ": "D", "đ": "d",
    "Ł": "L", "ł": "l",
    "Æ": "AE", "æ": "ae",
    "Œ": "OE", "œ": "oe",
    "ß": "ss",
    "Þ": "Th", "þ": "th",
    "Ð": "D", "ð": "d",
})


def shares_name_token(a: str, b: str) -> bool:
    """True if two normalised names share at least one token.

    Used to distinguish *rendering* differences for one person (``kepa
    arrizabalaga`` vs ``kepa arrizabalaga revuelta``) from two genuinely
    different people accidentally sharing a code.
    """
    return bool(set(a.split()) & set(b.split()))


@dataclass(frozen=True, slots=True)
class SeasonIndexReport:
    """What ``add_season`` learned from one ``players_raw.csv``."""

    season: str
    rows: int
    players_indexed: int
    manager_elements: tuple[int, ...]
    unknown_element_types: Mapping[int, int]
    ambiguous_names: tuple[str, ...]
    temporary_codes: tuple[int, ...] = ()

    @property
    def managers_dropped(self) -> int:
        return len(self.manager_elements)


@dataclass(frozen=True, slots=True)
class ResolutionReport:
    """Per-frame accounting for a resolution pass. Nothing is filled silently."""

    season: str
    total_rows: int
    by_code_column: int = 0
    by_element_id: int = 0
    by_name: int = 0
    dropped_manager: int = 0
    dropped_unmatched: int = 0
    unmatched_samples: tuple[str, ...] = field(default_factory=tuple)

    @property
    def resolved(self) -> int:
        return self.by_code_column + self.by_element_id + self.by_name

    @property
    def eligible(self) -> int:
        """Rows that *should* resolve: everything bar deliberately-excluded managers."""
        return self.total_rows - self.dropped_manager

    @property
    def match_rate(self) -> float:
        """Fraction of eligible rows that got a stable code. 1.0 == nothing lost."""
        return self.resolved / self.eligible if self.eligible else 1.0

    def render(self) -> str:
        return (
            f"{self.season}: {self.resolved}/{self.eligible} eligible rows resolved "
            f"({self.match_rate:.4%}) "
            f"[code_col={self.by_code_column} element={self.by_element_id} name={self.by_name}] "
            f"managers_dropped={self.dropped_manager} unmatched_dropped={self.dropped_unmatched}"
        )


class PlayerCodeIndex:
    """Resolves any historical row to a stable :class:`PlayerCode`.

    Build it by feeding one ``players_raw.csv`` per season, then resolve
    arbitrary frames against it.
    """

    def __init__(self) -> None:
        self._element_to_code: dict[tuple[str, int], int] = {}
        self._code_to_element: dict[tuple[str, int], int] = {}
        self._name_to_codes: dict[tuple[str, str], set[int]] = defaultdict(set)
        self._fullname_to_codes: dict[tuple[str, str], set[int]] = defaultdict(set)
        self._temporary_codes: set[tuple[str, int]] = set()
        self._manager_elements: dict[str, set[int]] = defaultdict(set)
        self._unknown_elements: dict[str, dict[int, int]] = defaultdict(dict)
        self._names_by_code: dict[int, set[str]] = defaultdict(set)
        self._identity: dict[int, dict[str, Any]] = {}
        self._seasons: list[str] = []

    # -- construction --------------------------------------------------------

    def add_season(self, season: str | Season, players_raw: pd.DataFrame) -> SeasonIndexReport:
        """Index one season's ``players_raw.csv``.

        Requires ``id`` and ``code``. Uses ``element_type``, ``first_name``,
        ``second_name`` and ``web_name`` when present.
        """
        season = str(season)
        missing = {"id", "code"} - set(players_raw.columns)
        if missing:
            raise KeyError(
                f"{season}: players_raw is missing {sorted(missing)}; without both the "
                "element -> code map cannot be built and cross-season joins are unsafe"
            )
        if season in self._seasons:
            raise ValueError(f"season {season!r} already indexed")
        self._seasons.append(season)

        managers: set[int] = set()
        unknown: dict[int, int] = {}
        indexed = 0
        for row in players_raw.itertuples(index=False):
            element = int(row.id)
            code = int(row.code)
            raw_type = getattr(row, "element_type", None)
            if raw_type is not None and not pd.isna(raw_type):
                try:
                    Position.from_api(int(raw_type))
                except ValueError:
                    # element_type 5 (Manager) and anything else unrecognised.
                    if int(raw_type) == 5:
                        managers.add(element)
                        self._manager_elements[season].add(element)
                    else:
                        unknown[element] = int(raw_type)
                        self._unknown_elements[season][element] = int(raw_type)
                    continue

            # FPL's own admission that a code is not yet stable: it will be
            # reissued once the Premier League registry catches up with a
            # transfer, splitting one career in two. Recorded, never assumed away.
            temporary = getattr(row, "has_temporary_code", None)
            if temporary is not None and not pd.isna(temporary) and bool(temporary):
                self._temporary_codes.add((season, code))

            prior = self._code_to_element.get((season, code))
            if prior is not None and prior != element:
                raise IdentityCollisionError(
                    f"{season}: elements {prior} and {element} both claim code {code}; "
                    "the stable key is not unique within the season"
                )
            self._element_to_code[(season, element)] = code
            self._code_to_element[(season, code)] = element
            indexed += 1

            first = getattr(row, "first_name", "") or ""
            second = getattr(row, "second_name", "") or ""
            web = getattr(row, "web_name", "") or ""
            full = normalize_name(f"{first} {second}")
            if full:
                self._name_to_codes[(season, full)].add(code)
                self._fullname_to_codes[(season, full)].add(code)
                self._names_by_code[code].add(full)
            web_norm = normalize_name(web)
            if web_norm:
                self._name_to_codes[(season, web_norm)].add(code)
            self._identity.setdefault(
                code,
                {"first_name": str(first), "second_name": str(second), "web_name": str(web)},
            )

        temporary = tuple(sorted(c for s_, c in self._temporary_codes if s_ == season))
        ambiguous = tuple(
            sorted(n for (s, n), codes in self._name_to_codes.items()
                   if s == season and len(codes) > 1)
        )
        return SeasonIndexReport(
            season=season,
            rows=len(players_raw),
            players_indexed=indexed,
            manager_elements=tuple(sorted(managers)),
            unknown_element_types=dict(unknown),
            ambiguous_names=ambiguous,
            temporary_codes=temporary,
        )

    # -- lookups -------------------------------------------------------------

    @property
    def seasons(self) -> tuple[str, ...]:
        return tuple(self._seasons)

    def code_for_element(self, season: str | Season, element: int | ElementId) -> PlayerCode | None:
        got = self._element_to_code.get((str(season), int(element)))
        return PlayerCode(got) if got is not None else None

    def element_for_code(self, season: str | Season, code: int | PlayerCode) -> ElementId | None:
        got = self._code_to_element.get((str(season), int(code)))
        return ElementId(got) if got is not None else None

    def codes_for_name(self, season: str | Season, name: object) -> frozenset[int]:
        """Every code in the season matching this name. >1 means "refuse to guess"."""
        return frozenset(self._name_to_codes.get((str(season), normalize_name(name)), ()))

    def is_manager(self, season: str | Season, element: int | ElementId) -> bool:
        return int(element) in self._manager_elements.get(str(season), set())

    def manager_elements(self, season: str | Season) -> frozenset[int]:
        return frozenset(self._manager_elements.get(str(season), set()))

    def identity(self, code: int | PlayerCode) -> Mapping[str, Any]:
        return dict(self._identity.get(int(code), {}))

    def names_for_code(self, code: int | PlayerCode) -> frozenset[str]:
        return frozenset(self._names_by_code.get(int(code), ()))

    def seasons_for_code(self, code: int | PlayerCode) -> tuple[str, ...]:
        return tuple(s for s in self._seasons if (s, int(code)) in self._code_to_element)

    # -- integrity checks ----------------------------------------------------

    def ambiguous_names(self) -> dict[tuple[str, str], frozenset[int]]:
        """``(season, normalised name) -> codes`` where a name is not unique.

        These are precisely the rows a name-keyed join would corrupt.
        """
        return {k: frozenset(v) for k, v in self._name_to_codes.items() if len(v) > 1}

    def identity_conflicts(self) -> dict[int, frozenset[str]]:
        """Codes carrying names that look like two different people.

        Name *rendering* drifts across seasons for one person (extra surnames,
        reordered East Asian names), so a bare "one name per code" assertion is
        too strict on real data. The test applied here is that every pair of
        names recorded for a code shares at least one token. A code whose names
        share nothing is a genuine identity merge and a bug.
        """
        out: dict[int, frozenset[str]] = {}
        for code, names in self._names_by_code.items():
            ordered = sorted(names)
            if len(ordered) < 2:
                continue
            if any(
                not shares_name_token(a, b)
                for i, a in enumerate(ordered)
                for b in ordered[i + 1:]
            ):
                out[code] = frozenset(ordered)
        return out

    def temporary_codes(self, season: str | Season | None = None) -> frozenset[int]:
        """Codes FPL has flagged as provisional and intends to reissue.

        The flag only exists in ``players_raw.csv`` from 2024-25 onward. Where it
        is absent it is not inferred; where it is present and set, the code is
        reported rather than trusted, because a reissue splits one career in two
        and both halves look like perfectly valid codes.
        """
        if season is None:
            return frozenset(c for _, c in self._temporary_codes)
        return frozenset(c for s, c in self._temporary_codes if s == str(season))

    def split_identities(self) -> dict[str, dict[str, int]]:
        """One name, unambiguous within each season, but a different code between them.

        This is what a code reissue looks like from the outside: a career cut in
        half, with no error anywhere. Measured on 2022-23..2025-26 it catches
        exactly one player (Kaine Kesler-Hayden, 537043 -> 465390).

        Deliberately *reported* rather than repaired. Merging on name equality is
        the very mistake this module exists to prevent -- 2022-23 fielded two
        different Ben Davieses -- so the halves stay distinct and the count is
        published instead.
        """
        by_name: dict[str, dict[str, int]] = defaultdict(dict)
        for (season, name), codes in self._fullname_to_codes.items():
            if len(codes) == 1:
                by_name[name][season] = next(iter(codes))
        return {
            name: dict(seasons)
            for name, seasons in by_name.items()
            if len(set(seasons.values())) > 1
        }

    def coverage(self) -> pd.DataFrame:
        """One row per code with the seasons it appears in. Handy for eyeballing."""
        rows = []
        for code in sorted(self._names_by_code):
            seasons = self.seasons_for_code(code)
            ident = self._identity.get(code, {})
            rows.append({
                "code": code,
                "web_name": ident.get("web_name", ""),
                "n_seasons": len(seasons),
                "seasons": ",".join(seasons),
            })
        return pd.DataFrame(rows)

    # -- resolution ----------------------------------------------------------

    def resolve_frame(
        self,
        season: str | Season,
        df: pd.DataFrame,
        *,
        element_col: str = "element",
        code_col: str = "code",
        name_col: str = "name",
        position_col: str = "position",
        allow_name_fallback: bool = True,
        max_samples: int = 20,
    ) -> tuple[pd.DataFrame, ResolutionReport]:
        """Attach a stable ``code`` column, dropping what cannot be resolved.

        Returns ``(resolved_frame, report)``. The frame contains only rows that
        got a real code; nothing is imputed and no placeholder code is invented.
        Manager rows are removed and counted separately from failures.
        """
        season = str(season)
        if df.empty:
            return df.assign(code=pd.Series(dtype="int64")), ResolutionReport(season, 0)

        managers = self._manager_elements.get(season, set())
        unknown_types = self._unknown_elements.get(season, {})

        has_code = code_col in df.columns
        has_element = element_col in df.columns
        has_name = name_col in df.columns and allow_name_fallback
        has_position = position_col in df.columns
        if not (has_code or has_element or has_name):
            raise KeyError(
                f"{season}: frame has none of {code_col!r}, {element_col!r}, {name_col!r}; "
                "there is no way to reach a stable code"
            )

        codes: list[int | None] = []
        n_code = n_elem = n_name = n_mgr = n_bad = 0
        samples: list[str] = []

        code_vals = df[code_col].to_numpy() if has_code else None
        elem_vals = df[element_col].to_numpy() if has_element else None
        name_vals = df[name_col].to_numpy() if has_name else None
        pos_vals = df[position_col].to_numpy() if has_position else None

        for i in range(len(df)):
            # Manager rows leave first, by either signal, so they can never be
            # mistaken for a mapping failure.
            if pos_vals is not None:
                pos_code = POSITION_STRINGS.get(str(pos_vals[i]).strip().upper())
                if pos_code is not None and pos_code not in PLAYABLE_ELEMENT_TYPES:
                    codes.append(None)
                    n_mgr += 1
                    continue
            if elem_vals is not None and not pd.isna(elem_vals[i]):
                element = int(elem_vals[i])
                if element in managers:
                    codes.append(None)
                    n_mgr += 1
                    continue
                if element in unknown_types:
                    codes.append(None)
                    n_bad += 1
                    if len(samples) < max_samples:
                        samples.append(
                            f"element {element} has unsupported element_type "
                            f"{unknown_types[element]}"
                        )
                    continue

            if code_vals is not None and not pd.isna(code_vals[i]):
                codes.append(int(code_vals[i]))
                n_code += 1
                continue

            if elem_vals is not None and not pd.isna(elem_vals[i]):
                element = int(elem_vals[i])
                mapped = self._element_to_code.get((season, element))
                if mapped is not None:
                    codes.append(mapped)
                    n_elem += 1
                    continue

            if name_vals is not None:
                candidates = self.codes_for_name(season, name_vals[i])
                if len(candidates) == 1:
                    codes.append(next(iter(candidates)))
                    n_name += 1
                    continue
                codes.append(None)
                n_bad += 1
                if len(samples) < max_samples:
                    why = "ambiguous" if candidates else "unknown"
                    samples.append(f"{why} name {name_vals[i]!r} (candidates={sorted(candidates)})")
                continue

            codes.append(None)
            n_bad += 1
            if len(samples) < max_samples:
                ident = elem_vals[i] if elem_vals is not None else "?"
                samples.append(f"element {ident!r} not in {season} players_raw")

        out = df.copy()
        out["code"] = pd.array(codes, dtype="Int64")
        kept = out[out["code"].notna()].copy()
        kept["code"] = kept["code"].astype("int64")

        report = ResolutionReport(
            season=season,
            total_rows=len(df),
            by_code_column=n_code,
            by_element_id=n_elem,
            by_name=n_name,
            dropped_manager=n_mgr,
            dropped_unmatched=n_bad,
            unmatched_samples=tuple(samples),
        )
        return kept.reset_index(drop=True), report


def build_index(
    players_raw_by_season: Mapping[str, pd.DataFrame],
    *,
    order: Iterable[str] | None = None,
) -> tuple[PlayerCodeIndex, list[SeasonIndexReport]]:
    """Convenience: index several seasons at once, oldest first."""
    index = PlayerCodeIndex()
    seasons = list(order) if order is not None else sorted(players_raw_by_season)
    reports = [index.add_season(s, players_raw_by_season[s]) for s in seasons]
    return index, reports
