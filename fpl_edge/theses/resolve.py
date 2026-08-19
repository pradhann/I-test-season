"""Resolution: grade what the gameweek settled, move the files, commit the truth.

Runs after every gameweek (``make resolve-gw``). For each open thesis whose
window has fully finalised in the warehouse *as visible at the run instant*:

1. grade the falsifiable prediction with its template's grader,
2. write the outcome -- including the counterfactual value of the ideas the
   user never acted on -- into the file and move it open/ -> resolved/,
3. recompute the scoreboard from all resolved files,
4. make one git commit that says in its message what was right and what was
   wrong, so ``git log theses/`` reads as the season's honest ledger.

Grading reads realised results through a Snapshot at the run instant, so a
half-finished gameweek is invisible and a re-run after corrections lands as a
new commit rather than silent drift. The comparator is never rebuilt: the codes
frozen at creation are the only members whose points are read.

``--dry-run`` renders everything -- grades, moves, scoreboard, the exact commit
message -- and touches nothing, which is how the engine is validated against a
past season before it is trusted with this one.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from fpl_edge.store import Warehouse
from fpl_edge.theses import scoreboard as sb
from fpl_edge.theses.grammar import Grade, window_points
from fpl_edge.theses.grammar import grade as grade_thesis
from fpl_edge.theses.model import ClaimType, Thesis, ThesisOutcome
from fpl_edge.theses.store import ThesesStore

UTC = dt.timezone.utc

GIT_IDENT = ("-c", "user.name=Nripesh", "-c", "user.email=nripeshpradhan@gmail.com")


@dataclass(frozen=True, slots=True)
class GradedThesis:
    thesis: Thesis          # with resolution written in
    grade: Grade | None     # None for unscored watches
    old_path: Path
    new_path: Path


@dataclass(frozen=True, slots=True)
class ResolveReport:
    season: str
    as_of: dt.datetime
    dry_run: bool
    graded: tuple[GradedThesis, ...] = ()
    still_open: tuple[str, ...] = ()
    synced: tuple[str, ...] = ()
    scoreboard: tuple[sb.Record, ...] = ()
    commit_message: str = ""
    committed: str | None = None  # short sha when a commit was made
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def counts(self) -> dict[str, int]:
        out = {o.value: 0 for o in ThesisOutcome}
        for g in self.graded:
            outcome = g.thesis.outcome
            if outcome:
                out[outcome.value] += 1
        return out

    def render(self) -> str:
        mode = " (DRY RUN — nothing was written)" if self.dry_run else ""
        lines = [
            (
                f"Thesis resolution — {self.season}, as of "
                f"{self.as_of:%Y-%m-%d %H:%M}Z{mode}"
            ),
            "",
        ]
        if self.synced:
            lines.append(f"Synced {len(self.synced)} idea(s) from the registry: "
                         + ", ".join(self.synced))
            lines.append("")
        if not self.graded:
            lines.append("Nothing to settle: no open thesis has a fully finalised window.")
        else:
            lines.append(f"Settled {len(self.graded)}:")
            for g in self.graded:
                t = g.thesis
                res = t.resolution or {}
                lines.append(f"  [{res.get('outcome'):>9}] {t.id}")
                lines.append(f"              claim: {t.falsifiable_prediction or '(watch)'}")
                if g.grade is not None:
                    lines.append(f"              {g.grade.detail}")
                if res.get("counterfactual"):
                    lines.append(f"              {res['counterfactual']}")
                move = "would move" if self.dry_run else "moved"
                lines.append(f"              {move}: {g.old_path} -> {g.new_path}")
        if self.still_open:
            lines.append("")
            lines.append(f"Still open ({len(self.still_open)}): " + ", ".join(self.still_open))
        if self.scoreboard:
            lines.append("")
            lines.append("Scoreboard after this run:")
            for r in self.scoreboard:
                hit = "no scored claims yet" if r.hit_rate is None else (
                    f"{r.correct}/{r.sample} scored claims correct ({r.hit_rate:.0%})"
                )
                extra = f", hesitancy cost {r.hesitancy_cost_pts:+.1f} pts" \
                    if r.hesitancy_cost_pts else ""
                lines.append(f"  {r.entity_type} {r.entity}: {hit}{extra}")
        if self.commit_message:
            lines.append("")
            header = "Would commit:" if self.dry_run else (
                f"Committed {self.committed}:" if self.committed else "Commit skipped:"
            )
            lines.append(header)
            lines.extend("  | " + line for line in self.commit_message.splitlines())
        for note in self.notes:
            lines.append("")
            lines.append(f"NOTE: {note}")
        return "\n".join(lines)


def _finished_results(warehouse: Warehouse, season: str, as_of: dt.datetime) -> pd.DataFrame:
    """All finalised per-fixture rows visible at ``as_of``, full columns --
    graders need starts/goals/assists, not just points."""
    return warehouse.snapshot_at(as_of).results_before(season)


def _gws_finished(results: pd.DataFrame) -> set[int]:
    return set() if results.empty else {int(g) for g in results["gw"].dropna().unique()}


def _counterfactual(thesis: Thesis, grade: Grade) -> str:
    """What following (or dodging) this call was worth, for the hesitancy ledger."""
    margin = grade.margin
    if margin is None:
        return ""
    if thesis.claim_type is ClaimType.AVOID:
        worth = grade.comparator_points - grade.subject_points \
            if grade.comparator_points is not None and grade.subject_points is not None \
            else -margin
        if thesis.acted:
            return f"Acted on. Avoiding was worth {worth:+.1f} pts vs the frozen alternative."
        return (
            f"Not acted on. Avoiding would have been worth {worth:+.1f} pts "
            f"vs the frozen alternative."
        )
    if thesis.acted:
        return f"Acted on. Following it was worth {margin:+.1f} pts vs the frozen comparator."
    return (
        f"NOT acted on. Following it would have been worth {margin:+.1f} pts "
        f"vs the frozen comparator over {thesis.window_label} — the cost of hesitancy."
    )


def _watch_summary(thesis: Thesis, results: pd.DataFrame) -> str:
    pts = window_points(results, [thesis.player_code], thesis.gw_range)
    total = float(pts.iloc[0]) if not pts.empty else 0.0
    return (
        f"Watch expired: {thesis.player} scored {total:.0f} pts over "
        f"{thesis.window_label}. No claim was made, so nothing is scored."
    )


def _commit_message(
    season: str, graded: list[GradedThesis], records: list[sb.Record], last_gw: int
) -> str:
    counts = {o: 0 for o in ThesisOutcome}
    for g in graded:
        outcome = g.thesis.outcome
        if outcome:
            counts[outcome] += 1
    summary_bits = [
        f"{counts[ThesisOutcome.CORRECT]} correct",
        f"{counts[ThesisOutcome.INCORRECT]} incorrect",
    ]
    for label, key in (("push", ThesisOutcome.PUSH), ("unscored", ThesisOutcome.UNSCORED),
                       ("void", ThesisOutcome.VOID)):
        if counts[key]:
            summary_bits.append(f"{counts[key]} {label}")
    lines = [
        f"theses: settle {len(graded)} through {season} GW{last_gw} — "
        + ", ".join(summary_bits),
        "",
    ]
    order = {
        ThesisOutcome.CORRECT: 0, ThesisOutcome.INCORRECT: 1, ThesisOutcome.PUSH: 2,
        ThesisOutcome.UNSCORED: 3, ThesisOutcome.VOID: 4,
    }
    for g in sorted(graded, key=lambda g: order[g.thesis.outcome or ThesisOutcome.VOID]):
        t = g.thesis
        res = t.resolution or {}
        tag = str(res.get("outcome", "?")).upper()
        if g.grade is not None and g.grade.subject_points is not None \
                and g.grade.comparator_points is not None:
            score = f" ({g.grade.subject_points:.0f} vs {g.grade.comparator_points:.0f})"
        elif g.grade is not None:
            score = f" ({g.grade.observed:.0f} of {g.grade.target:.0f} needed)"
        else:
            score = ""
        lines.append(f"{tag:9s} {t.id} [{t.scoreboard_key}]: "
                     f"{t.falsifiable_prediction or 'watch expired'}{score}")
    scored = [r for r in records if r.entity_type == "source" and r.sample]
    if scored:
        lines.append("")
        lines.append(
            "scoreboard: "
            + "; ".join(f"{r.entity} {r.correct}/{r.sample}" for r in scored)
        )
    return "\n".join(lines)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def _repo_root(start: Path) -> Path | None:
    proc = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    return Path(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else None


def _commit(store: ThesesStore, message: str) -> tuple[str | None, str | None]:
    """Stage only this store's files and commit. Returns (short_sha, problem)."""
    base = store.base.resolve()
    repo = _repo_root(base)
    if repo is None:
        return None, f"{base} is not inside a git repository; commit skipped"
    add = _git(repo, "add", "--", str(base))
    if add.returncode != 0:
        return None, f"git add failed: {add.stderr.strip()}"
    staged = _git(repo, "diff", "--cached", "--quiet")
    if staged.returncode == 0:
        return None, "nothing staged; commit skipped"
    commit = _git(repo, *GIT_IDENT, "commit", "-m", message)
    if commit.returncode != 0:
        return None, f"git commit failed: {commit.stderr.strip()}"
    sha = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    return sha or None, None


def resolve_theses(
    warehouse: Warehouse,
    *,
    season: str,
    as_of: dt.datetime | None = None,
    store: ThesesStore | None = None,
    dry_run: bool = False,
    commit: bool = True,
    sync_registry: bool = True,
) -> ResolveReport:
    """One resolution pass. Idempotent: an already-resolved thesis is gone from
    open/ and an unfinished window stays put untouched."""
    store = store or ThesesStore()
    when = (as_of or dt.datetime.now(UTC)).astimezone(UTC)
    notes: list[str] = []

    synced: list[str] = []
    if sync_registry and not dry_run:
        try:
            from fpl_edge.theses.create import sync_from_registry

            synced = [t.id for t, _ in sync_from_registry(warehouse, season=season, store=store)]
        except Exception as exc:
            notes.append(f"registry sync failed and was skipped: {exc}")

    results = _finished_results(warehouse, season, when)
    finished = _gws_finished(results)
    last_gw = max(finished) if finished else 0

    graded: list[GradedThesis] = []
    still_open: list[str] = []

    for thesis, path in store.load_open():
        if thesis.season != season:
            still_open.append(thesis.id)
            continue
        if not set(thesis.gw_range) <= finished:
            still_open.append(thesis.id)
            continue

        if thesis.claim_type is ClaimType.WATCH:
            done = thesis.resolved(
                outcome=ThesisOutcome.UNSCORED,
                resolved_utc=when,
                detail=_watch_summary(thesis, results),
            )
            graded.append(
                GradedThesis(done, None, path, store.resolved_dir / path.name)
            )
            continue

        g = grade_thesis(thesis, results)
        done = thesis.resolved(
            outcome=g.outcome,
            resolved_utc=when,
            observed=g.observed,
            target=g.target,
            subject_points=g.subject_points,
            comparator_points=g.comparator_points,
            margin=g.margin,
            detail=g.detail,
            counterfactual=_counterfactual(thesis, g),
            graded_gws=f"GW{thesis.gw_start}-GW{thesis.gw_end}",
        )
        graded.append(GradedThesis(done, g, path, store.resolved_dir / path.name))

    # Scoreboard: everything already resolved plus what this run resolves.
    prior = [t for t, _ in store.load_resolved()]
    records = sb.compute(prior + [g.thesis for g in graded])

    message = ""
    if graded:
        message = _commit_message(season, graded, records, last_gw)

    committed: str | None = None
    if not dry_run:
        for g in graded:
            store.move_resolved(g.thesis)
        if graded or synced:
            sb.write(store.scoreboard_dir, records, run_utc=when)
        if commit and (graded or synced):
            if not message:
                message = f"theses: sync {len(synced)} idea(s) from the registry"
            committed, problem = _commit(store, message)
            if problem:
                notes.append(problem)

    return ResolveReport(
        season=season,
        as_of=when,
        dry_run=dry_run,
        graded=tuple(graded),
        still_open=tuple(still_open),
        synced=tuple(synced),
        scoreboard=tuple(records),
        commit_message=message,
        committed=committed,
        notes=tuple(notes),
    )
