"""Load and access the verified FPL rule registry."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_REGISTRY_PATH = Path(__file__).with_name("registry.yaml")

# Keys inside registry.yaml that are metadata rather than rule namespaces.
_META_KEYS = frozenset({"season", "sources"})

# A dict is a rule leaf (rather than a namespace) iff it carries this key.
_LEAF_MARKER = "value"


class RuleNotFoundError(KeyError):
    """Raised when a dotted rule path does not exist in the registry."""


class UnverifiedRuleError(RuntimeError):
    """Raised when code reads a rule that has not been verified against a source.

    This is deliberately fatal. A wrong rule silently corrupts every downstream
    number, so the engine refuses to run on a guess.
    """


@dataclass(frozen=True, slots=True)
class Rule:
    """A single rule with its provenance."""

    path: str
    value: Any
    verified: bool
    source: tuple[str, ...]
    note: str | None = None

    def require(self) -> Any:
        """Return the value, raising if the rule is unverified."""
        if not self.verified:
            raise UnverifiedRuleError(
                f"Rule {self.path!r} is UNVERIFIED and must not be used. "
                f"note={self.note!r}. Verify it against an authoritative source "
                f"and set verified: true in registry.yaml."
            )
        return self.value


class RuleRegistry:
    """Dotted-path access to the rule registry."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        self._flat: dict[str, Rule] = {}
        for namespace, body in raw.items():
            if namespace in _META_KEYS:
                continue
            self._walk(namespace, body)

    def _walk(self, prefix: str, node: Any) -> None:
        if isinstance(node, dict) and _LEAF_MARKER in node:
            src = node.get("source") or ()
            if isinstance(src, str):
                src = (src,)
            self._flat[prefix] = Rule(
                path=prefix,
                value=node[_LEAF_MARKER],
                verified=bool(node.get("verified", False)),
                source=tuple(src),
                note=node.get("note"),
            )
            return
        if isinstance(node, dict):
            for key, child in node.items():
                self._walk(f"{prefix}.{key}", child)

    @property
    def season(self) -> str:
        return self._raw["season"]

    @property
    def sources(self) -> dict[str, Any]:
        return self._raw["sources"]

    def rule(self, path: str) -> Rule:
        """Return the :class:`Rule` object at ``path`` without verification checks."""
        try:
            return self._flat[path]
        except KeyError as exc:
            raise RuleNotFoundError(
                f"No rule at {path!r}. Known paths: {sorted(self._flat)[:10]}..."
            ) from exc

    def get(self, path: str) -> Any:
        """Return a verified rule value, raising :class:`UnverifiedRuleError` otherwise.

        This is the only accessor production code should use.
        """
        return self.rule(path).require()

    def paths(self) -> list[str]:
        return sorted(self._flat)

    def unverified(self) -> list[Rule]:
        """Every rule currently lacking verification. Surfaced in the weekly report."""
        return [r for r in self._flat.values() if not r.verified]


@functools.lru_cache(maxsize=1)
def rules() -> RuleRegistry:
    """Return the process-wide rule registry."""
    with _REGISTRY_PATH.open() as fh:
        return RuleRegistry(yaml.safe_load(fh))
