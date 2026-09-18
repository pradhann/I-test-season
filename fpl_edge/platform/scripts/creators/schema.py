"""The JSON Schemas for the board and the detail panels.

A1's five-module plan left this block with no home and assigned its two ends to
two different modules. It is one block, shared by two panels, so it is its own
module (ARCHITECTURE_REVIEW.md Section 2 check 1)."""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Schemas.

_SOURCE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["key", "kind", "url", "last_item_at", "last_status", "discovery"],
    "properties": {
        "key": {"type": "string"},
        "kind": {"type": "string"},
        "url": {"type": ["string", "null"]},
        "last_item_at": {"type": ["string", "null"]},
        "last_status": {"type": ["integer", "null"]},
        "discovery": {"type": "string", "enum": ["auto", "manual"]},
        "policy": {"type": ["string", "null"]},
        "last_error": {"type": ["string", "null"]},
    },
}

#: The read-side naming contract shared by every surface that serves a player
#: name a creator spoke. `name` is always the verbatim spoken string;
#: `display_name` is the canonical `web_name` when the strict resolver found
#: exactly one player (`resolved: true`), and the raw string otherwise
#: (`resolved: false` -- never guessed). `disambiguator` is non-null exactly
#: when the served web_name belongs to two or more players in the pool
#: ("C. Palmer (CHE)"), so a bare surname can never silently mean two people.
_NAMING_PROPS: dict[str, Any] = {
    "display_name": {"type": "string"},
    "resolved": {"type": "boolean"},
    "disambiguator": {"type": ["string", "null"]},
}

_CALL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "display_name", "resolved", "disambiguator",
                 "conviction", "quote", "start_s", "deep_link"],
    "properties": {
        # null when the spoken name does not resolve to exactly one stable
        # player code. The name is always the creator's own words.
        "code": {"type": ["integer", "null"]},
        "name": {"type": "string"},
        **_NAMING_PROPS,
        "conviction": {"type": "string"},
        "quote": {"type": ["string", "null"]},
        "start_s": {"type": ["number", "null"]},
        "deep_link": {"type": ["string", "null"]},
    },
}

_CHIP = {
    "type": "object",
    "additionalProperties": False,
    "required": ["chip", "stance", "quote", "horizon_gw"],
    "properties": {
        "chip": {"type": "string"},
        "stance": {"type": "string"},
        "quote": {"type": ["string", "null"]},
        "horizon_gw": {"type": ["integer", "null"]},
        "start_s": {"type": ["number", "null"]},
        "deep_link": {"type": ["string", "null"]},
    },
}

#: A watch call: the same shape as a recommendation plus WHERE it was raised,
#: because "watch, mentioned while discussing transfers in" says more than
#: "watch" alone.
_WATCH = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "display_name", "resolved", "disambiguator",
                 "conviction", "quote", "start_s", "deep_link", "raised_in"],
    "properties": {
        **_CALL["properties"],
        "raised_in": {"enum": ["transfers_in", "transfers_out", "captain",
                               "differentials"]},
    },
}

_TAKE = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["summary", "model", "transfers_in", "transfers_out",
                 "captain", "chips"],
    "properties": {
        "summary": {"type": "string"},
        "summary_bullets": {"type": "array", "items": {"type": "string"}},
        "model": {"type": "string"},
        "transfers_in": {"type": "array", "items": _CALL},
        "transfers_out": {"type": "array", "items": _CALL},
        "captain": {"type": "array", "items": _CALL},
        "differentials": {"type": "array", "items": _CALL},
        # Observations, not recommendations. Separated from the buy/sell lists
        # because the model stores a "watch" in whichever list it arose in, and
        # showing one as a transfer attributes a call nobody made.
        "watching": {"type": "array", "items": _WATCH},
        "chips": {"type": "array", "items": _CHIP},
    },
}

_LATEST = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["item_id", "title", "url", "published_at", "kind", "text_source"],
    "properties": {
        "item_id": {"type": "string"},
        "title": {"type": "string"},
        "url": {"type": ["string", "null"]},
        "published_at": {"type": "string"},
        "kind": {"type": "string"},
        "text_source": {"type": "string"},
    },
}

_RECORD = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scored", "hits", "hit_rate", "wilson_lo95", "weight", "earned"],
    "properties": {
        "scored": {"type": ["integer", "null"]},
        "hits": {"type": ["integer", "null"]},
        "hit_rate": {"type": ["number", "null"]},
        "wilson_lo95": {"type": ["number", "null"]},
        "weight": {"type": ["number", "null"]},
        "earned": {"type": "boolean"},
        "reason": {"type": ["string", "null"]},
    },
}

#: A show's FPL identity. `people` is the truthful field: a show is a place
#: several managers talk, and the FPL Wire alone has four hosts with four
#: different teams. The flat entry_id/name/verified are populated ONLY when the
#: show has exactly one verified person -- otherwise they are null and the
#: caller must name a person, because "the Wire's team" does not exist.
_PERSON = {
    "type": "object",
    "additionalProperties": False,
    "required": ["person", "entry_id", "verified"],
    "properties": {
        "person": {"type": "string"},
        "entry_id": {"type": ["integer", "null"]},
        "name": {"type": ["string", "null"]},
        "verified": {"type": "boolean"},
        "source_url": {"type": ["string", "null"]},
        "reason": {"type": ["string", "null"]},
    },
}

_ENTRY = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["entry_id", "name", "verified"],
    "properties": {
        "entry_id": {"type": ["integer", "null"]},
        "name": {"type": ["string", "null"]},
        "verified": {"type": "boolean"},
        "source_url": {"type": ["string", "null"]},
        "people": {"type": "array", "items": _PERSON},
    },
}

_CREATOR = {
    "type": "object",
    "additionalProperties": False,
    "required": ["creator", "kinds", "sources", "n_items", "n_items_window",
                 "n_claims_window", "last_item_at", "latest", "take",
                 "take_reason", "record", "entry", "entry_reason"],
    "properties": {
        "creator": {"type": "string"},
        "kinds": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": _SOURCE},
        "n_items": {"type": "integer"},
        "n_items_window": {"type": "integer"},
        "n_claims_window": {"type": "integer"},
        "last_item_at": {"type": ["string", "null"]},
        "latest": _LATEST,
        "latest_reason": {"type": ["string", "null"]},
        "take": _TAKE,
        "take_reason": {"type": ["string", "null"]},
        "record": _RECORD,
        "entry": _ENTRY,
        "entry_reason": {"type": ["string", "null"]},
    },
}

_SIDE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["n", "creators"],
    "properties": {
        "n": {"type": "integer"},
        "creators": {"type": "array", "items": {"type": "string"}},
        # Extractor stays visible: a cue claim is a keyword window, an llm
        # claim is a semantic read with a stated conviction. Averaging the two
        # into one count throws away the difference the reader needs.
        "n_cue": {"type": "integer"},
        "n_llm": {"type": "integer"},
    },
}

#: Is he in the OWNER's squad, and in what role. `in_squad` is nullable and a
#: null is not a false: when the squad cannot be read, "he is not in your team"
#: is a claim this script has no evidence for, and printing `false` for all 614
#: players is the exact fabrication the no-invention rule forbids. The reason
#: is board-level, in `mine_reason`, because it is one fact about one read.
_MINE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["in_squad", "multiplier", "role"],
    "properties": {
        "in_squad": {"type": ["boolean", "null"]},
        "multiplier": {"type": ["integer", "null"]},
        "role": {"type": ["string", "null"]},
        # read | derived | null. A pre-deadline picks payload carries no
        # multiplier at all, so `_squad_state` derives it from the role using
        # the scoring rule. Which of the two it is travels with the number.
        "source": {"type": ["string", "null"]},
    },
}

#: The DID channel at board level: how many panel members ACTUALLY hold him,
#: of how many whose squads are known. `of` is the denominator that stops `n:
#: 0` from reading as "nobody on the panel owns him" -- see `panel_squads` for
#: who is missing and why.
_PANEL_OWNED = {
    "type": "object",
    "additionalProperties": False,
    "required": ["n", "of", "people"],
    "properties": {
        "n": {"type": "integer"},
        "of": {"type": "integer"},
        "people": {"type": "array", "items": {"type": "string"}},
    },
}

_CONSENSUS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "resolved", "disambiguator", "buy", "sell",
                 "captain", "net", "mine", "panel_owned"],
    "properties": {
        "code": {"type": "integer"},
        # `name` here is already the canonical web_name when the code is in
        # the pool; `resolved: false` marks the fallback to the raw claim
        # string. `disambiguator` follows the shared naming contract.
        "name": {"type": "string"},
        "resolved": {"type": "boolean"},
        "disambiguator": {"type": ["string", "null"]},
        "pos": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "price": {"type": ["number", "null"]},
        "own_pct": {"type": ["number", "null"]},
        "buy": _SIDE,
        "sell": _SIDE,
        "captain": _SIDE,
        "net": {"type": "integer"},
        "mine": _MINE,
        "panel_owned": _PANEL_OWNED,
    },
}

#: What is actually known about the panel's squads, stated once for the whole
#: board rather than repeated on every row. `known < with_entry` is the normal
#: state and the difference is named, person by person.
_PANEL_SQUADS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["panel_size", "with_entry", "known", "reason"],
    "properties": {
        "panel_size": {"type": "integer"},
        "with_entry": {"type": "integer"},
        "known": {"type": "integer"},
        "gw": {"type": ["integer", "null"]},
        "unknown_people": {"type": "array", "items": {"type": "string"}},
        "no_entry_people": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
}

BOARD_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
        "gw": {"type": ["integer", "null"], "minimum": 1, "maximum": 38,
               "default": None},
        # Which creators the board is ABOUT. The corpus holds 30-odd sources
        # because ingest casts wide; the owner follows a named panel of 16
        # people across 7 shows. Showing all 30 read as "30 tracked creators",
        # which is not what tracking means -- it is what ingesting means.
        # `panel` is the default and the honest one; `all` stays reachable so
        # the wider corpus is never hidden, only un-defaulted.
        "scope": {"enum": ["panel", "all"], "default": "panel"},
        # Reading the OWNER's squad is the one thing on this board that leaves
        # the warehouse: `_squad_state` tries the private API, then public
        # picks, then the manually entered 15. It degrades to unreadable and
        # never crashes, but a caller that does not want the round trip (a
        # test, a batch render) can turn it off and gets `in_squad: null` with
        # a reason rather than a silent `false`.
        "mine": {"type": "boolean", "default": True},
    },
}

# `required` keeps this disjoint from the registry's {empty, reason} branch:
# an honest empty has no `creators`, a real board always does.
BOARD_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["as_of", "window_days", "gw", "gw_reason", "creators",
                 "consensus"],
    "properties": {
        "as_of": {"type": "string"},
        "window_days": {"type": "integer"},
        "gw": {"type": ["integer", "null"]},
        # ALWAYS a string, never null. "GW2" alone does not say whether it was
        # requested, deduced from the calendar, or scraped off the claims
        # because the calendar was unreadable, and a reader trusts those three
        # differently. `player_chatter.gw_reason` carries the identical rule.
        "gw_reason": {"type": "string"},
        # Which creators this board is about, and which the scope excluded.
        # Named explicitly so the page can say "16 people across 7 shows"
        # rather than a count of whatever ingest happened to reach.
        "scope": {
            "type": "object", "additionalProperties": False,
            "required": ["applied"],
            "properties": {
                "applied": {"enum": ["panel", "all"]},
                "shows": {"type": "array", "items": {"type": "string"}},
                "excluded": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": ["string", "null"]},
            },
        },
        "creators": {"type": "array", "items": _CREATOR},
        "consensus": {"type": "array", "items": _CONSENSUS},
        "record_note": {"type": "string"},
        # How much of what has been FETCHED has been READ. The analysis queue
        # is budget-capped by design, so items land in content_item long
        # before any claim is extracted from them. Without this the board
        # showed a creator's take from four days ago beside a show published
        # this morning and said nothing about the difference.
        "coverage": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["window_days", "items", "analysed", "note"],
            "properties": {
                "window_days": {"type": "integer"},
                "items": {"type": "integer"},
                "analysed": {"type": "integer"},
                "newest_unread": {"type": ["string", "null"]},
                "note": {"type": "string"},
                "by_source": {"type": "array", "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source_key", "unread", "newest_unread"],
                    "properties": {
                        "source_key": {"type": "string"},
                        "unread": {"type": "integer"},
                        "newest_unread": {"type": ["string", "null"]},
                    },
                }},
            },
        },
        # Why every `mine.in_squad` is null, when it is. Always populated when
        # the squad could not be read; null when it could.
        "mine_reason": {"type": ["string", "null"]},
        "panel_squads": _PANEL_SQUADS,
    },
}

_CLAIM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "display_name", "resolved", "disambiguator",
                 "action", "confidence", "quote", "start_s",
                 "deep_link", "extractor"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        **_NAMING_PROPS,
        "action": {"type": "string"},
        "confidence": {"type": ["number", "null"]},
        "quote": {"type": ["string", "null"]},
        "start_s": {"type": ["number", "null"]},
        "deep_link": {"type": ["string", "null"]},
        "extractor": {"type": "string"},
        "gameweek": {"type": ["integer", "null"]},
        "published_at": {"type": ["string", "null"]},
    },
}

_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["item_id", "title", "url", "published_at", "kind",
                 "text_source", "analysis", "claims"],
    "properties": {
        "item_id": {"type": "string"},
        "title": {"type": "string"},
        "url": {"type": ["string", "null"]},
        "published_at": {"type": "string"},
        "kind": {"type": "string"},
        "text_source": {"type": "string"},
        "analysis": _TAKE,
        "analysis_reason": {"type": ["string", "null"]},
        "claims": {"type": "array", "items": _CLAIM},
    },
}

_SQUAD_PICK = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "pos", "price", "multiplier", "is_captain"],
    "properties": {
        "code": {"type": ["integer", "null"]},
        "name": {"type": "string"},
        "pos": {"type": ["string", "null"]},
        "price": {"type": ["number", "null"]},
        "multiplier": {"type": ["integer", "null"]},
        "is_captain": {"type": "boolean"},
    },
}

_TRANSFER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["gw", "in_name", "in_code", "out_name", "out_code", "time_utc"],
    "properties": {
        "gw": {"type": "integer"},
        "in_name": {"type": ["string", "null"]},
        "in_code": {"type": ["integer", "null"]},
        "out_name": {"type": ["string", "null"]},
        "out_code": {"type": ["integer", "null"]},
        "time_utc": {"type": ["string", "null"]},
    },
}

DETAIL_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["creator"],
    "properties": {
        "creator": {"type": "string", "minLength": 1},
        "days": {"type": "integer", "minimum": 1, "maximum": 3650, "default": 60},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 40},
        # Which gameweek's SQUAD to show. `days`/`limit` bound the item list and
        # are a different axis entirely: a video published five weeks ago about
        # GW2 is still what this creator's GW2 team should be read beside.
        # Null defaults to the newest squad that has actually locked -- see
        # `_squad_gw` for why that is not literally the next gameweek.
        "gw": {"type": ["integer", "null"], "minimum": 1, "maximum": 38,
               "default": None},
    },
}

DETAIL_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["creator", "as_of", "entry", "squad", "squad_gw", "squad_gws",
                 "transfers", "items"],
    "properties": {
        "creator": {"type": "string"},
        "as_of": {"type": "string"},
        "window_days": {"type": "integer"},
        "entry": _ENTRY,
        "entry_reason": {"type": ["string", "null"]},
        "squad": {"type": ["array", "null"], "items": _SQUAD_PICK},
        "squad_reason": {"type": ["string", "null"]},
        # The gameweek `squad` IS, and the gameweeks a squad exists for. Both
        # are required: a squad with no gameweek on it is a team from an
        # unnamed week, which is what this pair exists to stop rendering.
        "squad_gw": {"type": ["integer", "null"]},
        "squad_gws": {"type": "array", "items": {"type": "integer"}},
        "transfers": {"type": "array", "items": _TRANSFER},
        "transfers_reason": {"type": ["string", "null"]},
        "items": {"type": "array", "items": _ITEM},
        "record": _RECORD,
    },
}
