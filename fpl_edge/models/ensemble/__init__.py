"""Projection sources: one adapter per estimate, all reduced to one shape.

STATUS: production, not research. ``sources.py`` is the only module left, and
``platform/scripts/fixtures/board.py:68`` imports ``odds_with_fixture_keys``
from it inside ``_resolved_odds``, so the fixtures panel reads this package on
every call. The 2026-08-20 reachability audit recorded the opposite;
ARCHITECTURE_REVIEW.md Section 2 check 4 corrects it.

``backtest.py``, ``frame.py`` and ``weights.py`` were deleted on 2026-09-18
(ARCHITECTURE_REVIEW.md Section 4 row 29). None of the three had an importer
outside this package, none wrote an artefact, and the walk-forward scoring they
carried is served by ``eval/projection_scoring.py``, which the settlement chain
runs as its ``score_projections`` step.
"""
