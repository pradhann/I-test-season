"""The platform HTTP surface, DESIGN.md §2.1, implemented exactly.

    GET  /api/panels                  registered panels + their pinned scripts
    POST /api/scripts/{name}/run      {result, provenance}, 10s soft budget
    POST /api/query                   guarded read-only SQL
    GET  /api/inbox                   deliveries, newest first
    POST /api/inbox/{id}/ack          acknowledge one
    GET  /api/monitors                monitor definitions
    POST /api/ingest/link             {url} -> {job_id, stages}
    GET  /api/ingest/link/{job_id}    {stage, pct, eta_s, done, error, item_id}
    POST /api/ingest/link/{job_id}/accept   transcribe it (the only GPU spend)
    POST /api/ingest/link/{job_id}/decline  say no at the preview; nothing stored
    DELETE /api/ingest/link/{job_id}  abort: declines if parked, cancels if running
    POST /api/content/items/{id}/discard   hide an ingested item (deletes nothing)
    POST /api/content/items/{id}/restore   put it back
    POST /api/content/items/{id}/gameweek  correct the gameweek, by hand, on record
    POST /api/conversations           new agent conversation -> {conv_id}
    GET  /api/conversations           conversation metas, newest first
    DELETE /api/conversations/{id}    delete one conversation (409 in-flight)
    POST /api/conversations/{id}/chat start an agent turn (202; 409 in-flight)
    GET  /api/conversations/{id}/stream  SSE: replay > after, then live
    GET  /api/conversations/{id}/events  JSON page (non-SSE fallback)
    POST /api/conversations/{id}/stop kill the in-flight turn
    GET  /api/chat/assets/{id}.{png|svg}  charts the agent's python_viz produced
    POST /api/players/{code}/fetch_profile  start the on-demand Understat fetch (202)
    GET  /api/players/{code}/fetch_profile  its state: idle|running|done|error
    POST /api/pipelines/{task_id}/run   trigger one registry pipeline (202);
                                        metered without confirm -> needs_confirm
    GET  /api/pipelines/{task_id}/run_state  the poller: state + latest ledger row
    GET  /api/content/sources          every source with ONE measured state
    POST /api/content/sources/{key}/fetch       fetch just this source (202)
    GET  /api/content/sources/{key}/fetch_state what that fetch got
    GET  /api/briefing                model-authored salience artefact + freshness;
                                      404-shaped JSON when it does not exist yet
    GET  /api/transcripts/queue       what the Mac ASR worker should transcribe
    POST /api/transcripts             one finished transcription, bearer auth
    /                                 the built web/ bundle, if present

What is deliberately *absent* is as load-bearing as what is here: no route
takes SQL from a panel and no route accepts credentials. TWO routes write to
the CORPUS, and both are named here because this file is the map.

``POST /api/ingest/link`` is the owner pasting a link at their own explicit
request, and it writes only by calling
:func:`fpl_edge.interfaces.creators.ingest_link`, the sanctioned single-URL
path that the robots exception in ``docs/data_sources.md`` §7A was granted for.
It opens no warehouse handle of its own and adds no second ingester; see
:mod:`fpl_edge.platform.link_jobs`.

``POST /api/transcripts`` is the second, added when transcription moved to the
owner's Mac because a Linux container has no Metal GPU (DEPLOYMENT.md §1). It
writes through :func:`fpl_edge.ingest.content.asr.store_transcription`, the one
sanctioned transcript write path, so a pushed transcript's segment and
provenance rows are identical to a locally produced one. It is the only route
in this app behind a bearer secret; see
:mod:`fpl_edge.platform.app.routes_transcripts`.

The server holds no API key -- the chat escalation
shells out to the Max-plan `claude` CLI, which owns its own auth (DESIGN §2
item 6), and every secret the engine needs is read through
:func:`fpl_edge.config.secret` at the point of use.

Binding: loopback only, by default and in the plist. This is a single-operator
local platform with no auth layer; a server that answers on 0.0.0.0 would put
an unauthenticated SQL endpoint on the local network.
"""

from __future__ import annotations

# Importing the scripts package is what registers the five panel scripts.
import fpl_edge.platform.scripts  # noqa: F401
from fpl_edge.platform.app.factory import create_app
from fpl_edge.platform.app.helpers import serve

#: The two names app.py exported and anything outside this package imports.
#: WEB_DIST and _TRANSFER_PLAN_PATH are deliberately NOT re-exported: a test
#: that monkeypatched them here would patch a copy and go silently inert.
__all__ = ["create_app", "serve"]
