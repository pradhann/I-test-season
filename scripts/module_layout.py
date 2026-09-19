"""The package tree as it stands, printed as the layout table.

One inventory, two readers. ``docs/platform/LAYOUT.md`` prints this table
between its generated markers, and ``tests/unit/test_layout_doc.py`` fails
when the file on disk and the tree disagree, so the doc can never describe a
layout the code has moved on from.

What a module is for is read out of the module itself: the first sentence of
its docstring, never a restatement kept here. A module with no docstring is
printed as such, because an undocumented module on the layout page is a gap
worth seeing rather than a gap worth hiding.

The layer rules that govern which package may import which are hand written
in LAYOUT.md above the generated block. They are a decision, not a fact about
the tree, so they are not generated.

Usage::

    uv run python scripts/module_layout.py            # print the table
    uv run python scripts/module_layout.py --write    # rewrite LAYOUT.md
    uv run python scripts/module_layout.py --check    # exit 1 when stale
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: The package this table covers. scripts/ holds entry points rather than
#: library code, and tests/ is not part of the shipped tree.
PACKAGE = "fpl_edge"

#: Where the generated block starts and ends inside LAYOUT.md. Everything
#: between the markers is this script's output; everything outside is written
#: by hand.
BEGIN = "<!-- BEGIN GENERATED: scripts/module_layout.py -->"
END = "<!-- END GENERATED -->"


@dataclass(frozen=True)
class Module:
    """One importable module: where it sits, what it is for, how big it is."""

    package: str
    """Dotted package path, ``fpl_edge`` for the top level."""

    module: str
    """File name, including the suffix."""

    responsibility: str
    """First sentence of the module docstring, or a stated absence."""

    lines: int
    """Physical line count, the number the review's size checks use."""


def first_sentence(doc: str) -> str:
    """The docstring's opening sentence, on one line, without the full stop.

    A summary line that runs onto a second line is joined, because the table
    has one cell per module and a wrapped line is a wrapped sentence, not two.
    """
    head = doc.strip().split("\n\n")[0]
    text = " ".join(part.strip() for part in head.split("\n") if part.strip())
    for stop in (". ", ".\t"):
        if stop in text:
            text = text.split(stop)[0]
            break
    return text.rstrip(".").strip()


def module_doc(path: Path) -> str | None:
    """The module docstring, read without importing the module."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return None
    return ast.get_docstring(tree)


def walk(root: Path | None = None) -> tuple[Module, ...]:
    """Every ``.py`` file under the package, in path order."""
    base = (root if root is not None else REPO / PACKAGE)
    out: list[Module] = []
    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(base.parent)
        package = ".".join(rel.parts[:-1])
        doc = module_doc(path)
        summary = first_sentence(doc) if doc else "(no module docstring)"
        lines = len(path.read_text().splitlines())
        out.append(Module(package, path.name, summary, lines))
    return tuple(out)


def _cell(text: str) -> str:
    """One markdown table cell, normalised.

    A pipe would end the cell and a newline would end the row. An em dash is
    replaced for the same reason the pipe is: this page is authored prose
    under the house rule in ``fpl_edge/platform/prose_style.py``, and a
    quoted docstring must not carry one onto it. The sentence is otherwise
    the module's own words.
    """
    return (text.replace("|", "/").replace("\n", " ")
                .replace(" \u2014 ", ", ").replace("\u2014", ",")
                .replace("\u2013", "-"))


def layout_table(modules: tuple[Module, ...] | None = None) -> str:
    """The generated half of LAYOUT.md: one row per module, plus the totals."""
    mods = modules if modules is not None else walk()
    lines = [
        f"{len(mods)} modules, {sum(m.lines for m in mods):,} lines.",
        "",
        "| Package | Module | Responsibility | Lines |",
        "|---|---|---|---:|",
    ]
    for m in mods:
        lines.append(
            f"| `{m.package}` | `{m.module}` | {_cell(m.responsibility)} "
            f"| {m.lines} |"
        )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def doc_path() -> Path:
    return REPO / "docs" / "platform" / "LAYOUT.md"


def rendered() -> str:
    """The full text LAYOUT.md should hold, given the tree as it is now."""
    path = doc_path()
    text = path.read_text()
    before, marker, rest = text.partition(BEGIN)
    _body, end_marker, after = rest.partition(END)
    if not marker or not end_marker:
        raise SystemExit(f"{path} has no generated block; add the markers")
    return f"{before}{BEGIN}\n\n{layout_table()}\n{END}{after}"


def rewrite_doc() -> bool:
    """Put the generated table back between the markers. True when changed."""
    path = doc_path()
    fresh = rendered()
    if fresh == path.read_text():
        return False
    path.write_text(fresh)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="rewrite the generated block in LAYOUT.md")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when LAYOUT.md is out of sync with the tree")
    args = ap.parse_args(argv)
    if args.check:
        stale = rendered() != doc_path().read_text()
        print("stale" if stale else "in sync")
        return 1 if stale else 0
    if args.write:
        print("rewrote" if rewrite_doc() else "already in sync")
        return 0
    print(layout_table())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
