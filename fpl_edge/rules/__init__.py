"""Verified FPL rule access.

Every rule the engine depends on is declared in ``registry.yaml`` together with
the source it was read from and the timestamp it was fetched. Rules marked
``verified: false`` raise :class:`UnverifiedRuleError` on access, so the engine
fails loudly rather than silently guessing.
"""

from fpl_edge.rules.loader import (
    Rule,
    RuleNotFoundError,
    RuleRegistry,
    UnverifiedRuleError,
    rules,
)

__all__ = [
    "Rule",
    "RuleRegistry",
    "UnverifiedRuleError",
    "RuleNotFoundError",
    "rules",
]
