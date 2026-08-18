"""The static leakage audit, and tests that the audit itself works.

Hunt list item 1. ``scripts/audit_leakage.py`` walks the AST of every module in
``fpl_edge/`` and flags model code that reads the warehouse directly, escapes a
Snapshot, reads the wall clock, builds naive datetimes, joins on ``element_id``,
fabricates values with ``fillna``, evaluates without walk-forward, double counts
bonus, or draws from the unseeded global RNG.

A static audit is only worth running if it is believed, and it is only believed
if it is (a) proven to fire and (b) not screaming. So this file does two things:

1. ``test_every_rule_fires`` feeds the auditor a module containing one instance
   of each violation and asserts each rule triggers. An audit that has silently
   stopped working is worse than no audit.
2. ``test_no_unreviewed_findings`` compares the live tree against ACCEPTED, a
   per-(rule, file) baseline with a written reason for each entry. Anything the
   auditor finds outside that baseline fails the suite. New modules and new rule
   classes therefore cannot land quietly; existing reviewed cases do not block
   other teams.

The baseline is keyed on (rule, path) and not on line numbers, because line
numbers move under a tree four teams are writing to.
"""

from __future__ import annotations

import textwrap

import pytest

from .conftest import REPO_ROOT, load_audit_script

audit = load_audit_script()


#: Findings that have been read and judged acceptable, with the reason.
#: Adding an entry here is a review decision, not a formality. Removing code
#: never requires touching this list; adding a NEW violating file does.
ACCEPTED: dict[tuple[str, str], str] = {
    ("DIRECT_TABLE", "fpl_edge/models/minutes/dataset.py"):
        "fixture LOADER: names the six tables to write committed CSVs into a "
        "real warehouse, then hands out Snapshots. Writes, never reads.",
    ("DIRECT_DB", "fpl_edge/models/minutes/dataset.py"):
        "same: constructs the Warehouse it is populating.",
    ("SIDE_CHANNEL", "fpl_edge/models/minutes/dataset.py"):
        "reads the committed fixture CSVs in order to load them INTO the "
        "warehouse; the model then reads through snapshot_at.",
    ("DIRECT_TABLE", "fpl_edge/models/team_goals/synthetic.py"):
        "synthetic-league generator: materialises a known-ground-truth league "
        "into a warehouse for parameter-recovery tests.",
    ("DIRECT_DB", "fpl_edge/models/team_goals/synthetic.py"):
        "same generator.",
    ("SIDE_CHANNEL", "fpl_edge/models/team_goals/synthetic.py"):
        "reads its own committed synthetic CSVs to rebuild the league.",
    ("FILL_CONST", "fpl_edge/models/minutes/features.py"):
        "REVIEWED, PARTIALLY UNSAFE. Most are zero-filling a full "
        "player x fixture grid where an absent row genuinely means 0 minutes. "
        "Two are not: days_rest.fillna(7.0) invents a rest period and "
        "status.fillna('a') assumes an unknown player is fit. Both are "
        "recorded in docs/known_weaknesses.md.",
    ("FILL_CONST", "fpl_edge/interfaces/parsing.py"):
        "ownership fillna(0.0) orders a clarification prompt only; it has no "
        "vote in the decision. Still fabricates 0% for an unparseable value.",
    ("FILL_CONST", "fpl_edge/ingest/vaastav.py"):
        "fillna(False) on a boolean presence flag, not a rate.",
    ("FILL_CONST", "fpl_edge/eval/baselines.py"):
        "baseline scorers; a fabricated 0 makes the baseline WEAKER, which "
        "biases against the thing being measured rather than for it.",
    ("FILL_CONST", "fpl_edge/interfaces/features.py"):
        "REVIEWED: total_points/minutes zero-fill where a missing row means the "
        "player did not feature, which is the FPL-correct semantics rather than "
        "a fabricated observation.",
    ("SILENT_DROPNA", "fpl_edge/interfaces/features.py"):
        "REVIEWED: dropna on the columns being averaged for a population "
        "summary, so the mean is over observed values rather than over an "
        "invented zero. Not a training-set filter.",
    ("SIDE_CHANNEL", "fpl_edge/models/team_goals/odds.py"):
        "reads a committed odds CSV. FLAGGED IN docs/known_weaknesses.md: a "
        "bookmaker price file carries no as_of and the snapshot cannot police "
        "when each quote was observable.",
    ("FILL_CONST", "fpl_edge/sim/synthetic.py"):
        "synthetic-league generator with known ground truth, not a data path.",
    ("DIRECT_DB", "fpl_edge/sim/synthetic.py"):
        "same generator: constructs the warehouse it populates.",
    ("FILL_CONST", "fpl_edge/ingest/injuries.py"):
        "REVIEWED, OPTIMISTIC. play_prob falls back to 1.0 when neither an "
        "explicit chance_of_playing nor a status prior is available, i.e. an "
        "unknown player is assumed certain to start. Recorded in "
        "docs/known_weaknesses.md.",
    ("FILL_CONST", "fpl_edge/interfaces/bias.py"):
        "REVIEWED: fillna(99) is a sentinel for 'no haul on record' in a "
        "gws-since-haul column, fillna(False) on boolean flags. Bias reporting, "
        "not a model input.",
    ("SILENT_DROPNA", "fpl_edge/interfaces/bias.py"):
        "REVIEWED: dropna over the two columns being correlated in a bias "
        "report, so the correlation is over observed pairs.",
    ("SILENT_DROPNA", "fpl_edge/models/minutes/evaluate.py"):
        "REVIEWED: drops empty reliability-diagram bins, not training rows.",
    ("DIRECT_DB", "fpl_edge/models/minutes/evaluate.py"):
        "an argparse entry point that opens the warehouse in order to mint "
        "Snapshots for the walk-forward run. Reads go through snapshot_at.",
    ("HARDCODED_DEADLINE", "fpl_edge/models/minutes/evaluate.py"):
        "REVIEWED: --catalog-at CLI default. A hardcoded as-of default silently "
        "freezes the evaluation to 2026-08-18 for anyone who omits the flag.",
    ("DIRECT_DB", "fpl_edge/models/team_goals/evaluate.py"):
        "same: evaluation entry point opening the warehouse read-only.",
    ("WALL_CLOCK", "fpl_edge/models/team_goals/evaluate.py"):
        "REVIEWED: run_real() is a diagnostic that reports what the live "
        "warehouse contains NOW; it is not a training or prediction path.",
    ("SIDE_CHANNEL", "fpl_edge/models/ownership/panel.py"):
        "reads a committed ownership panel parquet. FLAGGED IN "
        "docs/known_weaknesses.md: the panel carries no as_of, so point-in-time "
        "discipline cannot be enforced on it.",
    ("FILL_CONST", "fpl_edge/sim/engine.py"):
        "REVIEWED: _align() reindexes a column onto the player universe and "
        "zero-fills. Correct for expected points; for effective ownership it "
        "makes an unknown player a pure differential. See known_weaknesses.",
    ("DIRECT_DB", "fpl_edge/cli/main.py"):
        "the CLI opens the warehouse to mint a Snapshot; that is its job.",
    ("HARDCODED_DEADLINE", "fpl_edge/cli/main.py"):
        "false positive: the literal is inside an error message telling the "
        "user what a valid --as-of looks like.",
    ("DIRECT_DB", "fpl_edge/interfaces/testing.py"):
        "test-fixture builder: constructs a warehouse for interface tests.",
    ("DIRECT_TABLE", "fpl_edge/interfaces/testing.py"):
        "same builder: names the tables it writes.",
    ("FILL_CONST", "fpl_edge/interfaces/testing.py"):
        "same builder: defaults for synthetic rows.",
    ("WALL_CLOCK", "fpl_edge/models/ownership/elite.py"):
        "REVIEWED, LEAK RISK. EliteSample stamps as_of = wall clock when "
        "scraping top-10k picks. Correct for a live sample; a hard leak in any "
        "backtest, because FPL does not publish a manager's picks until AFTER "
        "the deadline. See docs/known_weaknesses.md sec 11.",
    ("SNAPSHOT_ESCAPE", "fpl_edge/models/ownership/model.py"):
        "REVIEWED, SAFE BUT WRONG SHAPE. _prior_players() reaches "
        "snapshot.warehouse only to mint an EARLIER snapshot, and an earlier "
        "as-of can reveal strictly less, so it does not leak. It is baselined "
        "rather than fixed because the right fix is on the Snapshot: it should "
        "expose a `rewind(timedelta)` so no model ever needs .warehouse. Until "
        "it does, this rule cannot be enforced without exceptions.",
    ("FILL_CONST", "fpl_edge/models/ownership/model.py"):
        "REVIEWED: transfer-flow counts and ownership deltas where an absent "
        "row means no transfers, not unknown transfers.",
    ("DIRECT_DB", "fpl_edge/sim/calibration.py"):
        "REVIEWED, IN-SAMPLE RISK. Opens the warehouse to recompute the "
        "simulator's verified anchors. See below.",
    ("DIRECT_TABLE", "fpl_edge/sim/calibration.py"):
        "REVIEWED, IN-SAMPLE RISK. Aggregates a WHOLE SEASON of "
        "fact_player_fixture with no snapshot, to calibrate the constants the "
        "field model samples from. That is defensible for a one-off constant "
        "and indefensible if the calibration season overlaps a backtest "
        "window: the anchors then encode the answer. Recorded in "
        "docs/known_weaknesses.md sec 10.",
    ("FILL_CONST", "fpl_edge/sim/field.py"):
        "REVIEWED: zero-filling ownership for players the field model has no "
        "row for. See docs/known_weaknesses.md -- an unknown-ownership player "
        "treated as 0% owned is scored as a pure differential.",
}


def _findings():
    return audit.audit_tree(REPO_ROOT)


def test_no_unreviewed_findings() -> None:
    """GUARDS: a new model module reading the warehouse without a Snapshot.

    Fails on any (rule, file) pair not in ACCEPTED. The message names the exact
    file:line so the owning team can fix it or argue for a baseline entry.
    """
    unreviewed = [f for f in _findings() if (f.rule, f.path) not in ACCEPTED]
    assert not unreviewed, (
        "static leakage audit found unreviewed violations:\n  "
        + "\n  ".join(f.render() for f in unreviewed)
        + "\n\nEither fix them, or add (rule, path) to ACCEPTED in "
          "tests/audit/test_static_leakage_audit.py with a written reason."
    )


def test_baseline_does_not_rot() -> None:
    """GUARDS: the baseline quietly becoming a blanket exemption.

    Every ACCEPTED entry must still correspond to a real finding. Once a team
    fixes something, the exemption must be deleted rather than left to cover
    whatever lands in that file next.
    """
    live = {(f.rule, f.path) for f in _findings()}
    stale = sorted(k for k in ACCEPTED if k not in live)
    assert not stale, (
        "these ACCEPTED entries no longer match any finding and must be "
        f"deleted so they cannot silently cover future code: {stale}"
    )


# ---------------------------------------------------------------------------
# The audit must be proven to fire. One offending construct per rule.
# ---------------------------------------------------------------------------

OFFENDING_MODULE = textwrap.dedent(
    '''
    """A module that commits every sin the auditor is supposed to catch."""
    import datetime as dt
    import numpy as np
    import pandas as pd
    from sklearn.model_selection import train_test_split

    RULES_URL = "https://fantasy.premierleague.com/help/rules"
    BOOTSTRAP = "https://fantasy.premierleague.com/api/bootstrap-static/"
    GW1 = "2026-08-21T17:30"

    def go(snapshot, con):
        raw = con.sql("SELECT * FROM fact_player_fixture")
        leak = snapshot.warehouse.sql("SELECT * FROM fact_fixture")
        side = pd.read_parquet("/tmp/prices.parquet")
        now = dt.datetime.now()
        naive = dt.datetime(2026, 8, 21, 17, 30)
        stale = now.replace(tzinfo=None)
        parsed = pd.to_datetime(raw["as_of"])
        joined = raw.merge(side, on="element_id")
        grouped = raw.groupby("element_id")
        filled = raw.fillna(0)
        meaned = raw.fillna(raw.mean())
        dropped = raw.dropna()
        tr, te = train_test_split(raw)
        noise = np.random.normal(0, 1, 10)
        double = raw["total_points"] + raw["bonus"]
        sql_join = "SELECT * FROM a JOIN b ON a.element_id = b.element_id"
        return leak, naive, stale, parsed, joined, grouped, filled, meaned, \\
            dropped, tr, te, noise, double, sql_join, GW1, RULES_URL, BOOTSTRAP
    '''
)

EXPECTED_RULES = {
    "DIRECT_TABLE",
    "SNAPSHOT_ESCAPE",
    "SIDE_CHANNEL",
    "WALL_CLOCK",
    "NAIVE_DATETIME",
    "JOIN_ELEMENT_ID",
    "FILL_CONST",
    "LEAKY_IMPUTE",
    "SILENT_DROPNA",
    "NOT_WALK_FORWARD",
    "GLOBAL_RNG",
    "BONUS_DOUBLE_COUNT",
    "RULES_PAGE",
    "HARDCODED_DEADLINE",
    "CURRENT_BOOTSTRAP",
}


@pytest.fixture()
def offending(tmp_path):
    pkg = tmp_path / "fpl_edge" / "models"
    pkg.mkdir(parents=True)
    path = pkg / "offender.py"
    path.write_text(OFFENDING_MODULE)
    return tmp_path, path


def test_every_rule_fires(offending) -> None:
    """GUARDS: the auditor silently ceasing to detect anything.

    A static check that has stopped matching is indistinguishable from a clean
    codebase, and looks better. This is the test that tells them apart.
    """
    root, path = offending
    got = {f.rule for f in audit.audit_file(path, root=root)}
    missing = sorted(EXPECTED_RULES - got)
    assert not missing, f"auditor no longer detects: {missing} (detected: {sorted(got)})"


def test_docstrings_are_not_treated_as_code(tmp_path) -> None:
    """GUARDS: the auditor crying wolf about prose.

    ``fpl_edge/models/team_goals/data.py`` explains ``fact_fixture`` at length in
    its docstring while reading it only through ``snapshot.table()``. An auditor
    that cannot tell those apart gets switched off, and then it protects nothing.
    """
    pkg = tmp_path / "fpl_edge" / "models"
    pkg.mkdir(parents=True)
    path = pkg / "prose.py"
    path.write_text(
        '"""Reads fact_fixture and fact_player_fixture through a Snapshot."""\n'
        "def go(snapshot):\n"
        '    """Explains fact_player_state at length."""\n'
        '    return snapshot.table("fact_fixture")\n'
    )
    assert audit.audit_file(path, root=tmp_path) == []


def test_suppression_requires_naming_the_rule(tmp_path) -> None:
    """GUARDS: a blanket ``# noqa``-style escape hatch.

    A suppression must name the specific rule it silences, so a line suppressed
    for ``FILL_CONST`` still trips ``SILENT_DROPNA``.
    """
    pkg = tmp_path / "fpl_edge" / "models"
    pkg.mkdir(parents=True)
    path = pkg / "supp.py"
    path.write_text(
        "def go(df):\n"
        "    a = df.fillna(0)  # audit: allow FILL_CONST counts, not a rate\n"
        "    b = df.fillna(0)  # audit: allow SILENT_DROPNA wrong rule named\n"
        "    return a, b\n"
    )
    rules = [f.rule for f in audit.audit_file(path, root=tmp_path)]
    assert rules == ["FILL_CONST"], (
        f"expected exactly the un-suppressed FILL_CONST, got {rules}"
    )


def test_store_zone_is_allowed_to_touch_tables(tmp_path) -> None:
    """GUARDS: the audit being so strict the warehouse itself cannot be written.

    ``fpl_edge/store`` is the one sanctioned place for direct SQL. If this ever
    starts failing, the zoning is broken and every other result is suspect.
    """
    pkg = tmp_path / "fpl_edge" / "store"
    pkg.mkdir(parents=True)
    path = pkg / "w.py"
    path.write_text(
        "def go(con):\n"
        '    return con.execute("SELECT * FROM fact_player_fixture")\n'
    )
    assert [f.rule for f in audit.audit_file(path, root=tmp_path)] == []
