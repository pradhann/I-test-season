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

    ``FPL_EDGE_REPO_SHA`` wins when it is set. The container image carries no
    ``.git`` directory, so without it every panel in the deployed UI would be
    stamped "unknown" and a screenshot could not be traced to the code that
    produced it. The Dockerfile passes the sha as a build argument.
    """
    import os

    override = os.environ.get("FPL_EDGE_REPO_SHA", "").strip()
    if override:
        return override
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


#: The tables a warehouse with any data at all has at least one row in. A
#: single row in any of them means the file holds data, so a panel that then
#: raises has a bug and must stay a 500.
_SPINE_TABLES: tuple[str, ...] = ("dim_player", "dim_team", "dim_event",
                                  "fact_fixture")

#: How much of a raising script's exception text rides into the gap reason.
_GAP_REASON_CHARS = 200


def warehouse_is_unseeded(wh) -> bool:
    """True when the warehouse holds schema and no rows at all.

    A first boot on a fresh Railway volume creates the file, applies
    ``schema.sql`` and the package migrations, and stops. Every table is
    present and every one is empty. A script written against real rows can
    raise anywhere in that state, and the honest answer there is that the data
    is absent rather than that the script is broken.

    Deliberately narrow. One row in any spine table means the file holds data
    and a raising panel is a defect, which keeps the error shape the UI draws
    as "could not load" distinct from the honest empty it draws as "no data".
    A warehouse that cannot be questioned at all counts as unseeded, because
    nothing it could be in that state would make an exception a panel's fault.
    """
    try:
        for table in _SPINE_TABLES:
            present = int(wh.sql(
                "SELECT count(*) c FROM information_schema.tables "
                "WHERE table_name = ?", [table]).iloc[0]["c"])
            if not present:
                continue
            if int(wh.sql(f"SELECT count(*) c FROM {table}").iloc[0]["c"]):
                return False
        return True
    except Exception:  # noqa: BLE001 - see the docstring
        return True


def accepts_ctx(fn: Callable[..., Any]) -> bool:
    """True when a script declares a ``ctx`` parameter.

    The context is passed by inspection rather than to every script, so a
    script that reads only shared warehouse state cannot receive a user and
    cannot start depending on one by accident.
    """
    import inspect

    try:
        return "ctx" in inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins and C callables
        return False


def run_script(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    db: Path | str | None = None,
    ctx: Any | None = None,
) -> ScriptRun:
    """Validate, execute against a fresh read copy, validate, stamp provenance.

    ``db`` overrides the warehouse path, which is what the tests use to point at
    a temporary file. Production passes nothing and gets ``DEFAULT_DB``.

    ``ctx`` is the requesting user (:class:`fpl_edge.platform.users.UserContext`)
    and reaches only the scripts that declare a ``ctx`` parameter. It is NOT a
    param: params are echoed back in the run record and in every panel's
    provenance, so a user id in there would ride into every screenshot and
    every log line. A caller that passes none gets the owner, which is what the
    scheduled jobs, the CLI and the test suite are.
    """
    from fpl_edge.platform.query import read_copy
    from fpl_edge.store.warehouse import DEFAULT_DB

    script_obj = script(name)
    clean = validate_params(script_obj, params)
    path = Path(db) if db is not None else DEFAULT_DB
    call_kwargs = dict(clean)
    if accepts_ctx(script_obj.fn):
        from fpl_edge.platform.users import owner_context

        call_kwargs["ctx"] = ctx if ctx is not None else owner_context()

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
            try:
                result = script_obj.fn(wh, **call_kwargs)
            except Exception as exc:  # noqa: BLE001 - re-raised unless unseeded
                # The one central place a fresh deployment's empty warehouse
                # becomes a structured gap instead of a 500 (DEPLOYMENT.md
                # §11.2 step 5). The distinction the UI depends on survives:
                # a warehouse WITH data whose panel raised is still an error,
                # re-raised here and served as {error, panel, reason}. Fixing
                # this per script would mean eighteen copies of the same
                # try/except and a nineteenth panel that forgot it.
                if not warehouse_is_unseeded(wh):
                    raise
                detail = f"{type(exc).__name__}: {exc}"
                if len(detail) > _GAP_REASON_CHARS:
                    detail = detail[: _GAP_REASON_CHARS - 1] + "..."
                result = {
                    "empty": True,
                    "reason": (
                        f"the warehouse at {path} has schema and no rows in "
                        f"any of {', '.join(_SPINE_TABLES)}, so {name} has "
                        f"nothing to read. Seed it or wait for the first "
                        f"ingest. The script stopped at: {detail}"
                    ),
                }
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
