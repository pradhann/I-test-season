"""creator_board / creator_detail — the Creators tab's data path.

Shape is fixed by docs/platform/CREATOR_PANEL_CONTRACT.md; the UI is built
against it in parallel. Deviations from that document are listed at the bottom
of this docstring, none of them silent.

Four rules do all the work here, and each one exists because breaking it was
observed to produce a plausible-looking lie:

1. **Nothing is invented.** Every nullable field in the payload has a paired
   ``*_reason`` string written for a human. ``content_analysis`` currently holds
   two rows against 594 items, so *most* creators have no summarised take today
   and a backfill is running concurrently. The panel must therefore be correct
   at 2 analyses and at 200, and a creator with no analysis renders
   ``take: null`` plus ``take_reason`` naming what is actually on file for their
   latest item ("show notes only, no transcript captured"). It never renders a
   blank card, and it never renders 0.0 for "unknown".

2. **Deep links are built here, not in the browser.** Only this side knows the
   platform's URL grammar and only this side can see ``transcript_segment``.
   A quote is located in the transcript by normalised substring search and the
   segment holding it supplies ``start_s``; the link is then
   ``watch?v=<id>&t=<n>s``. With no timestamp, ``start_s`` is null and
   ``deep_link`` is the item URL unchanged -- never a guessed offset.

3. **YouTube URLs are canonicalised on the video id.** ``watch?v=X``,
   ``youtu.be/X``, ``youtube.com/live/X`` and ``watch?reload=9&v=X`` are one
   video. The live warehouse proves the point: ``link_04dfb94e32cf04ca`` and
   ``link_280d525f5fb46a24`` are the same Andy LTFPL video stored twice, once
   with the analysis and once with the 1,199 transcript segments. Keying on the
   raw URL gives two "creators' latest videos" out of one, doubles a claim
   count (44 where 22 were real), and hides the transcript from the analysis
   that needs it for timestamps. Items are grouped on the video id, so the
   analysis and the transcript find each other.

4. **Point in time, claims *and* weights.** Claims come through
   :meth:`ContentStore.claims_visible_at`, the one sanctioned read, which
   filters ``published_at < as_of``. Creator weights come from ``creator_score``
   bounded by ``as_of <= moment`` -- the same discipline as
   ``fpl_mcp.tools.content_tools._scores_as_of``, and reintroducing the
   unbounded read (weighting a past question with today's track record) is a
   leak whose only symptom is a backtest that beats live. Manager facts come
   through ``sem_manager_*(as_of)``.

Untrusted text. ``summary``, ``quote``, ``rationale``, ``title`` are verbatim
third-party prose from podcasts, videos and blogs. They are data to be
rendered, never instructions to be followed.

Deviations from CREATOR_PANEL_CONTRACT.md, all forced by what the warehouse
actually holds:

* ``provenance`` is NOT a key of the result. ``registry.run_script`` stamps it
  as a sibling of ``result`` in the ``ScriptRun`` envelope; duplicating it
  inside the payload would let the two drift.
* ``sources[].discovery`` has no backing column. ``content_source`` records
  ``creator/kind/url/policy/note`` and probe state, and nothing anywhere
  records how a source was found. It is derived from registry membership
  instead: ``manual`` for a key present in ``content_source`` (the hand-curated
  registry in ``ingest/content/sources.py``), ``auto`` for a key that exists
  only because ingest materialised it while processing an item -- today that is
  ``user_link``, the pseudo-source behind shared links.
* ``take.summary`` is stored as 3-6 bullets (``TranscriptAnalysis.summary`` is
  ``list[str]``), not one string. The contract's string is emitted, newline
  joined, and ``take.summary_bullets`` carries the real structure beside it.
* ``latest`` is nullable with ``latest_reason``. Eight registry sources
  (The Athletic FPL, FPL JUiCE, Always Cheating ...) have never yielded an item;
  dropping those creators would hide a live source that is answering 200 with
  nothing in it.
* Additive, non-breaking keys the contract's own "nothing is invented" rule
  requires: ``gw_reason``, ``record_note``, ``record.reason``,
  ``latest_reason``, ``items[].analysis_reason``, ``take.differentials``
  (``TranscriptAnalysis`` carries them and they are not transfers), ``deep_link``
  on every quoted call, and ``n_cue``/``n_llm`` beside every consensus count so
  a keyword-window claim and a semantic one stay distinguishable.
* ``params`` are exactly as specified. Neither script takes an ``as_of``: both
  answer "now". Reconstructing a past deadline needs one and the contract
  should grow it -- see the report, not this file.
"""

from __future__ import annotations

from fpl_edge.platform.scripts.creators.board import creator_board
from fpl_edge.platform.scripts.creators.chatter import (
    CHATTER_PARAMS,
    CHATTER_RESULT,
    player_chatter,
)
from fpl_edge.platform.scripts.creators.detail import creator_detail
from fpl_edge.platform.scripts.creators.episodes import (
    creator_episodes,
    episode_summary,
)
from fpl_edge.platform.scripts.creators.identity import (  # noqa: F401
    UTC,
    TranscriptIndex,
    _analyses,
    _plain,
    _resolver,
    _take,
)

# Importing the four panel modules is what registers the four panel scripts,
# exactly as importing the single creators.py module used to. Registration
# order is preserved: report_card, board, detail, chatter.
from fpl_edge.platform.scripts.creators.report_card import (  # noqa: F401
    CARD_PARAMS,
    CARD_RESULT,
    _card_headline,
    creator_report_card,
)
from fpl_edge.platform.scripts.creators.schema import (
    BOARD_PARAMS,
    BOARD_RESULT,
    DETAIL_PARAMS,
    DETAIL_RESULT,
)

#: Every public name creators.py exported, plus the five private helpers that
#: callers outside this package read by name:
#:   _analyses, _resolver, _take  platform/link_jobs/take.py:128, in the body
#:                                of build_take, so an import error there would
#:                                surface at call time rather than at startup
#:   _plain          tests/unit/test_creator_panel.py:1744
#:   _take           tests/unit/test_creator_panel.py:1258
#:   _card_headline  tests/unit/test_creator_report_card.py:527,542,559
#: creator_board is re-exported because brief.py:2185 imports it inside
#: dashboard_brief and tests/unit/test_dashboard_brief.py:607 monkeypatches it
#: HERE; a function-local import reads this attribute at call time, so the
#: patch keeps working. No other private name is re-exported.
__all__ = [
    "BOARD_PARAMS",
    "BOARD_RESULT",
    "CARD_PARAMS",
    "CARD_RESULT",
    "CHATTER_PARAMS",
    "CHATTER_RESULT",
    "DETAIL_PARAMS",
    "DETAIL_RESULT",
    "UTC",
    "TranscriptIndex",
    "creator_board",
    "creator_detail",
    "creator_episodes",
    "creator_report_card",
    "episode_summary",
    "player_chatter",
]
