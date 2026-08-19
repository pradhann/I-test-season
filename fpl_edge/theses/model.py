"""The thesis file format: YAML front matter over prose, round-trippable.

The file IS the record. Nothing about a thesis lives only in a database: the
claim, the frozen comparator, the model's numbers at creation and (after
resolution) the outcome are all in the front matter, so ``git log theses/`` is
the complete history of what this project believed and how it fared.

Serialisation rules that matter:

* Timestamps are written as ISO-8601 UTC strings ending in ``Z`` and accepted
  back as either strings or the datetimes PyYAML produces, so a hand-edited
  file still parses.
* ``model_verdict_at_creation`` round-trips exactly. Resolution appends a
  ``resolution`` block and flips ``status``; it never rewrites the creation
  block, and a test asserts that byte-for-byte semantics (value equality).
* Front matter is emitted in a fixed key order so diffs are stable and a git
  diff of a resolution shows only the resolution.
"""

from __future__ import annotations

import datetime as dt
import enum
import math
import re
from dataclasses import dataclass, replace
from typing import Any

import yaml

UTC = dt.timezone.utc


class ThesisSource(enum.StrEnum):
    """Which channel the belief arrived through. The scoreboard keys on this."""

    USER_CHAT = "user_chat"
    CREATOR = "creator"
    ELITE_MANAGER = "elite_manager"
    MODEL = "model"
    LLM_SCOUT = "llm_scout"


class ClaimType(enum.StrEnum):
    BUY = "buy"
    AVOID = "avoid"
    WATCH = "watch"
    OUT_OF_POSITION = "out_of_position"
    MINUTES = "minutes"
    CAPTAIN = "captain"


class ThesisStatus(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    VOID = "void"


class ThesisOutcome(enum.StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    PUSH = "push"
    #: A watch thesis whose window closed. On the record, outside the hit rate.
    UNSCORED = "unscored"
    #: Ungradeable through no fault of the claim (e.g. empty comparator).
    VOID = "void"


#: Default horizon per claim type, in gameweeks. Captaincy is a one-week bet;
#: everything else is a hold and judging it on one blank would be a strawman.
DEFAULT_HORIZON: dict[ClaimType, int] = {
    ClaimType.BUY: 6,
    ClaimType.AVOID: 6,
    ClaimType.WATCH: 6,
    ClaimType.OUT_OF_POSITION: 6,
    ClaimType.MINUTES: 6,
    ClaimType.CAPTAIN: 1,
}

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)

#: Fixed emission order. Resolution-only keys go last so a resolving diff is
#: purely additive at the bottom of the front matter.
_KEY_ORDER = (
    "id", "created", "source", "creator", "raw_input",
    "player", "player_code", "season", "claim_type",
    "gw_start", "gw_end", "horizon_gws",
    "falsifiable_prediction", "comparator_codes", "comparator_label",
    "model_verdict_at_creation", "acted", "idea_id", "status", "resolution",
)


def _ts(value: Any, label: str) -> dt.datetime:
    """Accept a datetime or ISO string; always return tz-aware UTC."""
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.strip())
    if not isinstance(value, dt.datetime):
        raise ValueError(f"{label} must be a datetime or ISO string, got {value!r}")
    if value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware UTC, got naive {value!r}")
    return value.astimezone(UTC)


def _iso(ts: dt.datetime) -> str:
    return ts.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _plain(value: Any) -> Any:
    """Coerce numpy scalars / pandas NA-likes into plain Python for YAML."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else round(value, 6)
    if isinstance(value, dt.datetime):
        return _iso(_ts(value, "timestamp"))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "item"):  # numpy scalar
        return _plain(value.item())
    return str(value)


@dataclass(frozen=True, slots=True)
class Thesis:
    """One versioned belief. Immutable; resolution returns a new instance."""

    id: str
    created: dt.datetime
    source: ThesisSource
    raw_input: str
    player: str
    player_code: int
    season: str
    claim_type: ClaimType
    gw_start: int
    horizon_gws: int
    #: A string from the grammar in :mod:`fpl_edge.theses.grammar`, or None
    #: for watch theses. Validated at construction: a non-watch thesis with an
    #: unparseable prediction is refused, so nothing ungradeable reaches disk.
    falsifiable_prediction: str | None
    #: The model's numbers at creation, from a Snapshot at that instant.
    #: Written once. Never backfilled -- that would be leakage.
    model_verdict_at_creation: dict[str, Any]
    status: ThesisStatus = ThesisStatus.OPEN
    creator: str | None = None
    #: Comparator membership frozen at creation, for templates that grade
    #: against a set (peer medians, captain pools). Empty for the rest.
    comparator_codes: tuple[int, ...] = ()
    comparator_label: str = ""
    acted: bool = False
    #: Link back to the ideas registry when this thesis was born there.
    idea_id: str | None = None
    resolution: dict[str, Any] | None = None
    prose: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "created", _ts(self.created, "Thesis.created"))
        # Canonicalise nested dicts (timestamps -> ISO strings, numpy -> python)
        # at the boundary, so an in-memory Thesis equals its own file round-trip.
        object.__setattr__(
            self, "model_verdict_at_creation", _plain(self.model_verdict_at_creation)
        )
        if self.resolution is not None:
            object.__setattr__(self, "resolution", _plain(self.resolution))
        if self.horizon_gws < 1:
            raise ValueError("horizon_gws must be >= 1")
        if not 1 <= self.gw_start <= 38:
            raise ValueError(f"gw_start out of range: {self.gw_start}")
        if self.claim_type is ClaimType.WATCH:
            if self.falsifiable_prediction is not None:
                raise ValueError(
                    "a watch thesis carries no falsifiable_prediction -- it is a "
                    "note, not a fake claim. Use a real claim_type to predict."
                )
        else:
            if not self.falsifiable_prediction:
                raise ValueError(
                    f"claim_type={self.claim_type} requires a falsifiable_prediction "
                    "from the grammar; use claim_type=watch for unfalsifiable ideas"
                )
            # Import here to avoid a cycle: grammar grades Thesis objects.
            from fpl_edge.theses.grammar import parse

            parse(self.falsifiable_prediction)  # raises UngradeableClaimError
        if int(self.player_code) <= 0:
            raise ValueError("player_code must be a positive stable FPL code")

    @property
    def gw_end(self) -> int:
        return self.gw_start + self.horizon_gws - 1

    @property
    def gw_range(self) -> range:
        return range(self.gw_start, self.gw_end + 1)

    @property
    def window_label(self) -> str:
        if self.horizon_gws == 1:
            return f"GW{self.gw_start}"
        return f"GW{self.gw_start}-GW{self.gw_end}"

    @property
    def outcome(self) -> ThesisOutcome | None:
        if not self.resolution:
            return None
        return ThesisOutcome(str(self.resolution["outcome"]))

    @property
    def scoreboard_key(self) -> str:
        """Who gets credit: the named creator if there is one, else the channel."""
        return self.creator or str(self.source)

    # -- serialisation -------------------------------------------------------

    def to_markdown(self) -> str:
        front: dict[str, Any] = {
            "id": self.id,
            "created": _iso(self.created),
            "source": str(self.source),
            "creator": self.creator,
            "raw_input": self.raw_input,
            "player": self.player,
            "player_code": int(self.player_code),
            "season": self.season,
            "claim_type": str(self.claim_type),
            "gw_start": int(self.gw_start),
            "gw_end": int(self.gw_end),
            "horizon_gws": int(self.horizon_gws),
            "falsifiable_prediction": self.falsifiable_prediction,
            "comparator_codes": [int(c) for c in self.comparator_codes],
            "comparator_label": self.comparator_label,
            "model_verdict_at_creation": _plain(self.model_verdict_at_creation),
            "acted": bool(self.acted),
            "idea_id": self.idea_id,
            "status": str(self.status),
        }
        if self.resolution is not None:
            front["resolution"] = _plain(self.resolution)
        ordered = {k: front[k] for k in _KEY_ORDER if k in front}
        body = yaml.safe_dump(
            ordered, sort_keys=False, allow_unicode=True, width=88, default_flow_style=False
        )
        prose = self.prose.rstrip()
        return f"---\n{body}---\n\n{prose}\n" if prose else f"---\n{body}---\n"

    @classmethod
    def from_markdown(cls, text: str) -> "Thesis":
        m = _FRONT_MATTER.match(text)
        if not m:
            raise ValueError("not a thesis file: no YAML front matter")
        raw = yaml.safe_load(m.group(1))
        if not isinstance(raw, dict):
            raise ValueError("front matter did not parse to a mapping")
        prose = text[m.end():].strip()

        declared_end = raw.get("gw_end")
        gw_start = int(raw["gw_start"])
        horizon = int(raw["horizon_gws"])
        if declared_end is not None and int(declared_end) != gw_start + horizon - 1:
            raise ValueError(
                f"inconsistent window: gw_start={gw_start}, horizon_gws={horizon} "
                f"but gw_end={declared_end}"
            )

        resolution = raw.get("resolution")
        if resolution is not None and "resolved" in resolution:
            resolution = dict(resolution)
            resolution["resolved"] = _iso(_ts(resolution["resolved"], "resolution.resolved"))

        return cls(
            id=str(raw["id"]),
            created=_ts(raw["created"], "created"),
            source=ThesisSource(str(raw["source"])),
            creator=(str(raw["creator"]) if raw.get("creator") else None),
            raw_input=str(raw["raw_input"]),
            player=str(raw["player"]),
            player_code=int(raw["player_code"]),
            season=str(raw["season"]),
            claim_type=ClaimType(str(raw["claim_type"])),
            gw_start=gw_start,
            horizon_gws=horizon,
            falsifiable_prediction=(
                str(raw["falsifiable_prediction"])
                if raw.get("falsifiable_prediction") else None
            ),
            comparator_codes=tuple(int(c) for c in (raw.get("comparator_codes") or ())),
            comparator_label=str(raw.get("comparator_label") or ""),
            model_verdict_at_creation=dict(raw.get("model_verdict_at_creation") or {}),
            acted=bool(raw.get("acted", False)),
            idea_id=(str(raw["idea_id"]) if raw.get("idea_id") else None),
            status=ThesisStatus(str(raw.get("status", "open"))),
            resolution=(dict(resolution) if resolution else None),
            prose=prose,
        )

    def resolved(
        self, *, outcome: ThesisOutcome, resolved_utc: dt.datetime, **details: Any
    ) -> "Thesis":
        """A copy with the resolution written in. The creation block is untouched."""
        block: dict[str, Any] = {
            "outcome": str(outcome),
            "resolved": _iso(_ts(resolved_utc, "resolved_utc")),
        }
        block.update(_plain(details))
        status = ThesisStatus.VOID if outcome is ThesisOutcome.VOID else ThesisStatus.RESOLVED
        return replace(self, status=status, resolution=block)


def slugify(*parts: str) -> str:
    joined = "-".join(p for p in parts if p)
    folded = re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")
    return re.sub(r"-{2,}", "-", folded) or "thesis"


def make_thesis_id(created: dt.datetime, player: str, claim_type: ClaimType) -> str:
    """``2026-08-18-rashford-minutes``: date-sorted, human-greppable."""
    when = _ts(created, "created")
    return f"{when:%Y-%m-%d}-{slugify(player, str(claim_type))}"
