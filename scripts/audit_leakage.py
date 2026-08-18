#!/usr/bin/env python
"""Static leakage / correctness audit for fpl-edge.

This is a grep-with-a-parser. It walks the AST of every module under
``fpl_edge/`` and refuses to let a model module do any of the things that
silently destroy an FPL backtest:

* read a warehouse table directly instead of going through ``Snapshot``
* reach the raw connection through ``snapshot.warehouse``
* ask the operating system what time it is (model time is ``snapshot.as_of``)
* build a naive datetime, or hand pandas a timestamp without ``utc=True``
* join anything on ``element_id`` (per-season, reassigned) rather than ``code``
* ``fillna(0)`` a rate statistic, or ``dropna()`` away the hard cases
* evaluate with a shuffled split instead of walk-forward
* add ``bonus`` to ``total_points``, which already contains it
* draw random numbers from the global, unseeded numpy RNG

Zones
-----
``store``   -- allowed to touch tables and connections. This is the sanctioned
               place for direct SQL, and the only one.
``ingest``  -- allowed to touch tables and to read the wall clock (``as_of`` is
               genuinely "now" at fetch time).
``model``   -- everything else. Maximum paranoia.

Usage::

    uv run python scripts/audit_leakage.py            # human output, exit 1 on findings
    uv run python scripts/audit_leakage.py --json     # machine readable

Suppress a finding with an inline comment on the offending line::

    df = raw.fillna(0)  # audit: allow FILL_CONST counts, not a rate

Owned by the adversarial audit team. Other teams: if this fires on your code,
the fix is almost never to add a suppression.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Physical warehouse tables. Naming one in a model module means the module is
#: reading the database rather than a point-in-time Snapshot.
WAREHOUSE_TABLES = frozenset({
    "dim_event", "dim_team", "dim_player",
    "fact_player_state", "fact_fixture", "fact_player_fixture", "fact_odds",
    "raw_fetch",
})

#: Modules allowed to hold a connection and name tables.
STORE_ZONE = ("fpl_edge/store/",)

#: Modules allowed to name tables *and* read the wall clock.
INGEST_ZONE = ("fpl_edge/ingest/",)

#: The human-facing layer. It may read the wall clock (a person typed a message
#: at a real instant) but it still may not touch tables or escape a Snapshot.
INTERFACE_ZONE = ("fpl_edge/interfaces/", "fpl_edge/cli/")

#: Zones forbidden from naming a warehouse table.
_NO_TABLES = frozenset({"model", "interface"})

#: Zones forbidden from asking the OS what time it is.
_NO_WALL_CLOCK = frozenset({"model", "store"})

WALL_CLOCK = {
    ("datetime", "now"), ("datetime", "utcnow"), ("datetime", "today"),
    ("date", "today"), ("time", "time"), ("Timestamp", "now"),
    ("Timestamp", "utcnow"), ("Timestamp", "today"),
}

_SHUFFLED_SPLITTERS = {
    "train_test_split", "KFold", "StratifiedKFold", "ShuffleSplit",
    "StratifiedShuffleSplit", "cross_val_score", "cross_validate",
    "GridSearchCV", "RandomizedSearchCV",
}

#: numpy legacy global-state RNG entry points. Seeding these is process-global
#: and order-dependent; ``np.random.default_rng(seed)`` is the supported way.
_GLOBAL_RNG = {
    "rand", "randn", "randint", "random", "random_sample", "choice", "shuffle",
    "permutation", "normal", "poisson", "binomial", "uniform", "beta", "gamma",
    "multivariate_normal", "seed",
}

_IMPUTERS = {"SimpleImputer", "KNNImputer", "IterativeImputer"}

_DEADLINE_LIKE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")

_SUPPRESS = re.compile(r"#\s*audit:\s*allow\s+([A-Z_]+)")

#: ``element_id`` inside an actual join predicate, not merely selected.
_SQL_JOIN_ELEMENT = re.compile(r"\b(?:ON|USING)\b[^;]{0,120}\belement_id\b", re.IGNORECASE | re.DOTALL)

#: Marks a string as SQL rather than prose. A LeakageError message that mentions
#: ``fact_fixture`` is the audit working, not the audit's target.
_SQL_VERB = re.compile(r"\b(SELECT|FROM|JOIN|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE)\b", re.IGNORECASE)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids of every Constant node that is a module/class/function docstring.

    Prose is not code. A module that *explains* ``fact_fixture`` in its docstring
    is not reading ``fact_fixture``, and an audit that cannot tell the difference
    gets switched off within a week.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            out.add(id(body[0].value))
    return out


def _snapshot_table_args(tree: ast.AST) -> set[int]:
    """ids of table-name Constants passed to ``<snapshot>.table("...")``.

    ``snapshot.table("fact_fixture")`` is the sanctioned, as_of-filtered read.
    ``con.sql("SELECT * FROM fact_fixture")`` is the leak. Both mention the same
    string, so the audit distinguishes them by position rather than by text.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in WAREHOUSE_TABLES
        ):
            out.add(id(node.args[0]))
    return out


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.message}"


def _zone(rel: str) -> str:
    if any(rel.startswith(p) for p in STORE_ZONE):
        return "store"
    if any(rel.startswith(p) for p in INGEST_ZONE):
        return "ingest"
    if any(rel.startswith(p) for p in INTERFACE_ZONE):
        return "interface"
    return "model"


def _dotted(node: ast.AST) -> str:
    """Best-effort dotted name for an expression, e.g. ``np.random.rand``."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _const_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _str_list(node: ast.expr | None) -> list[str]:
    if node is None:
        return []
    one = _const_str(node)
    if one is not None:
        return [one]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [s for s in (_const_str(e) for e in node.elts) if s is not None]
    return []


class _Visitor(ast.NodeVisitor):
    def __init__(self, rel: str, zone: str, source_lines: list[str],
                 docstrings: set[int], sanctioned: set[int]) -> None:
        self.rel = rel
        self.zone = zone
        self.lines = source_lines
        self.docstrings = docstrings
        self.sanctioned = sanctioned
        self.out: list[Finding] = []

    # -- helpers ----------------------------------------------------------

    def _add(self, node: ast.AST, rule: str, message: str) -> None:
        line = getattr(node, "lineno", 0)
        src = self.lines[line - 1] if 0 < line <= len(self.lines) else ""
        m = _SUPPRESS.search(src)
        if m and m.group(1) == rule:
            return
        self.out.append(Finding(rule, self.rel, line, message))

    # -- string literals --------------------------------------------------

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and id(node) not in self.docstrings:
            text = node.value
            named = [t for t in sorted(WAREHOUSE_TABLES) if re.search(rf"\b{t}\b", text)]
            looks_like_access = bool(_SQL_VERB.search(text)) or text.strip() in WAREHOUSE_TABLES
            if self.zone in _NO_TABLES and id(node) not in self.sanctioned:
                if named and looks_like_access:
                    self._add(
                        node, "DIRECT_TABLE",
                        f"names warehouse table {named[0]!r} outside store/ingest; "
                        "model inputs must come from Snapshot",
                    )
                if "bootstrap-static" in text:
                    self._add(
                        node, "CURRENT_BOOTSTRAP",
                        "reads the live bootstrap; that is the CURRENT squad list and "
                        "using it to filter history is survivorship bias",
                    )
                if "/help/rules" in text:
                    self._add(
                        node, "RULES_PAGE",
                        "the FPL rules page renders deadlines in BROWSER-LOCAL time; "
                        "deadlines must come from the API's events[].deadline_time",
                    )
                if _DEADLINE_LIKE.search(text):
                    self._add(
                        node, "HARDCODED_DEADLINE",
                        f"hardcoded timestamp literal {text!r}; deadlines are data, "
                        "not constants, and they move",
                    )
            if _SQL_JOIN_ELEMENT.search(text):
                self._add(
                    node, "JOIN_ELEMENT_ID",
                    "SQL joins on element_id, which is reassigned between seasons; "
                    "join on the stable player code",
                )
        self.generic_visit(node)

    # -- calls ------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted(node.func)
        tail = name.rsplit(".", 1)[-1]

        # --- direct database access -------------------------------------
        if self.zone in _NO_TABLES:
            if tail in {"connect", "Warehouse"} and ("duckdb" in name or "Warehouse" in name):
                self._add(node, "DIRECT_DB",
                          f"{name}() opens the warehouse directly; take a Snapshot argument")
            if tail in {"read_parquet", "read_csv", "read_json", "read_sql", "read_sql_query"}:
                self._add(node, "SIDE_CHANNEL",
                          f"{name}() reads data outside the point-in-time warehouse; "
                          "as_of filtering cannot protect it")
            # NOTE: no blanket rule on ``.sql(...)``. The idea registry keeps its
            # own tables in the same DuckDB file and querying those is fine; what
            # is not fine is a SQL string naming a WAREHOUSE table, which
            # DIRECT_TABLE catches precisely.

        # --- wall clock --------------------------------------------------
        if self.zone in _NO_WALL_CLOCK:
            parts = name.split(".")
            if len(parts) >= 2 and (parts[-2], parts[-1]) in WALL_CLOCK:
                self._add(node, "WALL_CLOCK",
                          f"{name}() asks the OS for the time; a model's notion of now "
                          "is snapshot.as_of, otherwise backtests see the future")

        # --- naive datetimes ---------------------------------------------
        if (tail == "datetime" and name.endswith("datetime")
                and _kw(node, "tzinfo") is None and len(node.args) < 8):
            self._add(node, "NAIVE_DATETIME",
                      "datetime(...) built without tzinfo; a naive timestamp written "
                      "to a TIMESTAMPTZ column is silently reinterpreted in the "
                      "process-local timezone")
        if tail in {"utcnow", "utcfromtimestamp"}:
            self._add(node, "NAIVE_DATETIME",
                      f"{name}() returns a NAIVE datetime in UTC-looking clothing")
        if tail == "fromtimestamp" and _kw(node, "tz") is None:
            self._add(node, "NAIVE_DATETIME", f"{name}() without tz= returns local-naive")
        _tzarg = _kw(node, "tzinfo")
        if (tail == "replace" and isinstance(_tzarg, ast.Constant)
                and _tzarg.value is None):
            self._add(node, "NAIVE_DATETIME",
                      "replace(tzinfo=None) strips the timezone and invites an "
                      "off-by-one-hour deadline across the BST/GMT boundary")
        if tail == "to_datetime" and self.zone != "store":
            # The store layer checks tz-awareness explicitly BEFORE converting,
            # which is stricter than utc=True and is the behaviour we asked for.
            utc = _kw(node, "utc")
            if not (isinstance(utc, ast.Constant) and utc.value is True):
                self._add(node, "NAIVE_DATETIME",
                          "pd.to_datetime() without utc=True yields mixed/naive tz and "
                          "silently shifts times around the October clock change")

        # --- element_id as a join key ------------------------------------
        for kwname in ("on", "left_on", "right_on", "by", "index", "columns", "subset"):
            if "element_id" in _str_list(_kw(node, kwname)):
                self._add(node, "JOIN_ELEMENT_ID",
                          f"{kwname}= keys on element_id, which is a PER-SEASON row id "
                          "reassigned every summer; use the stable code")
        if (tail in {"groupby", "set_index", "sort_values"}
                and node.args and "element_id" in _str_list(node.args[0])):
            self._add(node, "JOIN_ELEMENT_ID",
                      f"{tail}() keys on element_id rather than the stable code")

        # --- silent NaN handling -----------------------------------------
        if tail == "fillna":
            filler = node.args[0] if node.args else _kw(node, "value")
            if isinstance(filler, ast.Constant) and isinstance(filler.value, (int, float)):
                self._add(node, "FILL_CONST",
                          f"fillna({filler.value!r}) turns 'unknown' into a real "
                          "observation; on a rate statistic that is a fabricated zero")
            if isinstance(filler, ast.Call) and _dotted(filler.func).rsplit(".", 1)[-1] in {
                "mean", "median"
            }:
                self._add(node, "LEAKY_IMPUTE",
                          "mean/median imputation computed over the whole frame leaks "
                          "the evaluation period into training")
        if tail == "dropna" and not node.args and not node.keywords:
            self._add(node, "SILENT_DROPNA",
                      "bare dropna() removes exactly the rows that are hard to predict "
                      "(new signings, returning injuries) and flatters every metric")
        if tail in _IMPUTERS:
            self._add(node, "LEAKY_IMPUTE",
                      f"{tail} must be fitted on the training fold only, never on all rows")

        # --- evaluation --------------------------------------------------
        if tail in _SHUFFLED_SPLITTERS:
            self._add(node, "NOT_WALK_FORWARD",
                      f"{tail} splits at random; FPL data is a time series and the only "
                      "honest evaluation is walk-forward (train <= GW n, test GW n+1)")

        # --- randomness ---------------------------------------------------
        if name.startswith(("np.random.", "numpy.random.")) and tail in _GLOBAL_RNG:
            self._add(node, "GLOBAL_RNG",
                      f"np.random.{tail}() uses process-global RNG state; results are "
                      "not reproducible from a seed argument. Use "
                      "np.random.default_rng(seed)")
        if name in {"random.random", "random.choice", "random.shuffle", "random.sample"}:
            self._add(node, "GLOBAL_RNG", f"{name}() is unseeded global randomness")

        self.generic_visit(node)

    # -- attribute access -------------------------------------------------

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self.zone in _NO_TABLES and node.attr == "warehouse":
            base = _dotted(node.value)
            base_tail = base.rsplit(".", 1)[-1].lower()
            # ``args.warehouse`` is an argparse option, not an escape hatch.
            # ``snapshot.warehouse`` is the escape hatch.
            if "snap" in base_tail or base_tail in {"self", "ctx", "view"}:
                self._add(node, "SNAPSHOT_ESCAPE",
                          "reaches through Snapshot.warehouse to the raw connection, "
                          "which bypasses every as_of filter. This is the leak the "
                          "Snapshot abstraction exists to prevent")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self.generic_visit(node)

    # -- arithmetic -------------------------------------------------------

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Add):
            names = {_subscript_or_attr(node.left), _subscript_or_attr(node.right)}
            if "total_points" in names and "bonus" in names:
                self._add(node, "BONUS_DOUBLE_COUNT",
                          "total_points ALREADY includes bonus; adding a BPS-model bonus "
                          "on top double counts it")
        self.generic_visit(node)


def _subscript_or_attr(node: ast.expr) -> str | None:
    """Extract ``total_points`` from ``df['total_points']`` or ``row.total_points``."""
    if isinstance(node, ast.Subscript):
        return _const_str(node.slice)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def audit_file(path: Path, *, root: Path = REPO_ROOT) -> list[Finding]:
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:  # a half-landed file from another team
        return [Finding("SYNTAX", rel, exc.lineno or 0, f"cannot parse: {exc.msg}")]
    v = _Visitor(
        rel, _zone(rel), text.splitlines(),
        _docstring_nodes(tree), _snapshot_table_args(tree),
    )
    v.visit(tree)
    return v.out


def audit_tree(root: Path = REPO_ROOT, *, packages: tuple[str, ...] = ("fpl_edge",)) -> list[Finding]:
    findings: list[Finding] = []
    for pkg in packages:
        base = root / pkg
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            findings.extend(audit_file(path, root=root))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    ap.add_argument("--root", default=str(REPO_ROOT))
    args = ap.parse_args(argv)

    findings = audit_tree(Path(args.root))
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        for f in findings:
            print(f.render())
        print(f"\n{len(findings)} finding(s).")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
