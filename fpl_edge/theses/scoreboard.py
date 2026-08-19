"""Per-source and per-creator accuracy, in the shape the oracle already eats.

The scoreboard is *derived state*: it is recomputed from every file under
``theses/resolved/`` on each run rather than incremented, so it can never drift
from the files and a corrupted scoreboard is fixed by rerunning resolve.

Two artifacts are written:

* ``sources.json`` -- the current cumulative record per source channel and per
  named creator. Each row carries exactly the fields
  :class:`fpl_edge.oracle.signals.SourceWeight` needs (``source``, ``kind``,
  ``hit_rate``, ``sample``), so :func:`source_weights` is a one-line adapter and
  the oracle's "weights are earned, never assumed" rule is fed by measured
  thesis outcomes with no translation layer.
* ``history.csv`` -- one row per (resolve run, entity) with the cumulative
  record *at that run*, appended forever. Accuracy over time is a plot over
  this file; git blame gives the same answer, but nobody plots git blame.

Hit rate counts correct/(correct+incorrect). Pushes, voids and unscored watch
theses are reported but excluded -- a tie is not evidence either way, and
counting it would let a source farm accuracy from coin-flip claims.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from fpl_edge.oracle.signals import SourceKind, SourceWeight
from fpl_edge.theses.model import Thesis, ThesisOutcome, ThesisSource

UTC = dt.timezone.utc

#: Where each thesis channel lands in the oracle's signal-family taxonomy.
SOURCE_TO_KIND: dict[ThesisSource, SourceKind] = {
    ThesisSource.USER_CHAT: SourceKind.USER_IDEA,
    ThesisSource.CREATOR: SourceKind.CREATOR,
    ThesisSource.ELITE_MANAGER: SourceKind.ELITE_MANAGER,
    ThesisSource.MODEL: SourceKind.OWN_MODEL,
    ThesisSource.LLM_SCOUT: SourceKind.EXTERNAL_PROJECTION,
}

_HISTORY_FIELDS = (
    "run_utc", "entity", "entity_type", "kind", "resolved", "correct", "incorrect",
    "push", "unscored", "void", "hit_rate", "mean_margin", "hesitancy_cost_pts",
)


@dataclass(frozen=True, slots=True)
class Record:
    """Cumulative record for one entity (a source channel or a named creator)."""

    entity: str
    entity_type: str  # "source" | "creator"
    kind: SourceKind
    resolved: int = 0
    correct: int = 0
    incorrect: int = 0
    push: int = 0
    unscored: int = 0
    void: int = 0
    margin_sum: float = 0.0
    #: Points left on the table: margins of correct calls the user did not act on.
    hesitancy_cost_pts: float = 0.0

    @property
    def sample(self) -> int:
        return self.correct + self.incorrect

    @property
    def hit_rate(self) -> float | None:
        return self.correct / self.sample if self.sample else None

    @property
    def mean_margin(self) -> float | None:
        return self.margin_sum / self.sample if self.sample else None

    def as_row(self) -> dict:
        return {
            "entity": self.entity,
            "entity_type": self.entity_type,
            "kind": str(self.kind),
            "resolved": self.resolved,
            "correct": self.correct,
            "incorrect": self.incorrect,
            "push": self.push,
            "unscored": self.unscored,
            "void": self.void,
            "hit_rate": None if self.hit_rate is None else round(self.hit_rate, 4),
            "sample": self.sample,
            "mean_margin": None if self.mean_margin is None else round(self.mean_margin, 3),
            "hesitancy_cost_pts": round(self.hesitancy_cost_pts, 1),
        }


def _accumulate(records: dict[tuple[str, str], Record], thesis: Thesis) -> None:
    outcome = thesis.outcome
    if outcome is None:
        return
    keys = [(str(thesis.source), "source")]
    if thesis.creator:
        keys.append((thesis.creator, "creator"))
    kind = SOURCE_TO_KIND[thesis.source]
    for entity, entity_type in keys:
        rec = records.get((entity, entity_type)) or Record(entity, entity_type, kind)
        counts = {
            ThesisOutcome.CORRECT: "correct",
            ThesisOutcome.INCORRECT: "incorrect",
            ThesisOutcome.PUSH: "push",
            ThesisOutcome.UNSCORED: "unscored",
            ThesisOutcome.VOID: "void",
        }
        field = counts[outcome]
        margin = None
        if thesis.resolution and thesis.resolution.get("margin") is not None:
            margin = float(thesis.resolution["margin"])
        scored = outcome in (ThesisOutcome.CORRECT, ThesisOutcome.INCORRECT)
        hesitancy = (
            margin
            if outcome is ThesisOutcome.CORRECT and not thesis.acted and margin and margin > 0
            else 0.0
        )
        records[(entity, entity_type)] = Record(
            entity=rec.entity,
            entity_type=rec.entity_type,
            kind=rec.kind,
            resolved=rec.resolved + 1,
            correct=rec.correct + (field == "correct"),
            incorrect=rec.incorrect + (field == "incorrect"),
            push=rec.push + (field == "push"),
            unscored=rec.unscored + (field == "unscored"),
            void=rec.void + (field == "void"),
            margin_sum=rec.margin_sum + (margin or 0.0 if scored else 0.0),
            hesitancy_cost_pts=rec.hesitancy_cost_pts + (hesitancy or 0.0),
        )


def compute(resolved: list[Thesis]) -> list[Record]:
    records: dict[tuple[str, str], Record] = {}
    for thesis in resolved:
        _accumulate(records, thesis)
    return sorted(records.values(), key=lambda r: (r.entity_type, r.entity))


def render_json(records: list[Record], *, generated_utc: dt.datetime) -> str:
    payload = {
        "generated_utc": generated_utc.astimezone(UTC).replace(microsecond=0).isoformat(),
        "note": (
            "hit_rate counts correct/(correct+incorrect); pushes, voids and "
            "unscored watches are excluded. Fields source/kind/hit_rate/sample "
            "feed fpl_edge.oracle.signals.SourceWeight unchanged."
        ),
        "records": [r.as_row() for r in records],
    }
    return json.dumps(payload, indent=2) + "\n"


def write(
    scoreboard_dir: Path, records: list[Record], *, run_utc: dt.datetime
) -> tuple[Path, Path]:
    """Write sources.json (overwrite) and append this run to history.csv."""
    scoreboard_dir.mkdir(parents=True, exist_ok=True)
    sources = scoreboard_dir / "sources.json"
    sources.write_text(render_json(records, generated_utc=run_utc), encoding="utf-8")

    history = scoreboard_dir / "history.csv"
    fresh = not history.exists()
    with history.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_HISTORY_FIELDS)
        if fresh:
            writer.writeheader()
        stamp = run_utc.astimezone(UTC).replace(microsecond=0).isoformat()
        for r in records:
            writer.writerow(
                {
                    "run_utc": stamp,
                    "entity": r.entity,
                    "entity_type": r.entity_type,
                    "kind": str(r.kind),
                    "resolved": r.resolved,
                    "correct": r.correct,
                    "incorrect": r.incorrect,
                    "push": r.push,
                    "unscored": r.unscored,
                    "void": r.void,
                    "hit_rate": "" if r.hit_rate is None else f"{r.hit_rate:.4f}",
                    "mean_margin": "" if r.mean_margin is None else f"{r.mean_margin:.3f}",
                    "hesitancy_cost_pts": f"{r.hesitancy_cost_pts:.1f}",
                }
            )
    return sources, history


def source_weights(scoreboard_path: Path) -> dict[str, SourceWeight]:
    """Load ``sources.json`` as oracle-ready weights, keyed by entity name.

    A source with no resolved sample gets ``hit_rate=None`` and therefore
    ``weight == 0.0`` -- the oracle's own "no track record, no influence" rule,
    applied automatically because the shape matches.
    """
    payload = json.loads(Path(scoreboard_path).read_text(encoding="utf-8"))
    out: dict[str, SourceWeight] = {}
    for row in payload.get("records", ()):
        out[str(row["entity"])] = SourceWeight(
            source=str(row["entity"]),
            kind=SourceKind(str(row["kind"])),
            hit_rate=(None if row.get("hit_rate") is None else float(row["hit_rate"])),
            sample=int(row.get("sample", 0)),
        )
    return out
