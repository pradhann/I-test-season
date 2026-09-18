"""The Mac ASR worker. DEPLOYMENT.md §1.8.

Transcription is the one part of the content pipeline that spends no metered
credits and no model tokens: ``mlx-whisper`` runs on this machine's Metal GPU
at 11.7x to 12.3x realtime, measured 2026-09-03. A Railway container is Linux
with no Metal, ``pyproject.toml`` guards the dependency with
``sys_platform == 'darwin'``, and moving to a CPU engine would both bill per
second and make every new transcript incomparable with the 62 episodes already
in the corpus. So the queue and the warehouse stay on the server and the GPU
work stays here.

This is a script rather than an extension of the ``content_transcribe``
registry task. The task writes through a local DuckDB file, claims a
``dag_firing`` row and lands a ``fetch_run`` row, and after the move none of
those rows are read by anything: the authority is the server's warehouse.
Keeping the task would mean keeping a second warehouse on this machine whose
only content is bookkeeping nobody looks at. What is worth reusing is reused by
import and not reimplemented: ``asr.fetch_audio``, ``asr.transcribe_file``,
``asr.Transcription`` and ``asr.Segment``. This file contains no transcription
code.

The loop
--------

1. Read the bearer secret through :func:`fpl_edge.config.secret`, which reads
   the environment first. Keep it in the macOS keychain rather than ``.env``,
   which sits in the repo tree and is readable by anything with access to the
   working directory::

       security add-generic-password -s fpl-edge-transcript-push -a "$USER" -w
       export TRANSCRIPT_PUSH_TOKEN="$(security find-generic-password \\
           -s fpl-edge-transcript-push -w)"

2. ``GET /api/transcripts/queue`` for what the server says is owed.
3. Per item, and only for items that need the GPU: ``asr.fetch_audio``, which
   consults the local content-addressed audio cache before the network, then
   ``asr.transcribe_file``.
4. ``POST /api/transcripts`` with the finished transcription.
5. The wall budget is checked BETWEEN items and never inside one, exactly as
   the registry task checks it, so the worst-case overrun is one episode.

Failure rules
-------------

A 401 stops the run and exits non-zero, with no retry: a wrong token is not
something a retry fixes, and launchd records the exit. Nothing is lost, because
the queue belongs to the server and an item that was not pushed is still queued
on the next run. A network failure retries with backoff and then exits
non-zero; the GPU time is spent again next run and nothing in the warehouse is
inconsistent. A 409 means the server holds a different transcript for that item
and the push did not force it; the item is reported and the run continues.

Usage::

    uv run python scripts/mac_transcribe_worker.py --base-url https://... --once
    uv run python scripts/mac_transcribe_worker.py --base-url https://... --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

UTC = dt.UTC

#: The service variable and keychain item the token lives in.
TOKEN_ENV = "TRANSCRIPT_PUSH_TOKEN"
KEYCHAIN_SERVICE = "fpl-edge-transcript-push"

#: How long one HTTP call may take. The push carries a whole transcript, so it
#: is given more room than the queue poll.
QUEUE_TIMEOUT_S = 60.0
PUSH_TIMEOUT_S = 180.0

#: Backoff between retries of a failed HTTP call, seconds.
RETRY_DELAYS_S = (5.0, 20.0, 60.0)


class Unauthorized(RuntimeError):
    """The server refused the bearer token. Fatal, never retried."""


def read_token() -> str:
    """The bearer secret, from the environment or the keychain.

    :func:`fpl_edge.config.secret` reads the environment first and never logs
    values. The keychain read is the fallback so the launchd wrapper does not
    have to export anything.
    """
    from fpl_edge import config

    value = config.secret(TOKEN_ENV, required=False)
    if value:
        return value
    import subprocess

    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"{TOKEN_ENV} is not set and the keychain could not be read: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    token = (out.stdout or "").strip()
    if not token:
        raise RuntimeError(
            f"{TOKEN_ENV} is not set and no keychain item {KEYCHAIN_SERVICE!r} "
            f"was found. Add one with:\n"
            f"    security add-generic-password -s {KEYCHAIN_SERVICE} "
            f"-a \"$USER\" -w"
        )
    return token


def _request(client, method: str, url: str, *, token: str, **kwargs) -> Any:
    """One HTTP call with backoff. A 401 raises and is never retried."""
    last: Exception | None = None
    headers = {"Authorization": f"Bearer {token}"}
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS_S)):
        if delay:
            print(f"  retrying in {delay:.0f}s (attempt {attempt + 1})", flush=True)
            time.sleep(delay)
        try:
            response = client.request(method, url, headers=headers, **kwargs)
        except Exception as exc:  # noqa: BLE001 - every transport error retries
            last = exc
            print(f"  {type(exc).__name__}: {exc}", flush=True)
            continue
        if response.status_code == 401:
            raise Unauthorized(
                f"{method} {url} returned 401. The token this worker presented "
                f"is not one the server accepts. Nothing was transcribed and "
                f"nothing was pushed; the queue is the server's, so the items "
                f"are still owed on the next run."
            )
        if response.status_code >= 500:
            last = RuntimeError(f"{response.status_code}: {response.text[:200]}")
            print(f"  HTTP {response.status_code}", flush=True)
            continue
        return response
    raise RuntimeError(f"{method} {url} failed after "
                       f"{len(RETRY_DELAYS_S) + 1} attempts: {last}")


def fetch_queue(client, base_url: str, *, token: str, limit: int,
                kinds: str, since: int) -> list[dict[str, Any]]:
    """What the server says is owed, newest first."""
    response = _request(
        client, "GET", f"{base_url}/api/transcripts/queue", token=token,
        params={"limit": limit, "kinds": kinds, "since": since},
        timeout=QUEUE_TIMEOUT_S)
    if response.status_code != 200:
        raise RuntimeError(f"queue returned {response.status_code}: "
                           f"{response.text[:300]}")
    body = response.json()
    items = list(body.get("items") or [])
    print(f"queue:    {len(items)} items served, "
          f"{body.get('queued')} queued, {body.get('gated')} below the "
          f"relevance gate, {body.get('already_transcribed')} already done")
    note = body.get("audio_note")
    if note:
        print(f"audio:    {note}")
    return items


def push(client, base_url: str, *, token: str, item_id: str,
         transcription, derivation: str, replace: bool) -> dict[str, Any]:
    """Send one finished transcription. The body is what ``asr`` holds."""
    payload = {
        "item_id": item_id,
        "derivation": derivation,
        "engine": transcription.engine,
        "model": transcription.model,
        "language": transcription.language,
        "audio_url": transcription.audio_url,
        "audio_sha256": transcription.audio_sha256,
        "audio_bytes": int(transcription.audio_bytes),
        "audio_seconds": (None if transcription.audio_seconds is None
                          else float(transcription.audio_seconds)),
        "covered_seconds": float(transcription.covered_seconds),
        "wall_seconds": float(transcription.wall_seconds),
        "created_utc": transcription.created_utc.isoformat(),
        "replace": replace,
        "segments": [
            {"seq": int(s.seq), "start_s": float(s.start_s),
             "end_s": float(s.end_s), "text": s.text}
            for s in transcription.segments
        ],
    }
    response = _request(client, "POST", f"{base_url}/api/transcripts",
                        token=token, json=payload, timeout=PUSH_TIMEOUT_S)
    body: dict[str, Any]
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is still reportable
        body = {"detail": response.text[:300]}
    body["status_code"] = response.status_code
    return body


def run_once(args, *, token: str) -> int:
    """One pass: poll, transcribe what needs the GPU, push. Returns an exit code."""
    import httpx

    from fpl_edge.ingest.content import asr
    from fpl_edge.ingest.content.transcribe_cmd import _asr_fetcher, kind_needs_asr

    status = asr.backend_status()
    print(status.render())
    if not status.ready:
        print("\nSTOPPED: this worker exists to run the local engine and the "
              "local engine is missing. Nothing was transcribed and nothing "
              "was pushed.")
        return 1

    started = time.monotonic()
    deadline = started + args.budget_s if args.budget_s else None
    base_url = args.base_url.rstrip("/")

    pushed = identical = conflicted = failed = skipped = 0
    audio_s = 0.0
    asr_wall = 0.0

    with httpx.Client(follow_redirects=False) as client:
        items = fetch_queue(client, base_url, token=token, limit=args.limit,
                            kinds=args.kinds, since=args.since)
        needed = [i for i in items if kind_needs_asr(i.get("kind", ""))]
        print(f"for this worker: {len(needed)} of {len(items)} items need the "
              f"GPU; the rest ride the caption route on the server")
        if args.dry_run:
            for item in needed:
                has_audio = "audio" if item.get("audio_url") else "NO-AUDIO"
                print(f"  {str(item.get('published_at'))[:10]}  "
                      f"{item.get('creator', '')[:22]:<22} {has_audio:<8} "
                      f"{item.get('title', '')[:50]}")
            print("\ndry run: nothing downloaded, nothing transcribed, "
                  "nothing pushed")
            return 0

        fetcher = _asr_fetcher(args.delay)
        try:
            for item in needed:
                if deadline is not None and time.monotonic() >= deadline:
                    print("  -- budget reached; the rest of the queue is "
                          "untouched and still owed")
                    break
                item_id = str(item["item_id"])
                url = item.get("audio_url") or ""
                if not url:
                    skipped += 1
                    print(f"  skip  {item_id} has no audio url in the queue "
                          f"payload", flush=True)
                    continue
                try:
                    got = asr.fetch_audio(fetcher, url)
                    if not got.ok:
                        skipped += 1
                        why = got.error or "audio unavailable"
                        print(f"  skip  {item_id} {why} ({got.status})",
                              flush=True)
                        continue
                    assert got.path is not None
                    result = asr.transcribe_file(
                        got.path, audio_url=url,
                        model=args.model or asr.DEFAULT_MODEL, status=status)
                except Exception as exc:  # noqa: BLE001 - one item, not the run
                    failed += 1
                    print(f"  FAIL  {item_id} {type(exc).__name__}: "
                          f"{str(exc)[:160]}", flush=True)
                    continue
                audio_s += result.audio_seconds or 0.0
                asr_wall += result.wall_seconds

                body = push(client, base_url, token=token, item_id=item_id,
                            transcription=result, derivation="asr",
                            replace=args.replace)
                code = body.get("status_code")
                if code == 200 and body.get("stored"):
                    pushed += 1
                    print(f"  ok    {item_id} {body.get('segments')} segments "
                          f"stored; {result.render()}", flush=True)
                elif code == 200:
                    identical += 1
                    print(f"  same  {item_id} {body.get('reason')}", flush=True)
                elif code == 409:
                    conflicted += 1
                    print(f"  409   {item_id} {body.get('reason')}", flush=True)
                else:
                    failed += 1
                    print(f"  push  {item_id} HTTP {code}: "
                          f"{str(body.get('detail'))[:200]}", flush=True)
        finally:
            fetcher.close()

    elapsed = time.monotonic() - started
    print()
    print(f"pushed:          {pushed} transcripts stored on the server")
    print(f"already stored:  {identical} identical, nothing changed")
    print(f"conflicts:       {conflicted} items hold a different transcript; "
          f"re-run with --replace to overwrite one deliberately")
    print(f"skipped:         {skipped} items with no usable audio")
    print(f"failed:          {failed} items; NOTHING was stored for these and "
          f"they are still queued")
    if asr_wall > 0:
        print(f"ASR rate:        {audio_s / 60:.1f} min of audio in "
              f"{asr_wall / 60:.1f} min of transcription = "
              f"{audio_s / asr_wall:.1f}x realtime")
    print(f"wall clock:      {elapsed / 60:.1f} min total")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    from fpl_edge.pipelines.registry import TRANSCRIBE_BUDGET_S

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", required=True,
                    help="the deployed service, e.g. https://fpl-edge.up.railway.app")
    ap.add_argument("--once", action="store_true",
                    help="one pass and exit. This is the default and the "
                         "launchd mode; --loop is the alternative.")
    ap.add_argument("--loop", action="store_true",
                    help="keep polling at --interval-s instead of exiting")
    ap.add_argument("--interval-s", type=float, default=3600.0,
                    help="seconds between polls under --loop")
    ap.add_argument("--limit", type=int, default=20,
                    help="how many queue items to ask for per poll")
    ap.add_argument("--kinds", default="podcast",
                    help="content_item.kind values to ask the queue for. The "
                         "default is podcast only: youtube rides the published "
                         "caption route, which the server runs itself.")
    ap.add_argument("--since", type=int, default=21,
                    help="only items published in the last N days (0 = all)")
    ap.add_argument("--budget-s", type=float, default=TRANSCRIBE_BUDGET_S,
                    help="wall-clock stop, checked between items")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="politeness delay between audio downloads, floor 2s")
    ap.add_argument("--model", default=None, help="MLX-Whisper weights id")
    ap.add_argument("--replace", action="store_true",
                    help="overwrite a stored transcript that differs. The swap "
                         "is recorded in transcript_provenance.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what the queue holds and stop")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        token = read_token()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not args.loop:
        try:
            return run_once(args, token=token)
        except Unauthorized as exc:
            print(str(exc), file=sys.stderr)
            return 3

    while True:
        try:
            run_once(args, token=token)
        except Unauthorized as exc:
            print(str(exc), file=sys.stderr)
            return 3
        except Exception as exc:  # noqa: BLE001 - a bad pass, not the worker
            print(f"pass failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"\nsleeping {args.interval_s:.0f}s until the next poll",
              flush=True)
        time.sleep(args.interval_s)


if __name__ == "__main__":
    raise SystemExit(main())
