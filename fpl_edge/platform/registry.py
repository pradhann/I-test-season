"""The panel-script registry: the only data path the UI has.

This generalises :func:`fpl_edge.interfaces.report.register_section`. That
registry let a team add a section to the weekly report without editing the
report; this one lets a team add a panel to the platform without editing the
server. The difference is the contract: a report section returns prose and is
allowed to be absent, while a panel script returns *typed JSON* and is allowed
to be **empty but never wrong**.

Three rules are enforced here rather than trusted to each script (the Argus
discipline in docs/platform/argus_architecture.md §2.3-§2.4, adopted natively):

1. **Params and result are both JSON Schema validated.** Validating params
   turns a bad request into a 400 with a path into the offending field.
   Validating the result is the load-bearing half: a script that returns the
   wrong shape *fails its run*, so a panel pinned to it can trust the shape
   without defensive rendering. A silently misshapen payload is how a dashboard
   starts rendering ``undefined`` and nobody notices for a week.

2. **Every run gets a fresh read copy, closed afterwards.** DuckDB permits one
   writer, and the live Telegram bot holds write leases between polls. A script
   that opened the live file would block the next ingest for its whole runtime.
   Scripts therefore never receive a warehouse handle they could keep.

3. **Every result carries provenance.** ``{script, repo_sha, generated_at,
   as_of?}``. ``repo_sha`` is the actual ``git rev-parse HEAD`` of this repo, so
   a screenshot of a panel can be traced to the code that produced it. This
   repo *is* the version store -- there is no separate script-versioning system,
   which is the simplification Option B bought us.

The 10-second budget is Argus's draft-run rule (§2.1) with one deliberate
change: exceeding it marks the response ``performance: "over_budget"`` rather
than failing the run. Argus enforces latency on an *author* who can go and
optimise the SQL; here the same person is the only user, and a slow honest
answer at the deadline beats a fast error.
"""

from __future__ import annotations

import datetime as dt
import functools
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jsonschema

UTC = dt.timezone.utc

#: Soft latency budget, seconds. Over it, the run still returns.
BUDGET_S = 10.0

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The shape every script result must satisfy on top of its own schema. A
#: script that has nothing to say says so in these terms and nothing else --
#: which is the whole anti-fabrication rule expressed as a type.
EMPTY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "empty": {"const": True},
        "reason": {"type": "string", "minLength": 1},
    },
    "required": ["empty", "reason"],
}


class ScriptError(RuntimeError):
    """A script that could not be run, or one whose contract it broke."""


class ParamsInvalid(ScriptError):
    """The caller's params do not satisfy the script's params schema."""


class ResultInvalid(ScriptError):
    """The script returned something its own result schema rejects.

    This is a bug in the script, never in the caller, and it is deliberately
    loud: a panel that trusts the declared shape must be able to trust it.
    """


@dataclass(frozen=True)
class PanelScript:
    name: str
    fn: Callable[..., dict[str, Any]]
    params_schema: dict[str, Any]
    result_schema: dict[str, Any]
    title: str
    description: str

    @property
    def doc(self) -> str:
        return (self.fn.__doc__ or "").strip()

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "doc": self.doc,
            "params_schema": self.params_schema,
            "result_schema": self.result_schema,
        }


_SCRIPTS: dict[str, PanelScript] = {}


def register_script(
    name: str,
    fn: Callable[..., dict[str, Any]],
    *,
    params_schema: dict[str, Any],
    result_schema: dict[str, Any],
    title: str = "",
    description: str = "",
) -> PanelScript:
    """Register a panel script. Re-registering a name replaces it.

    Registration is explicit and in code, never filesystem auto-discovery:
    adding a data surface should be a one-line diff a reviewer can see
    (argus_architecture.md §1.2).
    """
    for label, schema in (("params_schema", params_schema), ("result_schema", result_schema)):
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError(f"{name}.{label} must be an object-rooted JSON Schema")
        jsonschema.Draft202012Validator.check_schema(schema)
    script = PanelScript(
        name=name,
        fn=fn,
        params_schema=params_schema,
        result_schema=_allow_empty(result_schema),
        title=title or name.replace("_", " ").title(),
        description=description,
    )
    _SCRIPTS[name] = script
    return script


def _allow_empty(result_schema: dict[str, Any]) -> dict[str, Any]:
    """Every result schema also admits the honest-empty shape.

    Scripts must be able to say "there is no data for this yet" without each
    one having to bolt an optional ``empty`` branch onto its own schema, and
    without the alternative: inventing plausible rows so the panel looks alive.
    An empty warehouse should produce an empty panel that explains itself.
    """
    return {"oneOf": [result_schema, EMPTY_SCHEMA]}


def script(name: str) -> PanelScript:
    if name not in _SCRIPTS:
        raise KeyError(
            f"no panel script named {name!r}; registered: {sorted(_SCRIPTS) or '(none)'}"
        )
    return _SCRIPTS[name]


def registered() -> tuple[str, ...]:
    return tuple(sorted(_SCRIPTS))


def describe_all() -> list[dict[str, Any]]:
    return [_SCRIPTS[n].describe() for n in sorted(_SCRIPTS)]


def clear_registry() -> None:
    """Test hook. Production never calls this."""
    _SCRIPTS.clear()


@functools.lru_cache(maxsize=1)
def repo_sha() -> str:
    """``git rev-parse HEAD``, or ``"unknown"`` outside a checkout.

    Cached: this is called on every script run and shelling out per panel
    refresh is pure waste. The process is restarted on deploy, which is exactly
    when the value can change.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip() or "unknown"
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def validate_params(script_obj: PanelScript, params: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(params or {})
    validator = jsonschema.Draft202012Validator(script_obj.params_schema)
    errors = sorted(validator.iter_errors(params), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(root)"
        raise ParamsInvalid(f"{script_obj.name}: params invalid at {where}: {first.message}")
    # Fill declared defaults so a script never has to re-specify them.
    for key, spec in (script_obj.params_schema.get("properties") or {}).items():
        if key not in params and isinstance(spec, dict) and "default" in spec:
            params[key] = spec["default"]
    return params


def validate_result(script_obj: PanelScript, result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ResultInvalid(
            f"{script_obj.name}: returned {type(result).__name__}, not an object"
        )
    validator = jsonschema.Draft202012Validator(script_obj.result_schema)
    errors = sorted(validator.iter_errors(result), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(root)"
        raise ResultInvalid(
            f"{script_obj.name}: result violates its own result_schema at {where}: "
            f"{first.message}. Fix the script -- a panel pinned to this shape "
            f"cannot render a payload the schema rejects."
        )
    return result


@dataclass(frozen=True)
class ScriptRun:
    script: str
    result: dict[str, Any]
    provenance: dict[str, Any]
    duration_ms: int
    performance: str = "ok"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "script": self.script,
            "result": self.result,
            "provenance": self.provenance,
            "duration_ms": self.duration_ms,
            "performance": self.performance,
            "notes": self.notes,
        }


def run_script(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    db: Path | str | None = None,
) -> ScriptRun:
    """Validate, execute against a fresh read copy, validate, stamp provenance.

    ``db`` overrides the warehouse path, which is what the tests use to point at
    a temporary file. Production passes nothing and gets ``DEFAULT_DB``.
    """
    from fpl_edge.platform.query import read_copy
    from fpl_edge.store.warehouse import DEFAULT_DB

    script_obj = script(name)
    clean = validate_params(script_obj, params)
    path = Path(db) if db is not None else DEFAULT_DB

    started = time.monotonic()
    generated_at = dt.datetime.now(UTC)
    if not Path(path).exists():
        # A missing warehouse is an honest empty, not a stack trace: a fresh
        # clone has no data file until the first ingest runs.
        result: dict[str, Any] = {
            "empty": True,
            "reason": f"no warehouse at {path}; run an ingest first (`make ingest`).",
        }
    else:
        with read_copy(path) as wh:
            # Scripts read the database from a temp copy but artefacts (the
            # projection parquet, solved plans) live next to the ORIGINAL file.
            # Without this the handle knows only about the scratch directory.
            wh.source_path = Path(path)
            result = script_obj.fn(wh, **clean)
    duration_ms = int((time.monotonic() - started) * 1000)

    result = validate_result(script_obj, result)

    provenance: dict[str, Any] = {
        "script": name,
        "repo_sha": repo_sha(),
        "generated_at": generated_at.isoformat(),
        "params": clean,
    }
    as_of = result.get("as_of") if isinstance(result, dict) else None
    if as_of:
        provenance["as_of"] = as_of

    over = duration_ms > BUDGET_S * 1000
    notes = []
    if over:
        notes.append(
            f"took {duration_ms / 1000:.1f}s, over the {BUDGET_S:.0f}s panel budget. "
            f"Push filtering and aggregation into SQL; if it genuinely cannot fit "
            f"the budget it belongs in a monitor, not a panel."
        )
    return ScriptRun(
        script=name,
        result=result,
        provenance=provenance,
        duration_ms=duration_ms,
        performance="over_budget" if over else "ok",
        notes=notes,
    )
