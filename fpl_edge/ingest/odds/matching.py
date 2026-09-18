"""Joining two sources that disagree about names.

Fixtures and players arrive from three vocabularies (FPL, football-data.co.uk
and the odds API) and none of them is authoritative. These functions fold
accents, apply surname rules and refuse ambiguous matches rather than guessing,
because a wrong join silently attributes one player's price to another.

Split out of the 1,966-line ``fpl_edge/ingest/odds.py``
(ARCHITECTURE_REVIEW.md Section 3 and Section 4 row 15). The cut is by
function name, not by line range: the original's own line ranges overlapped.
``fpl_edge.ingest.odds`` re-exports the whole public surface, so every caller
imports exactly what it imported before.
"""

from __future__ import annotations
import datetime as dt
from dataclasses import dataclass
from typing import Any
import pandas as pd
from fpl_edge.ingest.player_mapping import normalize_name
from fpl_edge.store import Warehouse

from fpl_edge.ingest.odds.football_data import FD_TEAM_ALIASES, _slugify


def match_fixture_keys(wh: Warehouse, season: str, as_of: dt.datetime) -> pd.DataFrame:
    """Map natural odds keys onto ``season:fixture_id``.

    Returns the mapping rather than rewriting ``fact_odds`` in place: the
    warehouse is append-only by design, and a name-matching heuristic is
    exactly the kind of thing that should be re-derivable rather than baked
    into stored facts.
    """
    snap = wh.snapshot_at(as_of)
    fx = snap.table("fact_fixture", where="season = ?", params=[season])
    teams = snap.table("dim_team", where="season = ?", params=[season])
    if fx.empty or teams.empty:
        return pd.DataFrame(columns=["fixture_key", "matched_key", "fixture_id"])

    name_by_code = dict(zip(teams["team_code"], teams["name"]))
    odds_keys = wh.sql("SELECT DISTINCT fixture_key FROM fact_odds")["fixture_key"]

    canon = _slugify
    lookup: dict[tuple[str, str, str], int] = {}
    for _, f in fx.iterrows():
        ko = f["kickoff_utc"]
        day = ko.date().isoformat() if pd.notna(ko) else "unknown"
        h = canon(name_by_code.get(f["home_team_code"], ""))
        a = canon(name_by_code.get(f["away_team_code"], ""))
        lookup[(day, h, a)] = int(f["fixture_id"])

    alias = {canon(k): canon(v) for k, v in FD_TEAM_ALIASES.items()}
    out = []
    for key in odds_keys:
        parts = str(key).split(":")
        if len(parts) != 4 or parts[0] != season:
            continue
        _, day, h, a = parts
        h2, a2 = alias.get(h, h), alias.get(a, a)
        fid = lookup.get((day, h2, a2))
        out.append({"fixture_key": key, "fixture_id": fid,
                    "matched_key": f"{season}:{fid}" if fid is not None else None})
    return pd.DataFrame(out, columns=["fixture_key", "fixture_id", "matched_key"])


@dataclass(frozen=True, slots=True)
class NameMatch:
    """One resolution attempt, successful or not. Never a guess."""

    api_name: str
    code: int | None
    web_name: str | None
    rule: str


_LATIN_FOLD = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
    "ß": "ss", "đ": "d", "Đ": "d", "ð": "d", "Ð": "d", "ł": "l", "Ł": "l",
    "þ": "th", "Þ": "th", "ı": "i", "ħ": "h", "ŧ": "t", "ŉ": "n",
})


def fold_name(value: object) -> str:
    """Normalise a name for comparison, folding stroked letters first.

    Verified on the real GW1 cards: without the fold, "Martin Odegaard" from
    the bookmaker and "Martin Ødegaard" from FPL do not match.
    """
    return normalize_name(str(value).translate(_LATIN_FOLD))


def _fpl_name_keys(row: pd.Series) -> tuple[str, set[str]]:
    full = fold_name(f"{row.get('first_name') or ''} {row.get('second_name') or ''}")
    return full, set(full.split())


def match_player_names(
    api_names: list[str], squad: pd.DataFrame
) -> list[NameMatch]:
    """Resolve bookmaker player names against an FPL squad.

    ``squad`` must already be narrowed to the clubs in the fixture -- that
    constraint does most of the work, because it removes the possibility of
    matching a common surname onto the wrong club.

    Rules are tried in order and each must be *unique* among the candidates. An
    ambiguous match is returned as unmatched with ``rule="ambiguous"`` rather
    than resolved by picking one, on the same principle as
    ``player_mapping.PlayerCodeIndex``: two Ben Davieses is a reason to stop,
    not a reason to choose.

    The rules, and the real cases from GW1 that motivate each:

    1. ``exact_full`` -- "Martin Zubimendi Ibanez" == first+second once
       diacritics are folded ("Martín" + "Zubimendi Ibáñez").
    2. ``exact_web`` -- the bookmaker used FPL's short name.
    3. ``api_subset`` -- API tokens are a subset of the FPL tokens.
       "Gabriel Martinelli" ⊂ "Gabriel Martinelli Silva".
    4. ``fpl_subset`` -- the reverse, for when the bookmaker is more verbose.
       "Magalhaes Gabriel" against FPL "Gabriel dos Santos Magalhães" resolves
       here because token order is ignored.
    5. ``surname_initial`` -- last token plus first initial, the last resort.
    """
    out: list[NameMatch] = []
    if squad.empty:
        return [NameMatch(n, None, None, "no_squad") for n in api_names]

    cand = []
    for _, r in squad.iterrows():
        full, toks = _fpl_name_keys(r)
        cand.append({
            "code": int(r["code"]), "web": str(r["web_name"]),
            "web_norm": fold_name(r["web_name"]), "full": full, "toks": toks,
            "surname": full.split()[-1] if full else "",
        })

    for name in api_names:
        norm = fold_name(name)
        toks = set(norm.split())
        hit: list[dict[str, Any]] = []
        rule = "unmatched"

        for label in ("exact_full", "exact_web", "api_subset",
                      "fpl_subset", "surname_initial"):
            hit = [c for c in cand if _name_rule(label, norm, toks, c)]
            if hit:
                rule = label
                break

        if len(hit) == 1:
            out.append(NameMatch(name, hit[0]["code"], hit[0]["web"], rule))
        elif len(hit) > 1:
            out.append(NameMatch(name, None, None, "ambiguous"))
        else:
            out.append(NameMatch(name, None, None, "unmatched"))
    return out


def _name_rule(rule: str, norm: str, toks: set[str], c: dict[str, Any]) -> bool:
    """Whether one candidate satisfies one matching rule. See match_player_names."""
    if rule == "exact_full":
        return bool(c["full"]) and c["full"] == norm
    if rule == "exact_web":
        return bool(c["web_norm"]) and c["web_norm"] == norm
    if rule == "api_subset":
        return bool(toks) and toks <= c["toks"]
    if rule == "fpl_subset":
        return bool(c["toks"]) and c["toks"] <= toks
    if rule == "surname_initial":
        return _surname_initial(toks, c["toks"], c["surname"])
    raise ValueError(f"unknown name rule {rule!r}")


def _surname_initial(api_toks: set[str], fpl_toks: set[str], surname: str) -> bool:
    """Share the FPL surname and agree on some initial.

    Deliberately anchored on the *last* FPL token rather than any shared token.
    "Martin Odegaard" and "Martin Zubimendi Ibanez" share the token "martin",
    and without the surname anchor that collision made Odegaard ambiguous
    against every other Martin in the fixture.
    """
    if not surname or len(surname) <= 3 or surname not in api_toks:
        return False
    return bool({t[0] for t in api_toks} & {t[0] for t in fpl_toks})


def squad_for_fixture(
    wh: Warehouse, season: str, as_of: dt.datetime, clubs: list[str]
) -> pd.DataFrame:
    """The players of the named FPL clubs, as known at ``as_of``."""
    snap = wh.snapshot_at(as_of)
    players = snap.table("dim_player", where="season = ?", params=[season])
    teams = snap.table("dim_team", where="season = ?", params=[season])
    if players.empty or teams.empty:
        return players.iloc[0:0]
    codes = set(teams[teams["name"].isin(clubs)]["team_code"])
    return players[players["team_code"].isin(codes)].copy()
