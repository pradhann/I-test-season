"""The hypothesis registry: every belief becomes a versioned file that gets graded.

A thesis is a markdown file under ``theses/open/`` with YAML front matter. It
records who believed what, when, in a form a machine can grade after the
gameweeks it covers have finalised -- at which point the file moves to
``theses/resolved/`` with its outcome written in, the per-source scoreboard under
``theses/scoreboard/`` is updated, and the whole transition is a git commit. The
project's beliefs therefore compound in version control instead of drifting in
memory.

Three invariants, enforced in code rather than by convention:

* **Falsifiable or watch.** ``falsifiable_prediction`` is a string drawn from a
  closed grammar (:mod:`fpl_edge.theses.grammar`); every template has a grader.
  An idea that cannot be made falsifiable is stored as ``claim_type: watch``
  with a note -- never with a fake prediction.
* **The model's verdict is captured at creation.** ``model_verdict_at_creation``
  is read from a :class:`~fpl_edge.store.Snapshot` at the creation instant and
  never touched again. Backfilling it later would be leakage, and a test proves
  a thesis created at time T carries only data visible at T.
* **The comparator is frozen at creation.** "Beats his price peers" means the
  peers as they stood when the claim was made, stored as codes in the file, so
  resolution cannot quietly pick a comparator that chases the outcome.
"""

from fpl_edge.theses.create import (
    PlayerResolutionError,
    create_thesis,
    sync_from_registry,
    thesis_from_idea,
)
from fpl_edge.theses.grammar import Grade, UngradeableClaimError, grade, parse
from fpl_edge.theses.model import (
    ClaimType,
    Thesis,
    ThesisOutcome,
    ThesisSource,
    ThesisStatus,
)
from fpl_edge.theses.resolve import ResolveReport, resolve_theses
from fpl_edge.theses.scoreboard import source_weights
from fpl_edge.theses.store import DEFAULT_THESES_DIR, ThesesStore

__all__ = [
    "DEFAULT_THESES_DIR",
    "ClaimType",
    "Grade",
    "PlayerResolutionError",
    "ResolveReport",
    "ThesesStore",
    "Thesis",
    "ThesisOutcome",
    "ThesisSource",
    "ThesisStatus",
    "UngradeableClaimError",
    "create_thesis",
    "grade",
    "parse",
    "resolve_theses",
    "source_weights",
    "sync_from_registry",
    "thesis_from_idea",
]
