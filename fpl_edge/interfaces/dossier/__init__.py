"""Everything we know about one player at one instant.

Three modules::

    load.py      the one warehouse read, and the player the query resolves to
    sections.py  the sixteen builders, each returning a body or a reason
    render.py    build, and the CLI / Telegram / MCP views of the result

This file re-exports the surface the single ``dossier.py`` module had, so
``fpl_edge/cli/main.py``, ``fpl_edge/interfaces/telegram.py``, the
``player_dossier`` panel and ``tests/unit/test_dossier.py`` import unchanged
(ARCHITECTURE_REVIEW.md Section 4 row 18).
"""

from fpl_edge.interfaces.dossier.load import (
    DEFAULT_HISTORY,
    DEFAULT_SEASON,
    POS_NAME,
    PROJECTION_PATH,
    UTC,
    resolve,
)
from fpl_edge.interfaces.dossier.render import (
    Dossier,
    build,
    build_text,
    mcp_payload,
    register_cli,
    telegram_addendum,
)
from fpl_edge.interfaces.dossier.sections import (
    EXPECTED,
    Section,
)

__all__ = [
    "DEFAULT_HISTORY",
    "DEFAULT_SEASON",
    "Dossier",
    "EXPECTED",
    "POS_NAME",
    "PROJECTION_PATH",
    "Section",
    "UTC",
    "build",
    "build_text",
    "mcp_payload",
    "register_cli",
    "resolve",
    "telegram_addendum",
]
