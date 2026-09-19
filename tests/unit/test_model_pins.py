"""Every model call names its model, and one module holds every model id.

Three places in this repo spend model tokens: claim extraction over stored
transcripts, the briefing salience pass, and the chat. Until the pin landed,
none of them named a model id.

``ingest/content/analyze.py`` shelled out as ``claude -p --output-format
json`` with no ``--model`` flag at all, so the run used whatever that machine's
CLI defaulted to, which was the owner's most expensive model for weeks. Every
row it wrote was stamped ``claude-opus-5`` anyway, from a constant, because a
constant is what the code had. ``briefing_intel.py`` and ``chat_agent.py``
passed the alias ``"opus"``, which names whichever Opus the CLI currently calls
newest and therefore names a different model after a CLI upgrade, silently.

So this file pins two properties, and the second is what keeps the first true:

1. each spend site passes the pinned id to the backend, explicitly;
2. ``fpl_edge/config.py`` is the only module in ``fpl_edge`` where a model id
   appears at all, so a fourth spend site cannot introduce a fourth opinion
   about which model this engine runs on.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import types

import pytest

from fpl_edge.config import ANALYSIS_MODEL, BRIEFING_MODEL, CHAT_MODEL
from fpl_edge.ingest.content import analyze

# --------------------------------------------------------------- the pins

def test_the_three_pins_are_bare_model_ids() -> None:
    """Not aliases. An alias is a name for a moving target, and these ids are
    written into a primary key and into the ledger, where they have to stay
    joinable to the rows written beside them."""
    for pin in (ANALYSIS_MODEL, BRIEFING_MODEL, CHAT_MODEL):
        assert analyze.validate_model_id(pin) == pin


def test_a_pin_that_is_not_a_model_id_fails_at_import(monkeypatch) -> None:
    """The failure is loud and early, not a night of rows stamped with an
    alias nothing can join to."""
    import importlib

    from fpl_edge import config

    monkeypatch.setenv("FPL_EDGE_ANALYSIS_MODEL", "opus")
    with pytest.raises(RuntimeError, match="not a bare Anthropic model id"):
        importlib.reload(config)
    monkeypatch.delenv("FPL_EDGE_ANALYSIS_MODEL")
    importlib.reload(config)


# ------------------------------------------------- spend site 1: analyze.py

class _Envelope:
    """One captured ``claude -p`` invocation, answering as the CLI does."""

    def __init__(self) -> None:
        self.argv: list[str] = []

    def __call__(self, argv, **kwargs):
        self.argv = list(argv)
        body = (
            '{"type":"result","is_error":false,'
            '"result":"{\\"summary\\":[\\"s\\"],\\"transfers_in\\":[],'
            '\\"transfers_out\\":[],\\"captaincy\\":[],'
            '\\"chip_advice\\":[],\\"differentials\\":[],'
            '\\"insights\\":[]}",'
            '"usage":{"input_tokens":11,"cache_creation_input_tokens":2,'
            '"cache_read_input_tokens":7,"output_tokens":5},'
            '"modelUsage":{"claude-sonnet-5":{"outputTokens":5,'
            '"canonicalModel":"claude-sonnet-5"}}}'
        )
        return types.SimpleNamespace(returncode=0, stdout=body, stderr="")


def test_the_cli_call_passes_the_model_flag(monkeypatch) -> None:
    """The regression this file exists for. Without ``--model`` the CLI picks,
    and nothing in the row it produced says which model it picked."""
    import subprocess

    envelope = _Envelope()
    monkeypatch.setattr(subprocess, "run", envelope)
    measured = analyze._analyze_via_cli(
        "/bin/claude", title="t", creator="c", body="b")

    assert "--model" in envelope.argv
    assert envelope.argv[envelope.argv.index("--model") + 1] == ANALYSIS_MODEL
    assert measured.usage.model_reported == "claude-sonnet-5"


def test_the_sdk_fallback_passes_the_model_too() -> None:
    """Both doors, one pin. The SDK path is the fallback nobody watches, which
    is exactly where a second default would survive longest."""
    captured: dict = {}

    class _Messages:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                parsed_output=analyze.TranscriptAnalysis(
                    summary=["s"], transfers_in=[], transfers_out=[],
                    captaincy=[], chip_advice=[], differentials=[]),
                model=ANALYSIS_MODEL,
                usage=types.SimpleNamespace(input_tokens=3, output_tokens=4),
            )

    client = types.SimpleNamespace(messages=_Messages())
    measured = analyze.analyze_transcript_measured(
        title="t", creator="c", text="x", client=client)

    assert captured["model"] == ANALYSIS_MODEL
    assert measured.usage.model_reported == ANALYSIS_MODEL


# ------------------------------------------ spend site 2: briefing_intel.py

def test_the_briefing_pass_pins_its_model(monkeypatch) -> None:
    """``_run_model`` builds the SDK options; this reads the model off the
    options it actually built, rather than off the module constant it could
    have read and then ignored."""
    import claude_agent_sdk

    from fpl_edge.platform import briefing_intel

    captured: dict = {}

    class _Options:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def _query(*, prompt, options):
        result = claude_agent_sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1,
            is_error=False, num_turns=1, session_id="s",
            usage={"input_tokens": 9, "output_tokens": 2},
            model_usage={BRIEFING_MODEL: {"outputTokens": 2}},
        )
        for msg in (result,):
            yield msg

    monkeypatch.setattr(claude_agent_sdk, "ClaudeAgentOptions", _Options)
    monkeypatch.setattr(claude_agent_sdk, "query", _query)

    answer = briefing_intel._run_model("say something")

    assert captured["model"] == BRIEFING_MODEL
    assert answer.usage.model_reported == BRIEFING_MODEL
    assert answer.usage.tokens_in == 9 and answer.usage.tokens_out == 2


# ---------------------------------------------- spend site 3: chat_agent.py

def test_the_chat_pins_its_model(tmp_path) -> None:
    """The chat is the one site that is deliberately NOT on the cheaper pin,
    so the assertion names its own constant rather than sharing one."""
    from fpl_edge.platform import chat_agent

    agent = chat_agent.ChatAgent(root=tmp_path, cwd=tmp_path)
    conv = agent.create_conversation()["conv_id"]
    options = agent.build_options(agent._conv(conv), None)

    assert options.model == CHAT_MODEL


# ------------------------------------- spend site 4: jobs/deadline_dag.py

def test_the_alert_copy_polish_pins_its_model(monkeypatch, tmp_path) -> None:
    """The site nobody had counted. ``polish_copy`` runs one ``claude -p`` per
    delivered alert and carried no ``--model`` either, so it ran at the CLI's
    default on every delivery. It leaves no ledger row of its own, which is
    why an argv assertion is the only evidence available for it."""
    import subprocess

    from fpl_edge.jobs import deadline_dag

    captured: list[list[str]] = []

    def _run(argv, **kwargs):
        captured.append(list(argv))
        return types.SimpleNamespace(
            returncode=0, stdout='{"title": "T", "body": "B"}', stderr="")

    binary = tmp_path / "claude"
    binary.write_text("")
    monkeypatch.setattr(deadline_dag, "CLAUDE_BIN", binary)
    monkeypatch.setattr(subprocess, "run", _run)

    assert deadline_dag.polish_copy("title", "body") == ("T", "B")
    argv = captured[0]
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == BRIEFING_MODEL


# ------------------------------------------------------------- the grep test

#: A model id as it appears in code: the ``claude-`` prefix followed by a
#: family name. ``claude-agent-sdk`` is a package and does not match.
_MODEL_LITERAL = re.compile(
    r"\bclaude-(?:opus|sonnet|haiku|fable|mythos)\b"
    r"|^(?:opus|sonnet|haiku|fable|mythos)$"
)

#: The one module allowed to contain a model id.
_PIN_MODULE = "fpl_edge/config.py"


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Every node that is a module, class or function docstring.

    Excluded from the scan on purpose. Prose that explains which model a
    column holds, or names the id a validator rejects, is documentation and
    is the opposite of the problem: the problem is a model id that code acts
    on, and a docstring is not acted on.
    """
    out: set[int] = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            out.add(id(body[0].value))
    return out


def model_literals_in_tree(root: pathlib.Path) -> list[str]:
    """Every ``file:line: value`` where a model id is a live string literal."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in docs
                    and _MODEL_LITERAL.search(node.value)):
                found.append(f"{path}:{node.lineno}: {node.value[:60]!r}")
    return found


def test_only_the_config_module_names_a_model(capsys) -> None:
    """One place to change the model, one place to read it from.

    The output is printed rather than only asserted: a run of this test is
    also the inventory the gate asks for, so the list is visible whether it
    passes or fails.
    """
    found = model_literals_in_tree(pathlib.Path("fpl_edge"))
    with capsys.disabled():
        print("\nmodel string literals under fpl_edge/:")
        for line in found:
            print(f"  {line}")

    strays = [line for line in found if not line.startswith(_PIN_MODULE)]
    assert not strays, (
        "a model id outside the pin module. Every spend site reads its id "
        "from fpl_edge/config.py so that changing the model is one edit and "
        "so that no site can hold a different opinion: " + "; ".join(strays)
    )


def test_the_scan_would_catch_a_stray(tmp_path) -> None:
    """The scan is only worth its place if it fails on the thing it is for.

    Without this, a regex that had stopped matching anything would pass
    forever and read as proof that the tree is clean.
    """
    (tmp_path / "stray.py").write_text(
        '"""A docstring naming claude-opus-5 is fine."""\n'
        'MODEL = "claude-opus-5"\n'
    )
    found = model_literals_in_tree(tmp_path)
    assert len(found) == 1 and "stray.py:2" in found[0]


def test_python_can_import_every_spend_site() -> None:
    """A pin that breaks an import is not a pin, it is an outage."""
    for name in ("fpl_edge.ingest.content.analyze",
                 "fpl_edge.platform.briefing_intel",
                 "fpl_edge.platform.chat_agent"):
        assert name in sys.modules or __import__(name)
