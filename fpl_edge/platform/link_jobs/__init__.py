"""Pasted links: preflight, approve, ingest, take.

Three modules::

    preflight.py  read-only: what the URL is and what ingesting it would cost
    take.py       the assembled take, and the owner's discard/correct actions
    runner.py     the job machine the API drives

This file re-exports the surface the single ``link_jobs.py`` module had, so
``fpl_edge/platform/app/routes_content.py``, ``app/helpers.py`` and
``tests/unit/test_link_jobs.py`` import unchanged (ARCHITECTURE_REVIEW.md
Section 4 row 17). ``platform/app.py`` was the importer named here until that
module became the ``app/`` package in the same refactor.
"""

from fpl_edge.platform.link_jobs.preflight import (
    ASR_RATE,
    CAPTION_RATE,
    MIN_SUBSTANTIVE_CHARS,
    PREVIEW_TTL,
    STAGES,
    UTC,
    LinkRefused,
    Preflight,
    preflight,
)
from fpl_edge.platform.link_jobs.runner import (
    JOB_TTL,
    LINK_JOBS_DIR,
    REPO_ROOT,
    JobAlreadyFinished,
    LinkJobs,
    NotAwaitingDecision,
    UnknownJob,
)
from fpl_edge.platform.link_jobs.take import (
    WRITE_ATTEMPTS,
    build_take,
    correct_gameweek,
    discard_item,
    ingest_with_retry,
    restore_item,
)

__all__ = [
    "ASR_RATE",
    "CAPTION_RATE",
    "JOB_TTL",
    "JobAlreadyFinished",
    "LINK_JOBS_DIR",
    "LinkJobs",
    "LinkRefused",
    "MIN_SUBSTANTIVE_CHARS",
    "NotAwaitingDecision",
    "PREVIEW_TTL",
    "Preflight",
    "REPO_ROOT",
    "STAGES",
    "UTC",
    "UnknownJob",
    "WRITE_ATTEMPTS",
    "build_take",
    "correct_gameweek",
    "discard_item",
    "ingest_with_retry",
    "preflight",
    "restore_item",
]
