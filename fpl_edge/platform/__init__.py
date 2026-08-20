"""The decision platform: panel scripts, one guarded query path, an HTTP app.

The architecture is DESIGN.md §2 (Option B): a thin FastAPI surface over the
Python engine, adopting Argus's structural rules natively rather than adapting
its TypeScript server. The three load-bearing pieces here are

* :mod:`fpl_edge.platform.registry` -- panel scripts, the *only* data path the
  UI has, each with JSON Schema on both sides and provenance on every result;
* :mod:`fpl_edge.platform.query` -- the single guarded read path shared by the
  query endpoint, the panel scripts and the chat tools;
* :mod:`fpl_edge.platform.app` -- the §2.1 v1 routes.

Importing this package deliberately does *not* import the app: the CLI, the
Telegram bot and the tests all import the registry without paying for FastAPI.
"""

from __future__ import annotations

__all__ = ["app", "panels", "query", "registry", "scripts"]
