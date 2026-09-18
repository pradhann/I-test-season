"""Saved analyses: one read-only SQL statement, named, versioned in git.

An analysis is a question worth asking again. It is one read-only statement
over the warehouse with ``$name`` placeholders, stored as JSON under
``analyses/`` in this repo and committed on its own, so "why did the bot say
that in October" is answerable from git history months later.

This module owns the path rules, the name validation and the commit. Before
it, all three lived inside an MCP tool body, which meant the only way to save
an analysis was to be an MCP tool and the only test of the commit was a test
of that tool. A store the tool calls is a smaller thing to reason about than
an implementation the tool is.

THREE RULES, AND WHY EACH IS HERE RATHER THAN IN THE CALLER

**A name is validated before it is a path.** ``valid_name`` admits lower-case
letters, digits, underscore and hyphen, up to 64 characters, and nothing else.
That is what keeps a name out of a parent directory and out of a git pathspec,
and it is checked in ``save`` and in ``load`` rather than trusted to whoever
called them.

**The SQL is checked before it is stored, not before it is run.** A statement
that fails ``assert_single_statement`` or ``assert_read_only`` is rejected at
save time, because the alternative is discovering at the deadline that a saved
analysis was never runnable. The same two guards run again when the caller
executes it, through ``guarded_query``; this one is the early one.

**The commit is a fact the caller reports, not a side effect it hides.**
``save`` returns what happened to git in ``committed`` and ``commit_sha``,
including the case where the file was already identical and no commit was
made. A caller that prints "committed" when nothing was committed is telling
the user their question is recoverable when it is not.

The git author is the repository owner, read from ``fpl_edge.config``. Nothing
here reads a credential or a token: ``git commit`` against a local repository
needs neither.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

UTC = dt.UTC

#: The checkout this module writes into: three parents up from
#: fpl_edge/interfaces/analyses.py.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where saved analyses live. Inside the repo, so git versions them.
ANALYSES_DIRNAME = "analyses"

#: Filesystem-safe and git-safe names: lower snake or kebab, 64 characters.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: The ``$name`` placeholder form. Values are bound as DuckDB parameters at
#: run time; nothing is interpolated into the statement text.
_PARAM_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


class AnalysisError(ValueError):
    """A saved analysis that could not be written, or one that is not there."""


def valid_name(name: str) -> bool:
    """Filesystem-safe and git-safe analysis names: lower snake or kebab."""
    return bool(NAME_RE.match(str(name)))


def param_names(sql: str) -> list[str]:
    """The distinct ``$name`` parameters a statement declares, in order."""
    seen: list[str] = []
    for match in _PARAM_RE.finditer(sql or ""):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def analyses_dir(root: Path | str | None = None) -> Path:
    return Path(root or REPO_ROOT) / ANALYSES_DIRNAME


def analysis_path(name: str, root: Path | str | None = None) -> Path:
    if not valid_name(name):
        raise AnalysisError(
            f"invalid analysis name {name!r}: use lower-case letters, digits, "
            f"underscore or hyphen, starting with a letter or digit, at most "
            f"64 characters."
        )
    return analyses_dir(root) / f"{name}.json"


@dataclass(frozen=True)
class Analysis:
    """One saved analysis, as it sits on disk."""

    name: str
    description: str
    sql: str
    params_schema: dict[str, Any]
    saved_utc: str

    @property
    def params(self) -> list[str]:
        return param_names(self.sql)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "sql": self.sql,
            "params_schema": self.params_schema,
            "saved_utc": self.saved_utc,
        }


@dataclass(frozen=True)
class SaveResult:
    """What ``save`` did, including what git did about it."""

    analysis: Analysis
    path: Path
    relative_path: str
    committed: bool
    commit_sha: str | None
    git_note: str


def _git(args: list[str], root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=False,
    )


#: The commit author. ``UserConfig`` carries the FPL identity, not the git
#: one, so this stays a constant here rather than pretending to be derived.
#: ``GIT_AUTHOR`` in the environment overrides it, which is what a second
#: operator or a test needs.
DEFAULT_AUTHOR = "Nripesh <nripeshpradhan@gmail.com>"


def _author() -> str:
    """The commit author, as git wants it. Never a credential."""
    import os

    return os.environ.get("GIT_AUTHOR", "").strip() or DEFAULT_AUTHOR


def save(
    name: str,
    description: str,
    sql: str,
    params_schema: dict[str, Any] | None = None,
    *,
    root: Path | str | None = None,
    commit: bool = True,
    conversation: str | None = None,
) -> SaveResult:
    """Validate, write and commit one analysis. Overwriting keeps the history.

    ``commit=False`` writes the file without touching git, which is what a
    test wants and what a caller running outside a checkout needs.
    """
    from fpl_edge.platform.query import (
        QueryError,
        assert_read_only,
        assert_single_statement,
    )

    path = analysis_path(name, root)
    try:
        assert_single_statement(sql)
        assert_read_only(sql)
    except QueryError as exc:
        raise AnalysisError(str(exc)) from exc

    declared = param_names(sql)
    schema = dict(params_schema or {})
    undeclared = [key for key in schema if key not in declared]
    if undeclared:
        raise AnalysisError(
            f"params_schema declares {undeclared} but the statement contains "
            f"no ${undeclared[0]}. The statement's parameters are "
            f"{declared or 'none'}; make the schema match."
        )

    analysis = Analysis(
        name=name,
        description=description,
        sql=sql,
        params_schema=schema,
        saved_utc=dt.datetime.now(UTC).isoformat(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analysis.to_dict(), indent=2, sort_keys=True) + "\n")

    base = Path(root or REPO_ROOT)
    relative = str(path.relative_to(base))
    if not commit:
        return SaveResult(analysis, path, relative, False, None,
                          "not committed: this call asked for a write only.")

    added = _git(["add", relative], base)
    if added.returncode != 0:
        return SaveResult(
            analysis, path, relative, False, None,
            f"written, and `git add` failed: {added.stderr.strip()}",
        )
    message = f"analysis: {name}"
    if conversation:
        message += f"\n\nconversation: {conversation}"
    done = _git(
        ["commit", "--author", _author(), "-m", message, "--", relative], base,
    )
    if done.returncode != 0:
        out = (done.stdout + done.stderr).strip()
        if "nothing to commit" in out or "no changes added" in out:
            return SaveResult(
                analysis, path, relative, False, None,
                "identical to the committed version, so no new commit was made.",
            )
        return SaveResult(analysis, path, relative, False, None,
                          f"written, and `git commit` failed: {out}")
    sha = _git(["rev-parse", "--short", "HEAD"], base).stdout.strip() or None
    return SaveResult(analysis, path, relative, True, sha,
                      f"committed {sha}.")


def load(name: str, *, root: Path | str | None = None) -> Analysis:
    """One saved analysis by name, or an error naming what is saved."""
    try:
        path = analysis_path(name, root)
    except AnalysisError:
        path = None
    if path is None or not path.exists():
        have = [item.name for item in list_all(root=root)]
        raise AnalysisError(
            f"no analysis named {name!r}. Saved analyses: "
            f"{', '.join(have) if have else 'none yet'}."
        )
    stored = json.loads(path.read_text())
    return Analysis(
        name=str(stored.get("name") or name),
        description=str(stored.get("description") or ""),
        sql=str(stored.get("sql") or ""),
        params_schema=dict(stored.get("params_schema") or {}),
        saved_utc=str(stored.get("saved_utc") or ""),
    )


def list_all(*, root: Path | str | None = None) -> list[Analysis]:
    """Every saved analysis, by name. A corrupt file is skipped, not fatal."""
    directory = analyses_dir(root)
    if not directory.exists():
        return []
    out: list[Analysis] = []
    for path in sorted(directory.glob("*.json")):
        try:
            stored = json.loads(path.read_text())
        except Exception:  # noqa: BLE001 - one bad file must not hide the rest
            out.append(Analysis(path.stem, "(unreadable JSON on disk)", "", {}, ""))
            continue
        out.append(Analysis(
            name=str(stored.get("name") or path.stem),
            description=str(stored.get("description") or ""),
            sql=str(stored.get("sql") or ""),
            params_schema=dict(stored.get("params_schema") or {}),
            saved_utc=str(stored.get("saved_utc") or ""),
        ))
    return out


def bind(sql: str, values: dict[str, Any]) -> tuple[str, list[Any], list[str]]:
    """Replace every ``$name`` with a placeholder and collect the binds.

    Returns ``(statement, binds_in_order, missing_names)``. Values are bound
    as DuckDB parameters, never interpolated, so a note or a season string in
    a parameter can never become SQL. A ``$name`` with no value is reported
    rather than guessed at.
    """
    binds: list[Any] = []
    missing: list[str] = []

    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            if key not in missing:
                missing.append(key)
            return match.group(0)
        binds.append(values[key])
        return "?"

    return _PARAM_RE.sub(replace, sql), binds, missing


def defaults_for(analysis: Analysis) -> dict[str, Any]:
    """The default values the saved schema declares, if any."""
    return {
        key: spec.get("default")
        for key, spec in (analysis.params_schema or {}).items()
        if isinstance(spec, dict) and "default" in spec
    }
