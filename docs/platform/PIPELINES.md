# Data pipelines — the map, the facts, and the plan

Status: SPEC CLOSED 2026-08-31; SCOPE ELEVATED 2026-09-01 — the owner's
direction: "pipelines are the most important thing here... take this as the
main product, pipelines is the edge, organize it properly, best in class."
Concretely: a first-class `fpl_edge/pipelines/` package (registry, runner,
health) where every run is timed, logged (per-run log files with tails in the
ledger), and health-scored ({ok, failing, stale, running, never_ran,
disabled} with a reason, consecutive-failure counts, avg duration over the
last 20 ok runs); one `pipeline_status()` payload feeding a control-panel UI
with per-pipeline health, last run, average time, next due, and
trigger-with-confirm for metered tasks. Building in the §6 order with §6.4
promoted to flagship.

---

## 1 · What exists: 29 fetch surfaces, 6 groups

The full file:line inventory lives in the session record; this is the
operating map. **Bold** = scheduled today; *italics* = manual-only today.

### A · Core FPL loop
| Pipeline | Source | Schedule today | Writes |
|---|---|---|---|
| **bootstrap + fixtures** | fantasy.premierleague.com API | nightly post_gw + DAG T-30h | dim_event/team/player, fact_player_state, fact_fixture |
| **results settlement** | FPL live API | nightly post_gw (refuses un-finalised GWs) | fact_player_fixture |
| my-team (public/private) | FPL API (+OAuth for private) | manual / on-demand | data/myteam/*.json (not warehouse) |

### B · Odds (metered: 500 credits/month)
| Pipeline | Schedule today | Freshness gate |
|---|---|---|
| **Odds API featured** (h2h/totals/CS/scorer) | DAG ladder **T-36h / T-12h / T-5h** + nightly top-up | `MARKET_MAX_AGE_H` registry + skip-if-all-fresh; zero rows never counts as fresh |
| **Odds API extras** (CS-correct/BTTS/team totals) | T-36h rung only, ~42 credits/GW | own monthly cap 150, local spend ledger |
| **football-data forward fixtures** | post_gw + DAG T-30h | none (free) |
| *football-data history* | manual backfill | as_of = kickoff |

### C · Projections (6 providers, one CLI, nightly)
| Provider | Serves | Measured cadence (2026-27) |
|---|---|---|
| fplform | xp, p_appear, **xp_if_appears** | revises ~daily (values change most days) |
| fpl_ep | FPL's own ep_next | static for days at a time |
| gh_fplbench | xp + **xmins on every row** | repo pushes, roughly per-GW |
| gh_blueladd | xp + partial xmins | repo pushes |
| gh_apex_airsenal | xp | static 3-4 days between pushes |
| premierinjuries | **p_appear only** (never xp/xmins) | site updates |
| livefpl (same CLI) | EO predicted/top10k/elite | daily |

We pull all of them once daily (post_gw). **Measured: most pulls re-store
value-identical rows** — fplform is 60k rows over 13 pulls and it is the
*most* volatile provider; fpl_ep/airsenal re-store unchanged numbers for days.

### D · Content (creators)
| Pipeline | Schedule today | Gate |
|---|---|---|
| **RSS/blog/YouTube-page ingest** (22 podcasts, 3 blogs, 14 channels) | nightly post_gw (backfill 3d) + DAG T-30h (2d) | GUID-keyed content-addressed item_id; description-only until transcribed |
| *transcription* (MLX-Whisper ASR 11.5×; panel captions 286×) | **manual only** | queue skips done/skipped items; 80% coverage or nothing stored; audio cached on disk content-addressed, **never cleaned up** |
| *analysis* (Opus reads stored text → claims/insights) | manual only | per-item, resumable |
| paste-a-link | UI, preview-gated (park → accept/decline, 30-min TTL) | decline stores nothing |

### E · Cohort crawls (elite field)
**elite snowball** (budget 1100), **named elite** (identity-verified),
**top10k sampler** (grow +300/night) — all nightly post_gw, all budgeted with
per-endpoint HTTP TTL caches (finished-GW picks cached ~forever; transfers 3h).

### F · On-demand / manual
Understat player profile (UI click), Pulselive confirmed lineups (DAG T-90m),
intel (offline replay of archived bootstraps, nightly), *FPL-Core-Insights
per-match xG (manual — worth scheduling)*, *vaastav history (one-off)*.

### The scheduler that already exists
`deadline_dag` is launchd-ticked every 600s and is **already the Argus
scheduler transposed**: event-relative due times computed from `dim_event`
deadlines (T-30h presser, T-4h solve, T-90m lineups, the odds ladder),
per-task stale windows so a slept-through rung is dropped rather than
double-fired, an idempotent `dag_firing` ledger claimed before running, and
honest outcome rows (`delivered/quiet/skipped_stale/no_source/error`).
post_gw is the calendar-daily settlement chain. The design question is not
"build a scheduler" — it is "promote the one we have to own everything".

---

## 2 · The owner's questions, answered with facts

**xPoints — how often do providers publish?** Measured above (§1C). Daily
pulls are right for fplform; wasteful-but-harmless for the static ones. The
real waste is *storage semantics*, not fetch count — see change detection.

**xMins — do we fetch it?** Yes: `fact_projection.xmins` from gh_fplbench
(every row) and gh_blueladd (partial); `xp_if_appears` from fplform;
`p_appear` from fplform + premierinjuries; rotowire predicted XI kept
separate as the journalist-proxy (`fact_predicted_lineup`). The migration
that added the column forbids squashing p_appear↔xmins into each other —
deriving one from the other is a minutes model, which we refuse to fake.

**Transcription "on disk"?** Two-tier: panel-creator YouTube captions at
~286× realtime (cheap), MLX-Whisper on-GPU ASR at ~11.5× for podcast audio.
Audio downloads are content-addressed to `data/raw/content/asr_audio/` so a
re-run never re-downloads — that is the "disk" part, and it is correct.
Two real problems: **transcription is manual-only** (nothing schedules it),
and **the audio cache has no cleanup** (grows forever; episodes are
20-400MB).

**Injuries — source, store, surface?** Three paths, deliberately ranked:
1. **FPL API bootstrap** → `fact_player_state` (status, chance_of_playing,
   news, `news_added`). Chosen as PRIMARY because `news_added` is an honest
   published-at — exactly what a PIT store needs. 119k rows.
2. `intel_item` — the same facts replayed as dated AVAILABILITY items
   (content-hashed, so 300 polls of one injury = one row).
3. premierinjuries.com → `fact_projection.p_appear`.
Surfaced today: fixtures drawer team news, DAG T-30h digest, player_intel
tool. Gap: no single "availability" panel that merges the three with
provenance.

**RSS first-pass gate for creators?** The structure already exists: RSS
ingest is cheap and stores description-only items; transcription is the
expensive step and is separate. What is missing is (a) a faster RSS cadence
for the top creators than nightly, and (b) a *relevance gate* between
description and transcription so GPU/LLM spend follows worth.

**Manual link — don't fetch if not relevant?** Built: the paste-a-link job
parks at a preview (publisher, title, GW, description, duration, ETA) and
transcribes only on explicit accept; decline/expiry stores nothing.

**Duplicates?** The content store already survived this war: item_id =
sha256(source|GUID) after link-keying collapsed 378 episodes into one row;
claims are content-addressed; intel hashes content so re-observation is a
no-op; every warehouse append is a PIT anti-join that refuses contradictions.
The dedup religion is sound. The one soft spot is *value-identical re-writes
under new as_of* (next section).

---

## 3 · Defects found during inventory (fix regardless of design)

1. ~~`make deploy` copies the deleted telegram plist~~ — fixed (fe8ad79).
2. **`fpl_mcp` is a second, worse fetch surface**: bare `requests`, no
   archive, no budget, overwrites plain-JSON caches inside the hash-named
   archive dir, and — worst — `video_transcript.py` uses the youtubei route
   `fpl_edge` explicitly refuses as robots-disallowed, with no robots check
   and silent-empty failures. Must be unified onto the fpl_edge fetchers.
3. **Audio cache unbounded** — needs a post-transcription retention rule.
4. **paste-a-link writes `transcript_segment` without a
   `transcript_provenance` row** — the one transcript path with no receipt.
5. Stale prose: T-3h appears in two comments; the ladder is T-5h.
6. Manual-only pipelines that should be scheduled: transcription,
   FPL-Core-Insights (per-match xG for the fixtures form window!).

---

## 4 · Proposed architecture (for discussion)

### 4.1 One scheduler, one registry
Promote `deadline_dag` to THE scheduler. Every pipeline becomes one row in an
explicit registry (Argus rule: adding authority = one reviewable line):

```python
Task(id="ingest_projections",
     due=Daily(hour_utc=10) | DeadlineRelative(hours=-30) | OnDemand,
     stale_window=..., budget=..., freshness=skip_if_fresh_fn,
     run=..., enabled=True)
```

post_gw's steps fold in as calendar tasks; the ladder tasks stay
event-relative; today's manual-only pipelines get schedules (transcription
nightly with a budget; fpl_core_insights daily post-kickoff). One launchd
tick drives everything; launchd's only job is "wake the scheduler".

### 4.2 The fetch ledger + change detection (the "already latest" mechanism)
New table `fetch_run(pipeline, source, started, finished, status,
rows_written, rows_unchanged, http_status, credits_spent, note)` written by
every pipeline through one helper. Then the append path gains
**write-if-changed**: compare incoming rows to each entity's latest stored
values; write only rows whose payload differs; count the rest as
`rows_unchanged` in the ledger.

What this buys, in the owner's words:
- "don't rerun the whole thing if already latest" — the freshness gate reads
  the ledger: last successful run + rows_unchanged says *confirmed current*.
- "should not restore the same thing" — value-identical pulls stop writing
  fact rows at all.
- The PIT ambiguity dies: today "no new as_of" cannot distinguish *not
  refetched* from *refetched, unchanged*. Ledger + unchanged-count makes
  "confirmed unchanged at T" a first-class fact without bloating fact tables.
- MCP stability: fact-table schemas stop churning under storage-size
  pressure, because the pressure is gone.

### 4.3 UI: a Pipelines panel + safe triggering
One panel listing every registered task: last run, outcome, rows
written/unchanged, next due, freshness state (unifying today's four disjoint
staleness registries into one module the panel, the DAG gates, and the
drawers all read). Trigger buttons POST `/api/pipelines/{id}/run` (the
fetch_profile route is the precedent) — with metered pipelines either
excluded or confirm-gated (open question).

### 4.4 Content cadence + the relevance gate
- Top-creator RSS polled on its own faster schedule (open question: cadence);
  full-roster nightly as today. RSS polls are conditional-fetch cheap.
- New scheduled `transcribe` task with a nightly wall-clock budget:
  captions-first for panel creators (286×), ASR queue for podcasts ordered
  by a **relevance gate** — score the stored description (players named ×
  resolver hits, gameweek terms, panel status, recency) and transcribe above
  threshold; below-threshold items stay description-only with the score
  recorded (a named reason, not silence). No LLM in the gate at first: the
  scorer is deterministic and auditable; an LLM pass is a v2 refinement.
- Audio retention: delete audio after a stored transcript+provenance (the
  sha stays in provenance, so integrity survives the file).

### 4.5 What deliberately does NOT change
`as_of` PIT discipline, append-only contradiction refusal, content-addressed
ids, robots discipline (extended TO fpl_mcp, never relaxed), credit budgets
with refusal-before-spend, honest outcome rows.

---

## 5 · Decisions (closed with owner, 2026-08-31)

1. **One scheduler.** deadline_dag becomes THE scheduler with an explicit
   task registry — calendar tasks (post_gw's steps fold in), deadline-relative
   tasks (the ladders stay), on-demand tasks. The postgw plist retires once
   parity is proven; launchd's only job is waking the tick.
2. **Fetch ledger + write-on-change.** `fetch_run` records every pull;
   appends write only changed rows; "refetched, unchanged" becomes a
   first-class fact and the skip gate reads the ledger.
3. **4h top-creator RSS + nightly budgeted ASR.** Panel captions
   auto-transcribe on arrival; podcast ASR under a nightly wall-clock budget,
   queue ordered by a deterministic relevance score on the description;
   below-threshold stays description-only with the score recorded.
4. **All pipelines UI-triggerable; metered ones confirm-gated** with credit
   cost and month-to-date spend shown before the click.

## 6 · Build order (each step shippable)

1. **fetch_run ledger + write-on-change** in the three append paths
   (Warehouse.append, ProjectionStore, UnderstatStore) behind one helper.
   The PIT contract note: an entity's absent new as_of now means "no change
   observed OR not fetched", and the ledger is the disambiguator — every
   consumer that cares reads fetch_run. Tests must pin: unchanged rows not
   written but counted; changed rows written; the skip gate honours
   ledger+freshness; contradiction refusal unchanged.
2. **Scheduler registry.** Task dataclass + registry module; post_gw steps
   become calendar tasks executed by the DAG tick with the same firing
   ledger; parity run (both paths side by side, outcomes compared) before
   the postgw plist is retired.
3. **Schedule the missing**: transcribe (nightly budget, captions-first,
   relevance gate), fpl_core_insights (daily post-kickoff), top-creator RSS
   at 4h (a `content_tier` on sources), audio retention sweep (delete after
   stored transcript+provenance; sha survives in provenance).
4. **Pipelines panel + trigger routes** + the unified freshness module
   replacing the four disjoint registries; metered confirm flow shows
   credits and month spend.
5. **fpl_mcp fetch unification**: kill the youtubei route and bare requests;
   route through fpl_edge fetchers/archive.
6. **Small repairs**: paste-a-link writes transcript_provenance; T-3h prose
   corrected to T-5h.
