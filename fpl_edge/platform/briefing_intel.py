"""briefing_intel — the model-authored salience pass OVER the panels.

The owner's ask: "intelligence that finds the most salient parts from all the
different data sources and lays it out clearly" — xPts too high on a player I
don't own, creators strongly bullish, high EO divergence. That is a judgment
call over many panels at once, which is exactly what ``dashboard_brief`` is
FORBIDDEN from making (its anti-drift contract: select and threshold, never
synthesise). So the synthesis lives here, as a **separate artefact, clearly
labelled model-authored**, and it never merges into dashboard_brief's
deterministic payload.

The shape of the pass:

1. **Input assembly** — the registered panel scripts are called in-process the
   way ``brief.py`` calls its sources (one read copy, a failing panel degrades
   to an honest empty, never crashes the pass), then compacted: top
   :data:`MAX_ROWS` rows per table-like list, nulls dropped, the whole context
   capped at :data:`MAX_CHARS` characters. ``input_as_of`` records each
   panel's own as-of.
2. **Meta-prompt** — ``docs/platform/briefing_meta_prompt.md``, a real
   versioned owner-editable file. Its sha256[:12] rides into the artefact as
   ``meta_prompt_hash`` so a briefing is traceable to the exact instructions
   that produced it.
3. **One model call** — ``claude-agent-sdk``, the same auth posture as
   ``chat_agent.py``: the CLI's own login is the auth, this server holds no
   API key, and the ANTHROPIC_* environment is scrubbed before the SDK spawns
   anything. ``tools=[]``, no MCP — pure synthesis over the provided JSON.
4. **Validation before write** — every kept item must quote numbers, name
   source panels that were actually in the input, and reference only player
   codes present in the input. Rejects are counted (``rejected_n``), never
   silently dropped. At most :data:`MAX_ITEMS` items, severity-sorted
   (1 = act now).
5. **Failure honesty** — SDK unavailable, parse failure, zero valid items:
   nothing new is written and the error raises, so the pipeline ledger
   records status ``error`` with the reason. The ledger observes, never
   swallows.

Artefact: ``briefing_intel.json`` next to the warehouse file, written
atomically::

    {generated_at, model, meta_prompt_hash, input_as_of, items,
     rejected_n, duration_s}
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from fpl_edge.platform.prose_style import (
    STYLE_RULES,
    normalize_prose,
    slop_findings,
)

UTC = dt.UTC

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The versioned, owner-editable instructions. Loaded at run time; its
#: sha256[:12] lands in the artefact as ``meta_prompt_hash``.
META_PROMPT_PATH = _REPO_ROOT / "docs" / "platform" / "briefing_meta_prompt.md"

#: Artefact filename; it lives NEXT TO the warehouse file (the same rule the
#: solve plan follows), so tests with a tmp warehouse get a tmp artefact.
ARTEFACT_NAME = "briefing_intel.json"

#: The registered panel scripts the context is assembled from. player_chatter
#: is deliberately absent: it requires a player ``code`` parameter, so it has
#: no whole-board shape to assemble (a per-player drill, not a source).
INPUT_PANELS: tuple[str, ...] = (
    "squad_overview",
    "projection_table",
    "ownership_eo",
    "fixture_board",
    "creator_board",
    "price_radar",
    "dashboard_brief",
)

#: Top rows kept per table-like list in a panel result.
MAX_ROWS = 30
#: Total serialized-context budget, characters.
MAX_CHARS = 60_000
#: Kept-item cap. More than this is a feed, not a briefing.
MAX_ITEMS = 8
HEADLINE_MAX = 120
WHY_MAX = 280
#: The owner's decision is Opus always; the alias tracks the CLI's newest.
MODEL = "opus"
#: Wall-clock budget for the one-shot synthesis call. Generous: a large
#: context read plus an 8-item answer, not an agentic loop.
MODEL_TIMEOUT_S = 240.0
#: The route flags ``inputs_moved`` past this many hours (spec'd rule).
INPUTS_MOVED_H = 6.0


class BriefingIntelError(RuntimeError):
    """The pass could not produce an honest artefact. Nothing was written."""


def artefact_path(db_path: Path | str) -> Path:
    return Path(db_path).parent / ARTEFACT_NAME


# --------------------------------------------------------------------------
# 1. input assembly (pure over the panel results; the tests pin it)
# --------------------------------------------------------------------------


def collect_panels(wh, *, season: str,
                   panels: tuple[str, ...] = INPUT_PANELS) -> dict[str, dict[str, Any]]:
    """Call each registered panel script in-process, the way brief.py does.

    One shared read handle, declared param defaults filled from each script's
    own schema, and a failing panel degrades to the honest-empty shape rather
    than killing the pass. Nothing here re-implements a metric.
    """
    # Registration IS the import: the web server has these loaded, but the
    # scheduler's process does not, and an empty registry here turned every
    # panel into "no panel script named ..." on the 07:40 firing.
    import fpl_edge.platform.scripts  # noqa: F401 - imported for side effect
    from fpl_edge.platform import registry as panel_registry

    out: dict[str, dict[str, Any]] = {}
    for name in panels:
        try:
            script_obj = panel_registry.script(name)
            props = script_obj.params_schema.get("properties") or {}
            params = panel_registry.validate_params(
                script_obj, {"season": season} if "season" in props else {})
            res = script_obj.fn(wh, **params)
            if not isinstance(res, dict):
                res = {"empty": True,
                       "reason": f"{name} returned {type(res).__name__}"}
        except Exception as exc:  # noqa: BLE001 - a gap is data; the pass reports it
            res = {"empty": True,
                   "reason": f"{name} raised {type(exc).__name__}: {exc}"}
        out[name] = res
    return out


def _iso(v: Any) -> str | None:
    return None if v is None else str(v).replace(" ", "T")


def _prune(value: Any, max_rows: int) -> Any:
    """Drop nulls, truncate lists to ``max_rows``. Recursive, allocation-only."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            pv = _prune(v, max_rows)
            if pv is not None:
                out[k] = pv
        return out
    if isinstance(value, list):
        return [_prune(v, max_rows) for v in value[:max_rows]]
    if isinstance(value, float) and value != value:   # NaN is a null in disguise
        return None
    return value


def _size(context: dict[str, Any]) -> int:
    return len(json.dumps(context, ensure_ascii=False, separators=(",", ":"),
                          default=str))


def build_context(
    results: dict[str, dict[str, Any]],
    *,
    max_rows: int = MAX_ROWS,
    max_chars: int = MAX_CHARS,
) -> tuple[dict[str, Any], dict[str, str | None], list[str]]:
    """Compact panel results into the model's context.

    Returns ``(context, input_as_of, dropped_panels)``. Truncation is
    deterministic and loud: rows shrink first (halving from ``max_rows``),
    then whole panels are dropped largest-first, each drop recorded.
    """
    input_as_of: dict[str, str | None] = {}
    for name, res in results.items():
        if not res.get("empty"):
            input_as_of[name] = _iso(res.get("as_of"))

    rows = max_rows
    context = {name: _prune(res, rows) for name, res in results.items()}
    while _size(context) > max_chars and rows > 3:
        rows = max(3, rows // 2)
        context = {name: _prune(res, rows) for name, res in results.items()}

    dropped: list[str] = []
    while _size(context) > max_chars and len(context) > 1:
        biggest = max(context, key=lambda n: _size({n: context[n]}))
        dropped.append(biggest)
        del context[biggest]
        input_as_of.pop(biggest, None)
    return context, input_as_of, dropped


_CODE_KEY = re.compile(r"^(code|codes|.*_codes)$")

#: A numeric token in prose: digits with optional thousands commas and a
#: decimal part. Sign and percent handled at the call site.
_NUM_CAND = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ORDINAL = ("st", "nd", "rd", "th")


def prose_numbers(text: str) -> list[tuple[float, int]]:
    """Every numeric token in prose, with its printed decimal precision.

    Tolerant of formatting: thousands commas, percent signs, signs, ordinal
    suffixes ("33rd" is the quantity 33). Digits glued to letters (GW3,
    top10k, p90, 36h) are labels/units, not quantities this validator can
    adjudicate against payload values — skipped, deliberately.
    """
    vals: list[tuple[float, int]] = []
    text = text or ""
    for m in _NUM_CAND.finditer(text):
        s, e = m.span()
        if s and (text[s - 1].isalnum() or text[s - 1] in "._"):
            continue          # part of a label (GW3, x2, p90, v1.2)
        tail = text[e:]
        if tail[:1].isalpha() and not (
                tail[:2].lower() in _ORDINAL and not tail[2:3].isalpha()):
            continue          # unit glued on (10k, 36h, 5d)
        tok = m.group(0).replace(",", "")
        decimals = len(tok.split(".")[1]) if "." in tok else 0
        try:
            vals.append((float(tok), decimals))
        except ValueError:    # pragma: no cover - regex should prevent this
            continue
    return vals


def known_values(context: dict[str, Any]) -> dict[str, list[float]]:
    """Every numeric value each panel actually served, panel by panel.

    The pool the numeric-token validator checks prose against: a number the
    model prints must exist in a panel it cites — R2's finding was headline
    prose ("ranks 33rd", payload 30) slipping past a validator that only
    checked the number chips.
    """
    out: dict[str, list[float]] = {}

    def walk(acc: list[float], value: Any) -> None:
        if isinstance(value, dict):
            for v in value.values():
                walk(acc, v)
        elif isinstance(value, list):
            for v in value:
                walk(acc, v)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            acc.append(float(value))

    for name, res in context.items():
        acc: list[float] = []
        walk(acc, res)
        out[name] = acc
    return out


def _decimals_of(v: float) -> int:
    s = f"{v:.6f}".rstrip("0")
    frac = s.split(".")[1] if "." in s else ""
    return len(frac)


def _num_in(value: float, decimals: int, pool: list[float]) -> bool:
    """Does ``value`` (printed at ``decimals`` places) appear in ``pool``?

    Small rounding tolerance (half a unit in the last printed place), sign
    ignored (prose says "fall of 2891" for -2891), and the ×100/÷100 pair
    accepted because EO/probability fields are served both as fractions and
    as percents.
    """
    tol = 0.5 * 10.0 ** (-decimals) + 1e-9
    target = abs(value)
    for p in pool:
        ap = abs(p)
        if (abs(ap - target) <= tol
                or abs(ap * 100.0 - target) <= tol
                or abs(ap / 100.0 - target) <= tol):
            return True
    return False


def known_codes(context: dict[str, Any]) -> set[int]:
    """Every player code the model was actually shown.

    Collected from keys named ``code``, ``codes`` or ``*_codes`` — the
    conventions every panel already follows — so the validator can reject an
    item citing a player that was never in the input.
    """
    codes: set[int] = set()

    def walk(key: str | None, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(k, v)
        elif isinstance(value, list):
            for v in value:
                walk(key, v)
        elif (key is not None and _CODE_KEY.match(key)
                and isinstance(value, int) and not isinstance(value, bool)):
            codes.add(value)

    walk(None, context)
    return codes


# --------------------------------------------------------------------------
# 2. the meta-prompt
# --------------------------------------------------------------------------


def load_meta_prompt(path: Path = META_PROMPT_PATH) -> tuple[str, str]:
    """The meta-prompt text and its sha256[:12]. Missing file raises: a
    briefing without its instructions would be untraceable."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BriefingIntelError(
            f"meta-prompt missing or unreadable at {path}: "
            f"{type(exc).__name__}: {exc}") from exc
    if not text.strip():
        raise BriefingIntelError(f"meta-prompt at {path} is empty")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return text, digest


def build_prompt(meta_text: str, context: dict[str, Any],
                 input_as_of: dict[str, str | None]) -> str:
    # The style rule ships from code, not from the meta-prompt file: it is
    # mechanically enforced downstream, and the model must read the same
    # words the validator applies. The chat analyst gets the identical block.
    return (
        meta_text
        + "\n\n" + STYLE_RULES
        + "\n\n## Panel as-of instants\n```json\n"
        + json.dumps(input_as_of, ensure_ascii=False, indent=1, default=str)
        + "\n```\n\n## Panel inputs (the ONLY facts you may cite)\n```json\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"),
                     default=str)
        + "\n```\n"
    )


# --------------------------------------------------------------------------
# 3. the one-shot model call (isolated so tests monkeypatch it)
# --------------------------------------------------------------------------


def _scrub_environment() -> None:
    """Remove auth/nesting variables the SDK child must never inherit.

    Copied from chat_agent.py (same posture, same reasons): the CLI's own
    login is the auth, this server has no legitimate use for any of these,
    and a leaked ANTHROPIC_BASE_URL once sent the CLI's OAuth token to a dev
    proxy that rejected it as revoked.
    """
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
                "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
        os.environ.pop(var, None)


def _run_model(prompt: str, *, timeout_s: float = MODEL_TIMEOUT_S) -> str:
    """One query() against the Max-plan CLI via claude-agent-sdk.

    Same auth posture as chat_agent.py: no API key here, environment
    scrubbed, ``tools=[]`` (every built-in disabled), no MCP servers — this
    is pure synthesis over the provided JSON. Returns the final assistant
    text; raises :class:`BriefingIntelError` on anything else.
    """
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )
    except ImportError as exc:
        raise BriefingIntelError(
            f"claude-agent-sdk unavailable: {exc}") from exc

    _scrub_environment()
    options = ClaudeAgentOptions(
        cwd=str(_REPO_ROOT),
        model=MODEL,
        tools=[],
        allowed_tools=[],
        disallowed_tools=["Bash", "Read", "Write", "Edit", "MultiEdit",
                          "NotebookEdit", "Glob", "Grep", "WebFetch",
                          "WebSearch", "Task", "TodoWrite"],
        max_turns=1,
    )

    async def _collect() -> str:
        parts: list[str] = []
        error: str | None = None
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content or []:
                    if isinstance(block, TextBlock) and block.text:
                        parts.append(block.text)
            elif isinstance(msg, ResultMessage):
                if msg.is_error or msg.subtype != "success":
                    error = str(msg.result or msg.subtype or "model call failed")
        if error is not None:
            raise BriefingIntelError(f"model call failed: {error}")
        return "\n".join(parts)

    async def _bounded() -> str:
        return await asyncio.wait_for(_collect(), timeout=timeout_s)

    try:
        return asyncio.run(_bounded())
    except BriefingIntelError:
        raise
    except TimeoutError as exc:
        raise BriefingIntelError(
            f"model call timed out after {timeout_s:.0f}s") from exc
    except Exception as exc:  # noqa: BLE001 - one honest error class for the ledger
        raise BriefingIntelError(
            f"model call failed: {type(exc).__name__}: {exc}") from exc


# --------------------------------------------------------------------------
# 4. parse + validate before anything is written
# --------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def parse_items(text: str) -> list[Any]:
    """Extract the items array from the model's answer.

    Accepts a fenced ``json`` block, a bare JSON object with ``items``, or a
    bare JSON array. Anything else is a parse failure — raised, never patched.
    """
    candidates: list[str] = [m.group(1) for m in _FENCE.finditer(text or "")]
    candidates.append((text or "").strip())
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        if isinstance(data, list):
            return data
    raise BriefingIntelError(
        "could not parse an items array out of the model's answer "
        f"({len(text or '')} chars); nothing was written")


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _valid_drill(drill: Any, codes: set[int]) -> bool:
    if drill is None:
        return True
    if not isinstance(drill, dict) or len(drill) != 1:
        return False
    if "drawer" in drill:
        d = drill["drawer"]
        return isinstance(d, int) and not isinstance(d, bool) and d in codes
    if "tab" in drill:
        return isinstance(drill["tab"], str) and bool(drill["tab"])
    return False


def _valid_item(item: Any, panels: set[str], codes: set[int],
                values: dict[str, list[float]] | None = None) -> bool:
    """True when the item is contract-clean. See :func:`item_problem`."""
    return item_problem(item, panels, codes, values) is None


def item_problem(item: Any, panels: set[str], codes: set[int],
                 values: dict[str, list[float]] | None = None) -> str | None:
    """The FIRST contract violation in one item, named, or None if clean.

    A count of rejections tells the owner something is wrong; a named reason
    tells him what to fix, in the meta-prompt or in the rule. Pure; every
    branch mirrors the meta-prompt.

    When ``values`` is given (panel -> the numeric values it actually
    served), the numeric-token rule applies: every number chip's value must
    exist in its named panel, and every numeric token in the headline/why
    prose must exist in one of the CITED panels — small rounding tolerance,
    percent/fraction pairs accepted. A prose number the input never served
    is an invented stat, however plausible."""
    if not isinstance(item, dict):
        return "not an object"
    if not (isinstance(item.get("headline"), str)
            and 0 < len(item["headline"]) <= HEADLINE_MAX):
        return f"headline missing or over {HEADLINE_MAX} chars"
    if not (isinstance(item.get("why"), str) and 0 < len(item["why"]) <= WHY_MAX):
        return f"why missing or over {WHY_MAX} chars"
    sev = item.get("severity")
    if not (isinstance(sev, int) and not isinstance(sev, bool) and sev in (1, 2, 3)):
        return "severity not 1, 2 or 3"
    numbers = item.get("numbers")
    if not (isinstance(numbers, list) and numbers):
        return "no number chips"
    for n in numbers:
        if not isinstance(n, dict):
            return "number chip is not an object"
        if not _is_num(n.get("value")):
            return "number chip has no numeric value"
        if not isinstance(n.get("unit"), str):
            return "number chip has no unit"
        # A number citing a panel that was not in the input is an invented
        # source — the exact dishonesty this validator exists to catch.
        if n.get("source_panel") not in panels:
            return f"chip cites unknown panel {n.get('source_panel')!r}"
        if not (n.get("as_of") is None or isinstance(n.get("as_of"), str)):
            return "chip as_of is not a string"
    item_codes = item.get("codes")
    if not isinstance(item_codes, list):
        return "codes is not a list"
    for c in item_codes:
        if not (isinstance(c, int) and not isinstance(c, bool) and c in codes):
            return f"code {c!r} is not in the input"
    sps = item.get("source_panels")
    if not (isinstance(sps, list) and sps
            and all(isinstance(p, str) for p in sps)
            and set(sps) <= panels):
        return "source_panels missing or names a panel not in the input"
    if values is not None:
        # every chip value must exist in the panel it names
        for n in numbers:
            v = float(n["value"])
            if not _num_in(v, _decimals_of(v),
                           values.get(n["source_panel"]) or []):
                return (f"chip value {v} not served by "
                        f"{n['source_panel']}")
        # every numeric token in the PROSE must exist in a cited panel —
        # the seam R2 found: chips validated, sentences did not.
        cited: list[float] = []
        for p in sps:
            cited.extend(values.get(p) or [])
        for v, d in prose_numbers(
                str(item["headline"]) + " " + str(item["why"])):
            if not _num_in(v, d, cited):
                return f"prose number {v} appears in no cited panel"
    if not _valid_drill(item.get("drill"), codes):
        return "drill target missing or not in the input"
    return None


def _destyled(item: Any) -> dict[str, Any] | None:
    """Apply the house prose rule to one item's text.

    Formatting tics (em-dashes) are rewritten in place — losslessly, so a
    true finding is never lost to punctuation. Rhetorical constructions are
    a rejection: they are the "AI slop" the owner named, and no rewrite
    turns a judgment about the manager into an observation about the squad.
    """
    if not isinstance(item, dict):
        return item, None
    out = dict(item)
    for field in ("headline", "why"):
        text = out.get(field)
        if isinstance(text, str):
            out[field] = normalize_prose(text)
            found = slop_findings(out[field])
            if found:
                return None, f"{field}: {found[0]}"
    return out, None


def validate_items(
    raw_items: list[Any],
    *,
    panels: set[str],
    codes: set[int],
    values: dict[str, list[float]] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Keep only contract-clean items; count everything dropped.

    Thin wrapper over :func:`validate_items_verbose` for callers that want
    the counts only.
    """
    kept, rejected, _ = validate_items_verbose(
        raw_items, panels=panels, codes=codes, values=values)
    return kept, rejected


def validate_items_verbose(
    raw_items: list[Any],
    *,
    panels: set[str],
    codes: set[int],
    values: dict[str, list[float]] | None = None,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Keep contract-clean items; count AND NAME everything dropped.

    Rejects are dropped loudly into ``rejected_n``, never silently, and each
    one now says which rule bit. A bare count told the owner something was
    wrong; the reason tells him whether to fix the meta-prompt or the rule,
    and a whole briefing lost to one over-eager pattern is visible instead
    of mysterious. Survivors are severity-sorted (1 first) and capped at
    :data:`MAX_ITEMS`; overflow past the cap counts as rejected too.
    ``values`` (from :func:`known_values`) arms the numeric-token rule over
    chips AND headline/why prose; None skips it (schema-only validation).
    """
    kept: list[dict[str, Any]] = []
    reasons: list[str] = []
    for item in raw_items:
        clean, style_problem = _destyled(item)
        if clean is None:
            reasons.append(style_problem or "house prose rule")
            continue
        problem = item_problem(clean, panels, codes, values)
        if problem is None:
            kept.append(clean)
        else:
            reasons.append(problem)
    kept.sort(key=lambda i: int(i["severity"]))
    if len(kept) > MAX_ITEMS:
        reasons.extend(["over the item cap"] * (len(kept) - MAX_ITEMS))
        kept = kept[:MAX_ITEMS]
    return kept, len(reasons), reasons


# --------------------------------------------------------------------------
# 5. artefact IO (atomic; read side serves the API)
# --------------------------------------------------------------------------


def write_artefact(path: Path, artefact: dict[str, Any]) -> None:
    """Write-then-rename so a crash mid-write never leaves a torn artefact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(artefact, ensure_ascii=False, indent=1,
                              default=str),
                   encoding="utf-8")
    os.replace(tmp, path)


def _parse_ts(s: Any) -> dt.datetime | None:
    if s is None:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace(" ", "T"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


#: The input panels whose CURRENT as-of the freshness rule can read cheaply,
#: mapped to the warehouse table each panel stamps its own ``as_of`` from
#: (squad_overview: fact_player_state; projection_table: fact_projection).
#: A max(as_of) over a read copy costs a file copy, never a panel run.
CURRENT_AS_OF_TABLES: dict[str, str] = {
    "squad_overview": "fact_player_state",
    "projection_table": "fact_projection",
}


def current_inputs(db_path: Path | str,
                   *, now: dt.datetime | None = None) -> dict[str, Any]:
    """The cheap present: last passed deadline + max(as_of) per input table.

    Never raises: a missing warehouse or table yields nulls plus a ``note``,
    so the route stays a read and the rule degrades to the stored-as-of
    comparison alone.
    """
    from fpl_edge.platform.query import read_copy

    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    out: dict[str, Any] = {"last_deadline_utc": None, "as_of": {}, "note": None}
    db_path = Path(db_path)
    if not db_path.exists():
        out["note"] = f"no warehouse at {db_path.name}"
        return out
    try:
        with read_copy(db_path) as wh:
            row = wh.sql(
                "SELECT max(deadline_utc) AS d FROM dim_event "
                "WHERE deadline_utc <= ?", [now])
            if not row.empty and row.iloc[0]["d"] is not None:
                d = _parse_ts(row.iloc[0]["d"])
                out["last_deadline_utc"] = d.isoformat() if d else None
            for panel, table in CURRENT_AS_OF_TABLES.items():
                df = wh.sql(f"SELECT max(as_of) AS a FROM {table}")
                v = None if df.empty else df.iloc[0]["a"]
                ts = _parse_ts(v) if v is not None else None
                out["as_of"][panel] = ts.isoformat() if ts else None
    except Exception as exc:  # noqa: BLE001 - freshness is a read, never a crash
        out["note"] = f"could not read current inputs: {type(exc).__name__}: {exc}"
    return out


def freshness(artefact: dict[str, Any], *, now: dt.datetime,
              current: dict[str, Any] | None = None) -> dict[str, Any]:
    """The freshness verdict on a stored briefing, pure over its inputs.

    ``inputs_moved`` (the older rule): a stored input as-of past the
    generated_at by more than :data:`INPUTS_MOVED_H`, OR a CURRENT panel
    as-of (``current["as_of"]``) newer than the as-of the briefing was
    written from by that same window. ``outdated`` adds the calendar: a
    deadline (``current["last_deadline_utc"]``) has passed since the
    briefing was written. ``outdated_reasons`` says which, in words the
    page prints verbatim.
    """
    current = current or {}
    generated = _parse_ts(artefact.get("generated_at"))
    stored = {k: _parse_ts(v) for k, v in
              (artefact.get("input_as_of") or {}).items()}
    window = dt.timedelta(hours=INPUTS_MOVED_H)
    reasons: list[str] = []
    inputs_moved = False
    if generated is not None:
        for ts in stored.values():
            if ts is not None and (ts - generated) > window:
                inputs_moved = True
                break
    moved_panels: list[str] = []
    for panel, cur_iso in (current.get("as_of") or {}).items():
        cur = _parse_ts(cur_iso)
        if cur is None:
            continue
        was = stored.get(panel)
        if was is None or (cur - was) > window:
            moved_panels.append(
                f"{panel} now {cur.date().isoformat()}"
                + (f" (written from {was.date().isoformat()})" if was else ""))
    if moved_panels:
        inputs_moved = True
        reasons.append("inputs moved: " + ", ".join(moved_panels))
    last_deadline = _parse_ts(current.get("last_deadline_utc"))
    deadline_passed = bool(generated is not None and last_deadline is not None
                           and generated < last_deadline)
    if deadline_passed:
        reasons.append(
            f"a deadline has passed ({last_deadline.date().isoformat()})")
    stored_dates = sorted(t for t in stored.values() if t is not None)
    return {
        "age_hours": (round((now - generated).total_seconds() / 3600.0, 2)
                      if generated is not None else None),
        "inputs_moved": inputs_moved,
        "outdated": bool(moved_panels or deadline_passed),
        "outdated_reasons": reasons,
        "deadline_passed": deadline_passed,
        # the OLDEST input the briefing was written from: the honest date
        # for "written from N Sep data"
        "written_from_as_of": (stored_dates[0].isoformat()
                               if stored_dates else None),
        "current_as_of": dict(current.get("as_of") or {}),
        "last_deadline_utc": (last_deadline.isoformat()
                              if last_deadline else None),
    }


def briefing_response(db_path: Path | str,
                      *, now: dt.datetime | None = None,
                      current: dict[str, Any] | None = None) -> dict[str, Any]:
    """The GET /api/briefing payload: artefact + freshness, or an honest gap.

    A missing artefact is 404-shaped JSON, never an exception:
    ``{"empty": true, "reason": ..., "task": "briefing_intel"}``, so the UI
    can render the gap and offer the pipeline trigger. ``current`` is the
    present the freshness rule compares against (see :func:`freshness`);
    None reads it via :func:`current_inputs`.
    """
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    path = artefact_path(db_path)
    if not path.exists():
        return {"empty": True,
                "reason": f"no briefing artefact at {path.name}; run the "
                          f"briefing_intel pipeline to generate one.",
                "task": "briefing_intel"}
    try:
        artefact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"empty": True,
                "reason": f"briefing artefact unreadable: "
                          f"{type(exc).__name__}: {exc}",
                "task": "briefing_intel"}

    if current is None:
        current = current_inputs(db_path, now=now)
    out = dict(artefact)
    out.update(freshness(artefact, now=now, current=current))
    if current.get("note"):
        out["freshness_note"] = current["note"]
    return out


# --------------------------------------------------------------------------
# the pass itself
# --------------------------------------------------------------------------


def generate(
    db_path: Path | str,
    *,
    season: str,
    now: dt.datetime | None = None,
    run_model=None,
) -> dict[str, Any]:
    """Assemble, ask once, validate, write atomically. Raises on any failure
    path (nothing is written), so the pipeline ledger records the reason.

    ``run_model`` overrides :func:`_run_model` — the seam the tests use so no
    unit test ever spawns the CLI.
    """
    from fpl_edge.store.warehouse import Warehouse

    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    started = time.monotonic()
    db_path = Path(db_path)
    if not db_path.exists():
        raise BriefingIntelError(
            f"no warehouse at {db_path}; run an ingest first")

    with Warehouse.read_copy(db_path) as wh:
        # Artefact-reading panels (the solve plan, the projection parquet)
        # resolve against the ORIGINAL directory, exactly as run_script stamps.
        wh.source_path = db_path
        results = collect_panels(wh, season=season)

    if all(res.get("empty") for res in results.values()):
        raise BriefingIntelError(
            "every input panel is empty; there is nothing to synthesise; "
            + "; ".join(f"{n}: {r.get('reason')}" for n, r in results.items()))

    context, input_as_of, dropped = build_context(results)
    meta_text, meta_hash = load_meta_prompt()
    prompt = build_prompt(meta_text, context, input_as_of)

    text = (run_model or _run_model)(prompt)
    items = parse_items(text)
    kept, rejected_n, reasons = validate_items_verbose(
        items, panels=set(context), codes=known_codes(context),
        values=known_values(context))
    if not kept:
        tally = ", ".join(
            f"{r} x{reasons.count(r)}" if reasons.count(r) > 1 else r
            for r in dict.fromkeys(reasons))
        raise BriefingIntelError(
            f"zero valid items survived validation ({rejected_n} rejected of "
            f"{len(items)} returned); nothing was written. Reasons: {tally}")

    artefact: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "model": MODEL,
        "meta_prompt_hash": meta_hash,
        "input_as_of": input_as_of,
        "items": kept,
        "rejected_n": rejected_n,
        # What the drops were, so "2 rejected" is readable on the card
        # instead of merely honest.
        "rejected_reasons": sorted(set(reasons)),
        "duration_s": round(time.monotonic() - started, 2),
    }
    if dropped:
        artefact["dropped_panels"] = dropped
    write_artefact(artefact_path(db_path), artefact)
    return artefact
