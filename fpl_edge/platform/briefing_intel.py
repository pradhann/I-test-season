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
    from fpl_edge.platform import registry as panel_registry

    # Registration IS the import: the web server has these loaded, but the
    # scheduler's process does not, and an empty registry here turned every
    # panel into "no panel script named ..." on the 07:40 firing.
    import fpl_edge.platform.scripts  # noqa: F401 - imported for side effect

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
    return (
        meta_text
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


def _valid_item(item: Any, panels: set[str], codes: set[int]) -> bool:
    """One item against the contract. Pure; every rule mirrors the meta-prompt."""
    if not isinstance(item, dict):
        return False
    if not (isinstance(item.get("headline"), str)
            and 0 < len(item["headline"]) <= HEADLINE_MAX):
        return False
    if not (isinstance(item.get("why"), str) and 0 < len(item["why"]) <= WHY_MAX):
        return False
    sev = item.get("severity")
    if not (isinstance(sev, int) and not isinstance(sev, bool) and sev in (1, 2, 3)):
        return False
    numbers = item.get("numbers")
    if not (isinstance(numbers, list) and numbers):
        return False
    for n in numbers:
        if not isinstance(n, dict):
            return False
        if not _is_num(n.get("value")):
            return False
        if not isinstance(n.get("unit"), str):
            return False
        # A number citing a panel that was not in the input is an invented
        # source — the exact dishonesty this validator exists to catch.
        if n.get("source_panel") not in panels:
            return False
        if not (n.get("as_of") is None or isinstance(n.get("as_of"), str)):
            return False
    item_codes = item.get("codes")
    if not isinstance(item_codes, list):
        return False
    for c in item_codes:
        if not (isinstance(c, int) and not isinstance(c, bool) and c in codes):
            return False
    sps = item.get("source_panels")
    if not (isinstance(sps, list) and sps
            and all(isinstance(p, str) for p in sps)
            and set(sps) <= panels):
        return False
    return _valid_drill(item.get("drill"), codes)


def validate_items(
    raw_items: list[Any],
    *,
    panels: set[str],
    codes: set[int],
) -> tuple[list[dict[str, Any]], int]:
    """Keep only contract-clean items; count everything dropped.

    Rejects are counted — dropped loudly into ``rejected_n``, never
    silently — and the survivors are severity-sorted (1 first) and capped at
    :data:`MAX_ITEMS`; overflow past the cap counts as rejected too.
    """
    kept: list[dict[str, Any]] = []
    rejected = 0
    for item in raw_items:
        if _valid_item(item, panels, codes):
            kept.append(item)
        else:
            rejected += 1
    kept.sort(key=lambda i: int(i["severity"]))
    if len(kept) > MAX_ITEMS:
        rejected += len(kept) - MAX_ITEMS
        kept = kept[:MAX_ITEMS]
    return kept, rejected


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


def briefing_response(db_path: Path | str,
                      *, now: dt.datetime | None = None) -> dict[str, Any]:
    """The GET /api/briefing payload: artefact + freshness, or an honest gap.

    A missing artefact is 404-shaped JSON, never an exception —
    ``{"empty": true, "reason": …, "task": "briefing_intel"}`` — so the UI
    can render the gap and offer the pipeline trigger.
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

    generated = _parse_ts(artefact.get("generated_at"))
    age_hours = (round((now - generated).total_seconds() / 3600.0, 2)
                 if generated is not None else None)
    inputs_moved = False
    if generated is not None:
        for as_of in (artefact.get("input_as_of") or {}).values():
            ts = _parse_ts(as_of)
            if ts is not None and (ts - generated) > dt.timedelta(hours=INPUTS_MOVED_H):
                inputs_moved = True
                break
    out = dict(artefact)
    out["age_hours"] = age_hours
    out["inputs_moved"] = inputs_moved
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
            "every input panel is empty — there is nothing to synthesise; "
            + "; ".join(f"{n}: {r.get('reason')}" for n, r in results.items()))

    context, input_as_of, dropped = build_context(results)
    meta_text, meta_hash = load_meta_prompt()
    prompt = build_prompt(meta_text, context, input_as_of)

    text = (run_model or _run_model)(prompt)
    items = parse_items(text)
    kept, rejected_n = validate_items(
        items, panels=set(context), codes=known_codes(context))
    if not kept:
        raise BriefingIntelError(
            f"zero valid items survived validation ({rejected_n} rejected of "
            f"{len(items)} returned); nothing was written")

    artefact: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "model": MODEL,
        "meta_prompt_hash": meta_hash,
        "input_as_of": input_as_of,
        "items": kept,
        "rejected_n": rejected_n,
        "duration_s": round(time.monotonic() - started, 2),
    }
    if dropped:
        artefact["dropped_panels"] = dropped
    write_artefact(artefact_path(db_path), artefact)
    return artefact
