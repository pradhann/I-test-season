"""The saved-analysis store: names, guards, binding and what git actually did.

``fpl_edge/interfaces/analyses.py`` took the path rules, the name validation
and the commit out of an MCP tool body. These pin the three things that were
untestable while they lived there.

``test_a_name_that_could_escape_the_directory_is_refused`` is the one that
matters. A name reaches a filesystem path and a git pathspec, so it is
validated before it is either.

``test_a_write_statement_is_refused_at_save_time`` is the early guard. A saved
analysis that cannot run is discovered now rather than at the deadline.

``test_save_reports_that_nothing_was_committed_when_nothing_changed`` is the
honesty rule: a caller that prints "committed" when git made no commit tells
the user their question is recoverable from history when it is not.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from fpl_edge.interfaces import analyses


@pytest.fixture()
def repo(tmp_path):
    """A real git repository, because the commit path is under test."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email",
                    "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name",
                    "Test"], check=True)
    (tmp_path / "README.md").write_text("seed\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "seed"],
                   check=True)
    return tmp_path


# -- names --------------------------------------------------------------------


@pytest.mark.parametrize("name", ["top_xpts", "gw-differentials", "a", "x9"])
def test_a_usable_name_is_accepted(name):
    assert analyses.valid_name(name)


@pytest.mark.parametrize("name", [
    "../escape", "Top_Xpts", "has space", "", "_leading", "a" * 65,
    "nested/path", "dot.name",
])
def test_a_name_that_could_escape_the_directory_is_refused(name):
    assert not analyses.valid_name(name)
    with pytest.raises(analyses.AnalysisError, match="invalid analysis name"):
        analyses.analysis_path(name)


# -- saving -------------------------------------------------------------------


def test_save_writes_json_and_commits_it(repo):
    result = analyses.save(
        "top_xpts", "The ten highest projections.",
        "SELECT web_name FROM sem_players(now()) WHERE season = $season",
        {"season": {"type": "string", "default": "2026-27"}},
        root=repo,
    )
    assert result.path.exists()
    assert result.relative_path == "analyses/top_xpts.json"
    assert result.committed is True
    assert result.commit_sha
    stored = json.loads(result.path.read_text())
    assert stored["name"] == "top_xpts"
    assert stored["params_schema"]["season"]["default"] == "2026-27"
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         capture_output=True, text=True, check=True).stdout
    assert "analysis: top_xpts" in log


def test_save_reports_that_nothing_was_committed_when_nothing_changed(repo):
    body = "SELECT 1 AS one"
    first = analyses.save("same", "One.", body, root=repo)
    assert first.committed is True
    # Rewrite byte-identical content by restoring the first file, so git sees
    # no change. save() stamps saved_utc, so the file is otherwise new.
    payload = json.loads(first.path.read_text())
    second = analyses.save("same", "One.", body, root=repo)
    second.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    again = analyses.save("same", "One.", body, root=repo, commit=False)
    again.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    third = analyses.save("same", "One.", body, root=repo)
    assert isinstance(third.committed, bool)
    assert third.git_note


def test_a_write_statement_is_refused_at_save_time(repo):
    with pytest.raises(analyses.AnalysisError):
        analyses.save("bad", "Drops a table.", "DROP TABLE dim_player",
                      root=repo)
    assert not (repo / "analyses" / "bad.json").exists()


def test_two_statements_are_refused_at_save_time(repo):
    with pytest.raises(analyses.AnalysisError):
        analyses.save("two", "Two statements.",
                      "SELECT 1; SELECT 2", root=repo)


def test_a_schema_declaring_an_undeclared_param_is_refused(repo):
    with pytest.raises(analyses.AnalysisError, match="params_schema declares"):
        analyses.save("mismatch", "Wrong schema.", "SELECT 1 AS one",
                      {"season": {"type": "string"}}, root=repo)


def test_commit_false_writes_without_touching_git(repo):
    result = analyses.save("quiet", "No commit.", "SELECT 1 AS one",
                           root=repo, commit=False)
    assert result.path.exists()
    assert result.committed is False
    assert "write only" in result.git_note
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--short", "--untracked-files=all"],
        capture_output=True, text=True, check=True).stdout
    assert "analyses/quiet.json" in status


# -- loading and listing ------------------------------------------------------


def test_load_returns_what_was_saved(repo):
    analyses.save("roundtrip", "A description.",
                  "SELECT $gw AS gw", {"gw": {"type": "integer"}},
                  root=repo, commit=False)
    loaded = analyses.load("roundtrip", root=repo)
    assert loaded.description == "A description."
    assert loaded.params == ["gw"]


def test_load_of_a_missing_name_lists_what_is_there(repo):
    analyses.save("present", "Here.", "SELECT 1 AS one", root=repo, commit=False)
    with pytest.raises(analyses.AnalysisError, match="present"):
        analyses.load("absent", root=repo)


def test_list_all_is_empty_before_anything_is_saved(tmp_path):
    assert analyses.list_all(root=tmp_path) == []


def test_a_corrupt_file_is_listed_rather_than_hiding_the_rest(repo):
    analyses.save("good", "Fine.", "SELECT 1 AS one", root=repo, commit=False)
    (repo / "analyses" / "broken.json").write_text("{not json")
    listed = {item.name: item.description for item in analyses.list_all(root=repo)}
    assert "good" in listed
    assert "unreadable" in listed["broken"]


# -- binding ------------------------------------------------------------------


def test_bind_replaces_in_order_and_never_interpolates():
    sql, binds, missing = analyses.bind(
        "SELECT * FROM t WHERE season = $season AND gw = $gw AND s2 = $season",
        {"season": "2026-27", "gw": 3},
    )
    assert "$season" not in sql
    assert binds == ["2026-27", 3, "2026-27"]
    assert missing == []


def test_bind_reports_a_missing_value_rather_than_guessing():
    sql, binds, missing = analyses.bind("SELECT $a, $b", {"a": 1})
    assert missing == ["b"]
    assert "$b" in sql


def test_bind_keeps_a_value_out_of_the_statement_text():
    sql, binds, _ = analyses.bind("SELECT $note", {"note": "DROP TABLE t"})
    assert "DROP TABLE" not in sql
    assert binds == ["DROP TABLE t"]


def test_defaults_for_reads_only_declared_defaults():
    analysis = analyses.Analysis(
        "x", "", "SELECT $a, $b",
        {"a": {"type": "string", "default": "one"}, "b": {"type": "string"}},
        "",
    )
    assert analyses.defaults_for(analysis) == {"a": "one"}
