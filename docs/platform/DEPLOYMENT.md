# Deploying to Railway

Written for the engineer who implements this next. Everything here was read
out of the code on 2026-09-17 or measured on the owner's machine on the same
day. Where a number is a guess it says so, and where a claim could not be
checked against the repo it is listed in §12.

Three decisions in this workstream are the owner's to make. Each one is
stated below with a recommendation and with what the alternatives cost.
Nothing is picked silently.

---

## 0 · The measurements this spec is built on

Taken on the owner's Mac, `data/warehouse/` and `data/raw/`, 2026-09-17.

| Path | Size | Durable | Note |
|---|---|---|---|
| `data/warehouse/` (total) | 177 MB | yes | everything the server reads |
| `data/warehouse/fpl.duckdb` | 168,046,592 bytes (160.3 MiB) | yes | the single file, single writer |
| `data/warehouse/chat/` | 9.7 MB | yes | 24 conversation dirs plus chart assets |
| `data/warehouse/jobs/` | 6.8 MB | no | 1,469 DAG tick reports, gitignored |
| `data/warehouse/pipeline_logs/` | 496 KB | no | 114 run logs, gitignored |
| `data/warehouse/backtests/` | 12 KB | yes | committed |
| committed artefacts under `data/` | 1,211,977 bytes across 18 files | yes | 930 KB of that is `jobs/telegram.log`, which should not be tracked |
| `web/dist/` | 1.3 MB | no | zero-build UI, ships in the image |
| `data/raw/` (total) | 15 GB | see §3 | the provenance archive |
| `data/raw/content/` | 14 GB | see §3 | of which `ingest/` 10 GB, `asr_audio/` 2.9 GB (62 files) |
| `data/raw/fpl_api/` | 167 MB | see §3 | one month of daily snapshots |
| `data/raw/rivals/` | 232 MB | see §3 | cohort crawls |

**The brief describes the warehouse as "5GB-class". It is not.** The measured
file is 160 MiB. The 15 GB figure belongs to `data/raw/`, which is the raw
provenance archive and is a separate question from the warehouse. Fork 3 is
therefore much easier than the brief assumed, and the seeding path in §3.4 is
a one-time 177 MB upload rather than a rebuild.

Growth, for sizing only: the warehouse reached 160 MiB in the 26 days from
2026-08-18 to 2026-09-12, so roughly 6 MB per day of season. A full season at
that rate lands near 1.6 GB. This is an extrapolation from one month, not a
measurement of a season.

---

## 1 · Fork 1: where transcription runs

`fpl_edge/ingest/content/asr.py` runs `mlx-whisper` on the Apple Metal GPU.
`pyproject.toml` guards it with `sys_platform == 'darwin'`, so the dependency
is not even installed on Linux, and `asr.backend_status()` reports the engine
missing. Railway runs Linux containers with no Metal. Transcription cannot
run there as written.

### 1.1 Recommendation: (a), the Mac stays the transcription worker

The Mac transcribes locally and pushes finished transcripts to the Railway
warehouse over an authenticated endpoint. Railway never fetches audio.

Reasons, in order of weight:

1. It costs nothing. Transcription is the one part of the content pipeline
   that spends no metered credits and no model tokens today, and this is the
   only option that keeps it that way.
2. It matches how the corpus was actually produced. Every existing transcript
   in the warehouse came from this engine at these settings, so pushed
   transcripts stay comparable with stored ones. `transcript_provenance`
   records `engine` and `model` precisely so a re-run is comparable, and
   switching engines breaks that comparison silently.
3. The Mac already holds 2.9 GB of cached audio in `data/raw/content/asr_audio`
   plus the 10 GB ingest archive. Re-downloading it into a Railway container
   is a cost with no benefit.

### 1.2 What the alternatives cost

| Option | What it buys | What it costs |
|---|---|---|
| (a) Mac worker, recommended | zero marginal cost, identical engine and weights, reuses the existing audio cache | transcription stops when the Mac is off or asleep, and the queue backs up until it wakes. Adds one authenticated write endpoint and one script to operate. |
| (b) `faster-whisper` on CPU in the container | no second machine, no push endpoint, everything in one place | Railway compute is billed per second. Whisper large-v3 on CPU runs at roughly 1x realtime against the 11.7x to 12.3x measured for MLX on this Mac (registry.py records the measurement: 107.5 min of audio in 549s). The nightly budget is 3600s, so a nightly run that does 12 hours of audio on the Mac would do about one hour on CPU and would bill for the full hour every night. It also changes the engine, so every new transcript is incomparable with the 62-episode back catalogue. |
| (c) Hosted ASR API | no GPU to own, no worker to operate, transcription keeps running when the Mac sleeps | a per-minute bill in real money, a third-party dependency in the corpus path, and a second set of terms to check against `docs/data_sources.md`. It spends no model tokens, which is the owner's stated constraint, so it is not disqualified. It is rejected only because (a) is free and already works. |

### 1.3 The seam: what runs where

The split is not "transcription moves to the Mac". It is narrower than that,
and the narrower version reuses more code.

| Step | Runs on | Why |
|---|---|---|
| Content ingest (RSS, feeds, item rows) | Railway | pure HTTP, no darwin dependency |
| Queue selection and the relevance gate | Railway | `pipeline.cmd_transcribe` scores against `content_item` and `dim_player`, both of which live in the warehouse. Keeping the gate here also keeps `content_transcribe_skip` on the authority side. |
| Published-caption transcription | Railway | `asr.transcription_from_captions` reads `youtube-transcript-api` output. No Metal, no audio download. |
| Audio download and ASR | Mac | needs Metal |
| Writing segments, provenance and text promotion | Railway | `asr.store_transcription` is the one sanctioned write path and must stay on the single writer |

Two consequences of this split that B2 must handle as code changes:

1. **`cmd_transcribe` checks the ASR backend before it knows what is in the
   queue.** `fpl_edge/ingest/content/pipeline.py:974` runs
   `asr.backend_status()` and returns 1 if the engine is missing. On Linux
   that refuses a caption-only run that needs no engine at all. This breaks
   `content_fast_rss`, whose second step is
   `pipeline transcribe --kinds youtube --since 2 --budget-s 300`
   (`registry.py:668`). On Railway that step exits non-zero every four hours
   and the task goes to `error`. The gate must move so it is evaluated when
   an item actually needs ASR, not at the top of the command.
2. **`content_transcribe` must report `no_source`, not `error`, on a host
   with no engine.** As written, `run_transcribe_nightly` turns the non-zero
   exit into `outcome="error"` with `kind="alert"`, so Railway would fire a
   "Nightly transcription FAILED" alert every night forever for a condition
   that is correct and permanent. `no_source` is the vocabulary the runner
   already has for an honest gap (`runner.LEDGER_STATUS`), and its reason
   line should say that ASR is performed by the Mac worker.

`POST /api/ingest/link` needs no change. `link_jobs.py:241` refuses a pasted
media file and the pasted-link route transcribes published captions only, so
that path works on Linux as it stands.

### 1.4 `GET /api/transcripts/queue`

The Mac needs to know what to transcribe, and the queue lives on Railway.

Request: `GET /api/transcripts/queue?limit=20`, bearer auth as in §1.7.

The server runs the existing queue query from `cmd_transcribe`
(`pipeline.py:1003` onward): items whose `text_source <> 'transcript'`, of the
requested kinds, with no row in `transcript_segment` and none in
`content_transcribe_skip`, newest `published_at` first, then the relevance
gate at `RELEVANCE_THRESHOLD`. Below-threshold items are recorded in
`content_transcribe_skip` exactly as they are today. The response carries
only items that passed.

Response, one object per item:

| Field | Source | Why the Mac needs it |
|---|---|---|
| `item_id` | `content_item.item_id` | the key it pushes back under |
| `creator` | `content_item.creator` | log lines and per-creator limits |
| `title` | `content_item.title` | log lines |
| `kind` | `content_item.kind` | podcast means ASR, video means the server already handled captions |
| `published_at` | `content_item.published_at` | ordering and operator sanity |
| `audio_url` | `content_item_asset.enclosure_url`, falling back to the feed re-parse the server already does | what `asr.fetch_audio` downloads |

The queue endpoint is read-only apart from the skip rows the gate writes,
which it already writes today.

### 1.5 `POST /api/transcripts`

The body is exactly what `asr.store_transcription` needs to reconstruct an
`asr.Transcription` and write it. Nothing is invented; every field below maps
to a field on `asr.Transcription` (`asr.py:394`), to `asr.Segment`
(`asr.py:384`), or to a column of `transcript_provenance` (`asr.py:588`).

| Field | Type | Required | Maps to |
|---|---|---|---|
| `item_id` | string, 24 hex chars | yes | `store_transcription(item_id=...)`; formed as `sha256("{source_key}|{url}")[:24]` by `ContentItem.make_id` |
| `derivation` | `"asr"` or `"captions"` | yes | `transcript_provenance.derivation` |
| `engine` | string | yes | `Transcription.engine`, e.g. `mlx-whisper` |
| `model` | string | yes | `Transcription.model`, e.g. `mlx-community/whisper-large-v3-turbo` |
| `language` | string or null | yes | `Transcription.language` |
| `audio_url` | string | yes | `Transcription.audio_url` |
| `audio_sha256` | string, may be empty | yes | `Transcription.audio_sha256`. Empty on the captions path by construction (`asr.py:562`), so it cannot be the idempotency key. |
| `audio_bytes` | integer | yes | `Transcription.audio_bytes` |
| `audio_seconds` | float or null | yes | `Transcription.audio_seconds`. Null means the audio was never downloaded, which is the only honest answer on the captions path. Do not fill it from the last cue. |
| `covered_seconds` | float | yes | `Transcription.covered_seconds`, the completeness proof |
| `wall_seconds` | float | yes | `Transcription.wall_seconds` |
| `created_utc` | ISO 8601 with offset | yes | `Transcription.created_utc` |
| `segments` | array | yes | `Transcription.segments` |
| `segments[].seq` | integer | yes | `Segment.seq` |
| `segments[].start_s` | float | yes | `Segment.start_s` |
| `segments[].end_s` | float | yes | `Segment.end_s`. Not stored in `transcript_segment`, which is `(item_id, seq, start_s, text)`, but `Segment` requires it and coverage arithmetic reads it. |
| `segments[].text` | string | yes | `Segment.text` |
| `replace` | boolean, default false | no | see §1.6 |

The handler reconstructs the dataclasses, then calls
`asr.store_transcription(wh, item_id, transcription, derivation=...)` inside
one short write lease and `asr.stale_analyses(wh, item_id)` inside a second
one. The two leases stay separate for the reason `pipeline.py:1250` gives:
sharing a transaction meant a failure in the analysis cleanup rolled back the
transcript, and that is what the 2026-09-01 and 2026-09-02 nightly failures
were.

Refusals:

| Condition | Status | Body |
|---|---|---|
| Missing or wrong bearer token | 401 | names the header and nothing else |
| `item_id` not in `content_item` | 404 | an orphan transcript has nothing to attach to |
| Zero segments, or joined text is blank | 400 | `store_transcription` already raises `PartialTranscript` for both |
| A different transcript is already stored and `replace` is false | 409 | see §1.6 |
| Body over the size cap | 413 | a 2.5-hour episode is roughly 1 MB of JSON; cap at 16 MB |

### 1.6 Idempotency

**The key is `(item_id, sha256(joined segment text))`.**

`content_item.text_sha256` already stores exactly that hash once a transcript
is promoted (`asr.py:705`). So the handler computes
`sha256(transcription.text)` and compares it against the stored row:

- `text_source = 'transcript'` and the hashes match: no-op. Return 200 with
  `{"stored": false, "reason": "identical transcript already stored"}`. A
  retry after a dropped connection, a duplicate worker run, or a worker that
  lost its local bookkeeping all land here and change nothing.
- `text_source = 'transcript'` and the hashes differ, `replace` false: 409.
  The stored transcript cost minutes of GPU and the push may be a regression,
  so overwriting it is an explicit act.
- `text_source = 'transcript'` and the hashes differ, `replace` true:
  `store_transcription` deletes and rewrites the segments, and
  `store_provenance` records `prior_text_source` and `prior_text_sha256`, so
  the swap is auditable.
- `text_source <> 'transcript'`: the normal case. Store it.

`audio_sha256` is deliberately not the key. It is empty on the captions path
(`asr.py:562`), so a key built on it would treat every caption push as
identical to every other.

### 1.7 Authentication and rotation

A shared bearer secret. This is a machine-to-machine call between two systems
the owner controls, and the endpoint writes to exactly one table family, so a
bearer token is proportionate. It is not a substitute for the auth layer
workstream D and E add.

- **Header.** `Authorization: Bearer <token>`.
- **Comparison.** `hmac.compare_digest`, never `==`.
- **Token.** 32 random bytes, base64url, generated by the owner.
- **On Railway.** A service variable named `TRANSCRIPT_PUSH_TOKEN`, marked
  secret. Read at the point of use through `fpl_edge.config.secret`, which
  already reads the environment first and never logs values.
- **On the Mac.** The macOS keychain, not `.env`. `.env` sits in the repo
  tree, is read by `config.load_env()`, and is therefore visible to anything
  with read access to the working directory. Store it with
  `security add-generic-password -s fpl-edge-transcript-push -a "$USER" -w`
  and have the worker's launchd wrapper export it into the environment at
  start with `security find-generic-password -s fpl-edge-transcript-push -w`.
  The worker then reads it through `config.secret` like every other secret.
- **Rotation.** The server accepts two values: `TRANSCRIPT_PUSH_TOKEN` and,
  when set, `TRANSCRIPT_PUSH_TOKEN_NEXT`. To rotate: set `_NEXT` on Railway,
  update the Mac keychain to the new value, run one push and confirm 200,
  then move the new value into `TRANSCRIPT_PUSH_TOKEN` and clear `_NEXT`.
  Two accepted values during the window means rotation never needs the two
  sides to restart together. Rotate quarterly, and immediately on any
  suspicion.
- **When the token is wrong.** 401 with `{"error": "unauthorized"}` and
  nothing else. It must not say whether the `item_id` exists, because that
  would make the endpoint an existence oracle for an unauthenticated caller.
  The server logs the attempt with the client IP and the first four
  characters of the presented token, never the whole token. The Mac worker
  treats 401 as fatal, stops the run rather than retrying, and exits
  non-zero so launchd records it. Nothing is lost: the queue is Railway's,
  and an item that was not pushed is simply still in the queue on the next
  run.
- **Rate limit.** 60 pushes per minute per token. A nightly run pushes at
  most a few dozen.

One documentation change rides with this endpoint. The module docstring of
`fpl_edge/platform/app.py` states that "Exactly one route writes to the
CORPUS". This makes it two. The docstring must be corrected in the same diff
that adds the route, or it becomes a false statement in the file that exists
to be the map.

### 1.8 What the Mac worker is

**Recommendation: a separate script, `scripts/mac_transcribe_worker.py`, not
an extension of the `content_transcribe` task.**

Why:

- `content_transcribe` is a registry `Task`. It writes through
  `_write_with_retry(args.db, ...)` against a local DuckDB file, it claims a
  `dag_firing` row, and it lands a `fetch_run` ledger row. On the Mac, after
  the move, none of those rows are read by anything: the authority is the
  Railway warehouse. Keeping the task means keeping a second warehouse on the
  Mac whose only purpose is to hold bookkeeping nobody looks at, which is the
  single-writer problem duplicated for no gain.
- The queue the task reads is a local SQL query. The worker's queue is an
  HTTP call. Putting both inside one function means a branch on "am I local
  or remote" in the middle of the queue logic, which is the kind of fork that
  drifts.
- The parts worth reusing are pure and are reused directly by import:
  `asr.fetch_audio`, `asr.transcribe_file`, `asr.Transcription`,
  `asr.Segment`, and `registry.TRANSCRIBE_BUDGET_S`. The script writes no new
  transcription code.

The script's loop: read the token, `GET /api/transcripts/queue`, and for each
item download the audio with `asr.fetch_audio`, transcribe with
`asr.transcribe_file`, then `POST /api/transcripts`. It respects
`TRANSCRIBE_BUDGET_S` between items exactly as the task does, checks the
budget between items and never inside one, and stops on the first 401. It
runs under launchd on the Mac on the same nightly cadence the task has today
(12:00 UTC).

Cost of the alternative, extending the task: one code path instead of two,
and the budget and grace constants stay in the registry where a reviewer sees
them. That is a real benefit. It is outweighed by dragging a DuckDB writer, a
firing ledger and a stale-window machinery onto a host that is no longer the
system of record.

---

## 2 · Fork 2: one DuckDB file, one writer, one volume

### 2.1 Recommendation: (a), one service

One Railway service owns the volume. The FastAPI app and the scheduler run in
one process, with the scheduler as a background asyncio task. `launchd` is
replaced, not ported.

**This caps the deployment at one instance, and that cap is not a temporary
inconvenience.** DuckDB permits one writer per file. `Warehouse._connect`
(`warehouse.py:388`) waits for a conflicting writer and then raises
`WarehouseLockedError` naming the timeout. A second replica would fail to
open the database at boot and would restart-loop. `replicas = 1` must be set
explicitly in `railway.toml`, not left to a default.

### 2.2 What the alternative costs

Option (b), two services with the API and a worker split, is not viable as
the repo stands. A Railway volume attaches to exactly one service, so the
worker either cannot see the database at all, or, if it could, it would be a
second writer process against one file. The repo has no cross-process lock
protocol for that. It has the opposite: a single-writer assumption stated in
the `warehouse.py` module docstring and relied on by every append path. The
cost of (b) is therefore not "slightly more complexity", it is inventing and
proving a distributed lock, which is a workstream of its own.

### 2.3 Railway Cron, evaluated

Railway Cron runs a command on a schedule in its own container. Evaluated
against the in-process loop on the three axes that matter:

| Axis | Railway Cron | In-process asyncio loop |
|---|---|---|
| Single writer | A cron container is a second process. To do any work it needs the volume, and a volume attaches to one service. So either it cannot reach the database, or it is option (b) with the same missing lock protocol. This is decisive on its own. | The scheduler and the API are the same process, so there is one writer by construction. |
| Cold start | Each firing pays a container start plus Python import plus a DuckDB open plus migrations. The tick cadence today is 600s, so a cron at that cadence pays the start cost 144 times a day. The actual cost was not measured; see §12. | Paid once at boot. |
| Observability | A cron run's output goes to Railway's platform logs. `fetch_run`, `dag_firing` and `data/warehouse/pipeline_logs/` are all warehouse-or-volume writes, which a cron container cannot make. The Pipelines panel would show nothing for exactly the tasks that moved to cron, and under rule 9 it would have to show that absence with a reason. | Every firing goes through `pipelines/runner.py:execute`, which is the one execution path: ledger row, timings, captured log file, and the note tail on a non-ok run. The Pipelines panel keeps working unchanged. |

**Recommendation: reject Railway Cron.** Not "defer it". The single-writer
argument makes it structurally identical to option (b), and the observability
argument means adopting it would blind the panel the owner just had built.

### 2.4 The in-process scheduler

`fpl_edge/platform/scheduler.py`, new, owned by B2.

- One asyncio task started at boot. It calls `deadline_dag.tick(db_path=...)`
  in a thread executor, because `tick` is blocking and takes the write lock
  in bursts.
- Cadence 600s, the same as the launchd plist, so stale windows keep the
  meaning they were tuned for. Several tasks carry a 23-hour
  `stale_window` specifically because the launchd tick did not fire while the
  Mac slept (`registry.py:755`). On Railway the service does not sleep, so
  those windows become generous rather than necessary. Leave them alone in
  this workstream; retuning them is a decision with its own evidence.
- **The boot catch-up needs no new mechanism.** `tick` already reads
  `LOOKBACK = 36h` of owed firings and evaluates `stale` per task against
  that task's own `stale_window` (`registry.registry_due`). A restarted
  service's first tick is the catch-up: it claims and runs what is owed and
  inside its window, and records what is owed but outside it as
  `skipped_stale` with the age in the detail. The only requirement on B2 is
  that the first tick runs immediately at boot rather than after the first
  600s sleep.
- **Single flight.** A tick that is still running when the next is due is
  skipped, with the skip recorded rather than silent. The settlement chain is
  17 steps and can run long.
- **One writer inside the process, too.** This is a new concurrency case that
  launchd never had. Under launchd the tick was a separate process and the
  DuckDB file lock kept it apart from the server. In one process, the
  scheduler and `POST /api/pipelines/{task_id}/run` (which calls
  `runner.run_task`, which opens a writer) can both want to write. DuckDB
  will not deadlock on this, but it will raise transaction conflicts, which
  is what `pipeline._is_contention` and `_write_with_retry` already exist to
  survive. B2 must serialise every writer in the process behind one lock
  owned by the scheduler module, and the UI trigger route must take that lock
  rather than racing for it.
- **Checkpoint discipline stays.** `_write_with_retry` ends each lease with
  `CHECKPOINT` because DuckDB replays the WAL to rebuild primary-key ART
  indexes on every write open, and on 2026-09-03 that replay started
  returning `Corrupted unique ART index` fatally to every writer until the
  WAL was quarantined. A container that is killed mid-write leaves a WAL. Do
  not remove the checkpoint, and see §7 step 3 for what boot does about a WAL
  it finds.

### 2.5 The deploy that breaks the single writer

A rolling deploy that starts the new container before stopping the old one
puts two processes on one volume. The new one fails to open the database and
restart-loops while the old one holds the lock. The deploy must stop the old
container first. The exact Railway setting name is in §12 as unverified;
whatever it is called, the overlap must be zero and `replicas` must be 1.

---

## 3 · Fork 3: what lives in the volume

### 3.1 The mount point

**Mount one volume at `/app/data`.**

Every path constant in the repo is relative to the working directory:
`DEFAULT_DB = Path("data/warehouse/fpl.duckdb")` (`warehouse.py:33`),
`LOG_DIR = Path("data/warehouse/pipeline_logs")` (`runner.py:46`),
`LOG_DIR = Path("data/warehouse/jobs")` (`deadline_dag.py:74`),
`AUDIO_CACHE = Path("data/raw/content/asr_audio")` (`asr.py:76`). Artefacts
are resolved as `source_dir(wh) / NAME`, which is the directory of the real
warehouse file (`platform/scripts/common.py:39`). With `WORKDIR /app` and the
volume at `/app/data`, all of that resolves onto the volume with no code
change.

The complication is that four paths under `data/` are tracked in git and
would be hidden by the mount: `data/panels/creator_panel_2026_27.yaml` and
the committed artefacts in `data/warehouse/`. The fix is a seed directory,
§3.4.

### 3.2 Volume layout

| Path | What it is | Size today | Durable | Rebuilt on boot |
|---|---|---|---|---|
| `/app/data/warehouse/fpl.duckdb` | the warehouse, single writer | 160.3 MiB | yes, this is the thing the volume exists for | no |
| `/app/data/warehouse/fpl.duckdb.wal` | write-ahead log | 0 when clean | transient | folded by the boot checkpoint |
| `/app/data/warehouse/*.parquet`, `*.json`, `*.html` | the artefacts panels read beside the database | 218 KB across 11 files | yes | no, but regenerable by their owning tasks (see §3.3) |
| `/app/data/warehouse/chat/` | agent conversations and chart assets | 9.7 MB | yes | no |
| `/app/data/warehouse/pipeline_logs/` | one log per run, named by `run_id` | 496 KB | no, but keep | swept, see §3.5 |
| `/app/data/warehouse/jobs/` | one JSON report per DAG tick | 6.8 MB | no, but keep | swept, see §3.5 |
| `/app/data/panels/` | the curated creator panel YAML | 56 KB | yes | seeded from the image if absent |
| `/app/data/raw/` | the provenance archive | grows | yes, with retention | no |
| `/app/data/tmp/` | `TMPDIR` for `Warehouse.read_copy` | transient | no | emptied at boot |

**Size the volume at 10 GB.** The reasoning: 177 MB today, extrapolated to
roughly 1.6 GB of warehouse over a season, plus `data/raw/` growth from
whatever Railway itself fetches, plus headroom for concurrent read copies
(§3.6). Alert at 70%. This is a sizing judgement from one month of growth,
not a measurement of a season.

`data/raw/content/asr_audio/` is 2.9 GB on the Mac and must never appear on
the volume. After fork 1, Railway downloads no audio.
`data/raw/content/ingest/` is 10 GB on the Mac from historical backfill and
is likewise not seeded.

### 3.3 What is rebuilt, what is fetched, what is absent

| Thing | Policy | Why |
|---|---|---|
| `fixture_ratings.parquet`, `fixture_calibration.parquet` | rebuilt daily by `fixture_ratings_refit` at 11:00 UTC; a 2.5s fit | cheap enough that its absence is a one-day wait, not an outage |
| `forecast.parquet`, `forecast.meta.json` | rebuilt daily by `forecast_refresh` at 11:30 UTC | same |
| `briefing_intel.json` | rebuilt daily by `briefing_intel` at 07:40 Europe/London | costs model tokens, so it is not rebuilt on boot |
| `transfer_plan.json` | written only by `fpl recommend`, never by a scheduled task (`registry.py:547`) | it is the owner's solve output, so it must be seeded and then preserved |
| `elite_list.json`, `gw1_projection.parquet`, `gw1_plan.json`, `retro_report.html`, `backtests/*.json` | seeded once, then whatever writes them writes them | historical artefacts with no daily refresh |
| `fixture_difficulty.parquet` | written by the settlement chain's `fixture_difficulty` step | the brief records this as a deprecated blended artefact that the UI no longer reads. Seed it, do not build anything new on it. |
| `data/raw/` archive | fetched on demand by whatever ingest runs; never seeded from the Mac | 15 GB of history with no consumer on Railway |
| `data/warehouse/jobs/telegram.log` | not seeded, not tracked | 930 KB of the 1.2 MB of tracked data is this one log file, which should be untracked in a separate diff |

Nothing here is rebuilt during boot. Boot checks presence and reports it
(§7 step 5), and the scheduler's first tick rebuilds what its tasks own. A
boot that blocked on a model fit would turn every restart into a several
minute outage.

### 3.4 Seeding the first deploy

The `.duckdb` is never in the image. `.gitignore` excludes
`data/warehouse/*.duckdb` and `*.duckdb.wal`, so a `COPY` of the repo cannot
pick it up, and the build asserts that (§10).

**Recommended path: a one-time authenticated upload, then the seed directory
for the rest.**

1. The image contains `/app/seed/data/`, a copy of the git-tracked files
   under `data/` (1.2 MB, 18 files, minus `jobs/telegram.log`). It is
   read-only.
2. At boot, for every file under `/app/seed/data/`, if the corresponding path
   under `/app/data/` does not exist, copy it. Never overwrite. So a first
   boot lands the committed artefacts and the panel YAML, and every later
   boot leaves the live ones alone.
3. The database itself is uploaded once by the owner. The Mac runs
   `CHECKPOINT` to fold the WAL, gzips the file (160 MiB raw; DuckDB
   compresses, so expect roughly 60 to 100 MB on the wire, unverified), and
   pushes it to a one-time `POST /api/admin/seed` that accepts a database
   file only when `/app/data/warehouse/fpl.duckdb` does not already exist.
   The endpoint takes the same bearer secret as the transcript push, refuses
   with 409 if a database is present, and is the only route in the system
   that writes a database file rather than rows.
4. The service starts with `FPL_EDGE_DISABLE_NETWORK_INGEST=1` set, so the
   scheduler's first tick records every fetch task as `no_source` with a
   named reason rather than hammering every upstream while the warehouse is
   still empty. The owner clears the variable after the seed lands.

Cost of the alternative, rebuilding from ingest: the warehouse holds four
seasons of vaastav history, a month of daily FPL API snapshots, the odds
archive, six projection providers, the creator corpus with 62 transcribed
episodes, and the elite crawls. Rebuilding it needs the 15 GB raw archive,
which is on the Mac, so the rebuild would have to re-fetch from upstreams
that in several cases no longer serve the old windows. The transcripts could
not be rebuilt at all without re-running ASR over the audio cache. A duration
for this was not measured and would be a guess; treat it as days, not hours,
and as lossy. The upload is 160 MiB once.

### 3.5 Log retention

`data/warehouse/jobs/` holds 1,469 tick reports in 6.8 MB and
`pipeline_logs/` holds 114 run logs in 496 KB. Neither has a sweep. At a
600s tick that is 144 new job reports a day, so the directory reaches roughly
50,000 files a year. Add a retention sweep to the existing `audio_retention`
task's slot, or as its own maintenance task: keep 30 days of tick reports and
90 days of run logs. This is a follow-up, not a blocker, but a volume that
fills with tick reports takes the database down with it.

### 3.6 `TMPDIR` and read copies

`Warehouse.read_copy` copies the entire database file to a temp directory for
every heavy read, which is how every panel reads (`warehouse.py:451`). At
160 MiB per copy that is fast. At the extrapolated 1.6 GB it is not, and a
dashboard that runs several panels concurrently holds several copies at once.

Two things follow:

1. Set `TMPDIR=/app/data/tmp`, on the volume. The container's own writable
   layer is small and ephemeral, and filling it kills the container. Filling
   the volume is also bad, but the volume is sized and monitored and the
   layer is not.
2. Empty `/app/data/tmp` at boot. No read copy can legitimately survive a
   restart. The finalizer in `read_copy` is a backstop that has already
   failed in practice: the docstring records 457 orphaned directories
   totalling 5.1 GB found on one machine.

---

## 4 · Service topology

```
  ┌──────────────────────── Owner's Mac ────────────────────────┐
  │                                                              │
  │  launchd (nightly, 12:00 UTC)                                │
  │      └─ scripts/mac_transcribe_worker.py                     │
  │            ├─ GET  /api/transcripts/queue   (bearer)         │
  │            ├─ asr.fetch_audio   -> data/raw/content/asr_audio│
  │            ├─ asr.transcribe_file  (mlx-whisper, Metal GPU)  │
  │            └─ POST /api/transcripts         (bearer)         │
  │                                                              │
  │  One-time: POST /api/admin/seed  (fpl.duckdb, gzipped)       │
  └──────────────────────────────┬───────────────────────────────┘
                                 │ HTTPS
                                 ▼
  ┌─────────────────── Railway, one service, replicas = 1 ───────────────────┐
  │                                                                          │
  │   uvicorn, one process                                                   │
  │     ├─ FastAPI app (fpl_edge.platform.app:create_app)                    │
  │     │    ├─ /api/health          Railway's healthcheck target            │
  │     │    ├─ /api/panels, /api/scripts/{name}/run   18 panel scripts      │
  │     │    ├─ /api/query, /api/inbox, /api/monitors, /api/briefing         │
  │     │    ├─ /api/conversations/* chat, via the Claude CLI's own login    │
  │     │    ├─ /api/transcripts, /api/transcripts/queue   (new, bearer)     │
  │     │    ├─ routes_account       loopback only, so unreachable here      │
  │     │    └─ StaticFiles("/")     web/dist, baked into the image          │
  │     │                                                                    │
  │     └─ scheduler.py, one asyncio task                                    │
  │          └─ every 600s: deadline_dag.tick() in a thread executor         │
  │               ├─ claim   dag_firing  (short write burst)                 │
  │               ├─ run     via pipelines/runner.py:execute                 │
  │               └─ record  fetch_run + pipeline_logs/<run_id>.log          │
  │                                                                          │
  │   ONE writer lock, held by the scheduler module, taken by every writer    │
  │   in the process including POST /api/pipelines/{task_id}/run             │
  │                                                                          │
  └──────────────────────────────┬───────────────────────────────────────────┘
                                 │
                                 ▼
  ┌──────────── Railway volume, 10 GB, mounted at /app/data ─────────────────┐
  │  warehouse/fpl.duckdb   the single file, single writer                   │
  │  warehouse/*.parquet|json|html   artefacts, read as source_dir(wh)/NAME  │
  │  warehouse/chat/  pipeline_logs/  jobs/                                  │
  │  panels/  raw/  tmp/                                                     │
  └──────────────────────────────────────────────────────────────────────────┘
```

---

## 5 · Environment variables

**The rule, stated first because it is the one that matters: no `ANTHROPIC_*`
variable is ever set on this service.** Not `ANTHROPIC_API_KEY`, not
`ANTHROPIC_AUTH_TOKEN`, not `ANTHROPIC_BASE_URL`, not
`ANTHROPIC_CUSTOM_HEADERS`. The server holds no model key. Model calls go
through the operator's Claude CLI login, which owns its own auth, and both
`chat_agent.py:707` and `briefing_intel.py:388` already scrub those four
names plus `CLAUDE_CODE_ENTRYPOINT` and `CLAUDE_CODE_SSE_PORT` from the
environment handed to any child. Setting one on Railway would defeat that
scrub for the one path that reads it directly: `analyze.py:522` constructs
`anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))` as a fallback, and
that fallback must stay unreachable on this service. The build asserts the
absence (§10).

| Name | Purpose | Where set | Secret |
|---|---|---|---|
| `PORT` | the port uvicorn binds | Railway, automatic | no |
| `TMPDIR` | where `Warehouse.read_copy` writes its private copies; `/app/data/tmp` | Railway variable | no |
| `FPL_ENTRY_ID` | the owner's entry id, 4490171, until workstream D replaces the singleton | Railway variable | no |
| `ODDS_API_KEY` | the credit-metered Odds API key; `ingest/odds.py:1036` refuses without it | Railway variable | yes |
| `TELEGRAM_BOT_TOKEN` | outbox delivery; `config.py:141` | Railway variable | yes |
| `TELEGRAM_ALLOWED_CHAT_ID` | which chat the outbox may send to; `config.py:145`, optional | Railway variable | no |
| `TRANSCRIPT_PUSH_TOKEN` | the bearer secret the Mac worker presents (§1.7) | Railway variable, and the Mac keychain | yes |
| `TRANSCRIPT_PUSH_TOKEN_NEXT` | the second accepted value during a rotation | Railway variable, temporarily | yes |
| `FPL_EDGE_DISABLE_NETWORK_INGEST` | the one switch that gates every scheduled fetch; set to `1` for the first boot, cleared after the seed lands | Railway variable | no |
| `FPL_EDGE_DISABLE_PRIVATE` | set to `1`: guarantees no authenticated FPL request can happen from this host (`myteam/private.py:125`) | Railway variable | no |
| `FPL_EDGE_ANALYSE_BUDGET_S` | per-run wall budget for claim extraction; default 1800 | Railway variable | no |
| `FPL_EDGE_SCHEDULER` | whether the in-process scheduler starts; `1` on the service, unset in `make deploy-check` | Railway variable, new in B2 | no |
| `FPL_EDGE_DAG_POLISH` | model-polish of delivered copy; leave unset, it spends tokens | not set | no |
| `FPL_EDGE_TRANSCRIBE_BUDGET_S` | the Mac worker's wall budget | Mac only | no |
| `FPL_SESSION_COOKIE`, `FPL_ACCESS_TOKEN`, `FPL_REFRESH_TOKEN`, `FPL_USERNAME`, `FPL_PASSWORD` | the owner's FPL credentials | **not set on the service** | yes |
| `ANTHROPIC_*` (any) | model auth | **never set on the service** | n/a |

On the FPL credentials: `routes_account.py` refuses any request whose client
host is not loopback, and that guard held through both ngrok and Cloudflare
tunnels. On Railway every request arrives from the proxy, so those routes
answer 403 to everyone including the owner. The credentials therefore cannot
be entered through the deployed UI and must not be set as service variables
as a workaround. Private my-team reads stay on the Mac until workstream E
gives the server a real per-user credential store. With
`FPL_EDGE_DISABLE_PRIVATE=1` set, `myteam/account.py:191` reports the panels
as reading public picks with that exact reason, which is the honest empty
state rather than a silent fallback.

---

## 6 · Volume durability summary

Durable means it survives a restart and a redeploy because it is on the
volume. Ephemeral means it is in the container layer and is gone on restart.

| Category | Durable | Ephemeral |
|---|---|---|
| The database and its WAL | yes | |
| Artefacts beside it | yes | |
| Chat conversations and chart assets | yes | |
| Run logs and tick reports | yes, until the sweep in §3.5 | |
| The raw provenance archive | yes, with retention | |
| Read copies under `TMPDIR` | on the volume, but emptied at boot | |
| `web/dist/` | | in the image |
| `/app/seed/data/` | | in the image, read-only |
| Python site-packages | | in the image |

---

## 7 · Boot sequence

In order. Each step either succeeds or fails the boot loudly. None of them
guesses.

1. **Mount check.** `/app/data` exists, is a directory, and is writable
   (write and delete a probe file). If not, exit non-zero with a message
   naming the path. Do not fall back to the container layer: the service
   would appear healthy, accept writes, and lose them on the next restart.
2. **Empty `/app/data/tmp`.** Create it if absent, delete everything in it if
   present. No read copy survives a restart legitimately.
3. **Open the warehouse as a writer, once.** `Warehouse(db_path)` creates the
   parent directory, creates the file if absent, applies `store/schema.sql`,
   runs `Warehouse._migrate()` (the additive column migrations), and applies
   `store/views.sql` with `CREATE OR REPLACE`, in that order
   (`warehouse.py:369-386`). This is also where a WAL left by a killed
   container is replayed, so run one `CHECKPOINT` before closing and treat a
   failure here as a boot failure with the WAL path in the message. An empty
   volume gets a schema-only warehouse, which is what makes step 6 possible.
4. **Apply the per-package migrations.** These are applied lazily today, at
   first use, which means a migration failure surfaces as a 500 on whichever
   request happened to touch it first. Run them at boot so a failure is a
   boot failure:

   | Migration set | Applied by | What it creates |
   |---|---|---|
   | DAG and outbox | `deadline_dag.apply_migrations(wh)` | `dag_firing`, the outbox tables |
   | Content | `ContentStore(wh)` constructor, which calls `migrate()` | `schema_migration`, `content_item`, `content_claim`, `transcript_segment`, `content_analysis`, panel people, insights, `claim_outcome_revision` |
   | Intel | `intel/store.py` | its own `migrations/001_intel.sql` |
   | Projections | `ingest/projections/store.py` | its own `migrations/*.sql` |
   | Understat | `ingest/understat.py` | its own `understat_migrations/*.sql` |
   | Transcript provenance | `asr.ensure_schema(wh)` | `transcript_provenance` |

   Order matters only in that the content migrations must run after
   `schema.sql`, because they reference tables it creates. Each set is
   idempotent and records its own applied versions.
5. **Seed from the image, without overwriting.** For each file under
   `/app/seed/data/`, copy to the matching path under `/app/data/` if and
   only if the target does not exist (§3.4).
6. **Artefact presence check.** For each artefact the panels read as
   `source_dir(wh) / NAME`, record present or absent with the file's mtime.
   Do not create, do not rebuild, do not fabricate. The result goes into the
   health payload so an operator can see at a glance that, for example,
   `forecast.parquet` is missing and the Planner will therefore report an
   honest empty state until `forecast_refresh` fires.
7. **Start the scheduler**, unless `FPL_EDGE_SCHEDULER` says otherwise. The
   first tick runs immediately rather than after 600s, and that first tick is
   the catch-up: `deadline_dag.tick` reads `LOOKBACK = 36h` of owed firings
   and evaluates each against its own `stale_window`, running what is inside
   its window and recording what is outside it as `skipped_stale` with the
   age in the detail (§2.4).
8. **Flip the health endpoint to ready.** Only now. Steps 1 through 6 must
   complete before Railway routes traffic, or the first request hits a
   half-migrated database.

---

## 8 · Health checks

Railway polls one path. That path is `/api/health`, which exists today
(`app.py:197`) and returns `ok`, `repo_sha`, `warehouse`, `warehouse_present`
and `now`. B2 extends the payload.

| Field | Meaning | Affects the status code |
|---|---|---|
| `ok` | boot steps 1 through 6 completed | yes |
| `volume.mounted` | `/app/data` is a directory | yes |
| `volume.writable` | the probe file was written and deleted | yes |
| `volume.free_bytes` | remaining space on the volume | no, but alert under 30% |
| `warehouse_present` | the `.duckdb` file exists | yes |
| `migrations.applied` | every set in §7 step 4 returned | yes |
| `artefacts` | one entry per artefact: present, absent, and mtime | no |
| `scheduler.running` | the asyncio task is alive | **no**, see below |
| `scheduler.last_tick_utc` | when the last tick finished | no |
| `scheduler.next_due` | what `tick` reports as owed next | no |
| `repo_sha` | which commit is serving | no |

**A failing health check means Railway will not route traffic to the
container, and a container that keeps failing it is restarted.** That is why
the status code is driven by the volume and the database and not by the
scheduler. A scheduler bug that crashed the background task would, if it
failed the health check, restart-loop the whole service and take the UI down
with it. The correct response to a dead scheduler is a red dot on the
Pipelines panel and a `scheduler.running: false` in the payload, not an
outage.

Concretely:

- `/api/health` returns 503 when the volume is not mounted, not writable, the
  warehouse file is absent, or a migration set failed. Each of those is a
  condition under which serving a request would either lose data or return a
  wrong answer.
- It returns 200 with `scheduler.running: false` when the scheduler is dead.
  The service still serves panels correctly against the data it has.
- The healthcheck timeout in `railway.toml` must exceed the boot sequence.
  Step 3 on a 160 MiB database is fast, but step 4 on a first boot applies
  every migration in the repo. Set 300s and measure the real number on the
  first deploy.

---

## 9 · The Mac worker in operation

Covered in §1.7. The operational summary:

| Situation | What happens |
|---|---|
| Token correct | 200, transcript stored, `{"stored": true, "segments": N}` |
| Token correct, transcript already stored and identical | 200, `{"stored": false, "reason": "identical transcript already stored"}` |
| Token correct, a different transcript stored, `replace` false | 409, nothing written |
| Token missing or wrong | 401 `{"error": "unauthorized"}`. Nothing about the `item_id`. The server logs IP and the first four characters of the token. The worker stops the run, exits non-zero, and launchd records it. The queue is unaffected, so the next run retries the same items. |
| Token rotated mid-run | the old value is still accepted while `TRANSCRIPT_PUSH_TOKEN_NEXT` is set, so the run finishes |
| Railway unreachable | the worker retries with backoff, then exits non-zero. The transcript is lost only in the sense that the GPU time is spent again next run; nothing in the warehouse is inconsistent. |

---

## 10 · What the Dockerfile must exclude, and how the build proves it

Multi-stage, `linux/amd64`, Python 3.11 to match `requires-python`.

| Must not be in the image | Where it comes from | Why it cannot be there |
|---|---|---|
| `mlx-whisper`, `mlx`, `mlx-lm` | `pyproject.toml` declares `mlx-whisper>=0.4; sys_platform == 'darwin'` | needs Apple Metal. The marker already excludes it on Linux, so this is an assertion that the marker did its job, not a change. |
| `pbpaste` | referenced as a shell command in `myteam/cli.py:493`, `myteam/account.py:59`, `myteam/private.py:65`, `myteam/tokens.py:308` | a macOS binary. These are help strings in the FPL-credential flow, which is loopback-only and unreachable on Railway anyway. |
| `~/.local/bin/claude` as a hard path | `analyze.py:427` (`Path.home() / ".local/bin/claude"`) and `chat_agent.py:731` | the container has no such install. `_find_claude_cli` already falls back to `shutil.which("claude")`, so the path is checked and missed rather than breaking, but the build must prove no such file exists so the fallback is the only route. |
| `ANTHROPIC_API_KEY` or any `ANTHROPIC_*` | would only come from a build arg or an ENV line | §5 |
| `data/warehouse/*.duckdb`, `*.duckdb.wal` | gitignored, so a repo copy cannot include one | the database belongs to the volume |
| `.env` | gitignored | secrets come from Railway variables |
| `data/raw/**` beyond `.gitkeep` | gitignored | 15 GB |
| `av` (PyAV) | `pyproject.toml` declares it unconditionally | it is the audio decoder for ASR. It installs on Linux and is harmless, but it is dead weight in a container that never decodes audio. Moving it behind the darwin marker alongside `mlx-whisper` is a small `pyproject.toml` change worth making. |

**How the build proves it.** A final `RUN` layer in the Dockerfile that exits
non-zero on any hit. Failing at build time is the point: a check that runs at
container start would let a bad image reach the registry.

```
RUN set -eu; \
    ! python -c "import mlx" 2>/dev/null || (echo "mlx present"; exit 1); \
    ! python -c "import mlx_whisper" 2>/dev/null || (echo "mlx_whisper present"; exit 1); \
    ! command -v pbpaste >/dev/null || (echo "pbpaste present"; exit 1); \
    ! test -e "$HOME/.local/bin/claude" || (echo "claude hard path present"; exit 1); \
    ! find / -name "*.duckdb" -o -name "*.duckdb.wal" | grep -q . || (echo "duckdb in image"; exit 1); \
    ! test -e /app/.env || (echo ".env in image"; exit 1); \
    env | grep -q "^ANTHROPIC_" && (echo "ANTHROPIC_ set at build"; exit 1) || true
```

The same assertions run again inside `make deploy-check` against the built
image, because an image can be rebuilt without the build layer being
re-executed from cache.

A `.dockerignore` carries the exclusions rather than relying on `.gitignore`.
It must list at least `data/`, `.env`, `.venv/`, `__pycache__/`,
`.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `code_bases/`,
`web/chat-app/node_modules/`. Note that `data/` is excluded wholesale and the
tracked files under it are copied explicitly into `/app/seed/data/`, so the
seed is a deliberate list rather than whatever happened to be on disk.

---

## 11 · What the build agent (B2) must produce

### 11.1 Files

| File | What it contains |
|---|---|
| `Dockerfile` | multi-stage, `linux/amd64`, Python 3.11, `WORKDIR /app`, `uv sync` or `pip install -e .` without dev extras, `web/dist` copied in, the tracked `data/` files copied to `/app/seed/data/`, the assertion layer from §10, and a CMD that runs the boot sequence then uvicorn on `$PORT` |
| `.dockerignore` | the exclusions in §10 |
| `railway.toml` | `replicas = 1`, the volume mounted at `/app/data`, `healthcheckPath = "/api/health"`, a healthcheck timeout above the measured boot time, and zero deploy overlap (§2.5) |
| `fpl_edge/platform/scheduler.py` | the in-process scheduler of §2.4: one asyncio task, 600s cadence, immediate first tick, single-flight, the process-wide writer lock, and `FPL_EDGE_SCHEDULER` to disable it |
| `fpl_edge/platform/boot.py` (or equivalent) | the eight boot steps of §7, callable from the CMD and from the test |
| the transcript routes | `GET /api/transcripts/queue` and `POST /api/transcripts` per §1.4 to §1.7, added to `app.py` **before** the `Mount("/")`, which is a catch-all matched in order (the comment at `app.py:1043` records that a router added after it answers 404) |
| `scripts/mac_transcribe_worker.py` | the Mac-side worker of §1.8 |
| `deploy/com.fpledge.transcribe.plist` | launchd for the Mac worker, nightly, with the keychain read in the wrapper |
| changes to `pipeline.py` and `registry.py` | move the ASR backend gate off the top of `cmd_transcribe` (§1.3 item 1) and make `content_transcribe` report `no_source` on a host with no engine (§1.3 item 2) |
| `app.py` module docstring | corrected: two routes write to the corpus, not one (§1.7) |
| `Makefile` | the `deploy-check` target below. `deploy`, `deploy-dag`, `undeploy` and `undeploy-dag` already exist and are launchd targets; do not touch them. |

### 11.2 The `make deploy-check` acceptance test

From the brief: boot the image locally against an empty volume and prove every
panel returns a structured empty state rather than a 500.

The target must:

1. `docker build --platform linux/amd64 -t fpl-edge:check .` and fail on a
   non-zero exit.
2. Re-run the §10 assertions inside the built image.
3. Create an empty host directory and run the container with it mounted at
   `/app/data`, with `FPL_EDGE_DISABLE_NETWORK_INGEST=1`,
   `FPL_EDGE_SCHEDULER=0`, and no `ANTHROPIC_*` in the environment.
4. Poll `/api/health` until it returns 200, with a timeout, and record how
   long the boot took. That number is what the `railway.toml` healthcheck
   timeout is set from.
5. `GET /api/panels`, then for every name it returns
   `POST /api/scripts/{name}/run` and assert **HTTP 200** with a body that is
   a valid empty-state payload for that script. There are 18 registered
   scripts today: `creator_board`, `creator_detail`, `creator_report_card`,
   `dashboard_brief`, `fixture_board`, `fixture_detail`, `idea_registry`,
   `market_watch`, `ownership_eo`, `pipeline_board`, `pipeline_run_log`,
   `planner_grid`, `player_chatter`, `player_profile`, `player_radar`,
   `price_radar`, `projection_table`, `squad_overview`. The test must
   enumerate from `/api/panels`, not from that list, so a new panel is
   covered automatically.
6. Assert that a 500 body of the shape `{"error": true, "panel": ..., "reason": ...}`
   never appears. `app.py:243` produces that shape for a broken panel and it
   is deliberately distinct from the honest `{empty, reason}` 200. The test
   is exactly the distinction between the two.
7. Run one scheduler tick in-process against the empty warehouse and assert a
   `fetch_run` row was written. With no `dim_event` rows there are no
   deadlines, so the deadline-relative tasks are not owed; the calendar and
   interval tasks are, and with network ingest disabled they return `_GATED`,
   which is the `no_source` outcome. A `fetch_run` row with status
   `no_source` is a pass. A tick that writes no row at all is a fail.
8. Tear down the container and delete the host directory.

**One thing B2 must expect to fix for step 5 to pass.** With no warehouse
file, `run_script` reaches `read_copy`, which raises `FileNotFoundError` for
a missing path (`warehouse.py:465`), and `post_run_script`'s catch-all turns
that into `_panel_error`, a 500. Boot step 3 is what prevents this: opening
`Warehouse(db_path)` once creates a schema-only warehouse, so `read_copy`
succeeds and each script returns its own `{empty, reason}` instead. Whether
all 18 scripts do in fact return a structured empty state against a
schema-only warehouse was not verified for this spec, because verifying it
means running them. That verification is step 5's job, and any script that
raises instead is a defect for B2 to fix in the script, not in the route.

### 11.3 Acceptance, restated from the brief

`docker build` succeeds on `linux/amd64`. The container boots with an empty
volume, serves `/api/health`, and every registered panel returns HTTP 200
with a valid empty-state payload. The registry runs one full tick in-process
and records it in `fetch_run`. The image contains no `mlx`, no `pbpaste`, no
`~/.local/bin/claude` path, and no `ANTHROPIC_API_KEY`.

---

## 12 · Facts this spec could not verify from the code

Everything here needs checking before or during the first deploy. None of it
changes a recommendation.

| Claim | Status |
|---|---|
| The brief's "5GB-class warehouse" | **Contradicted by measurement.** The file is 168,046,592 bytes, 160.3 MiB. The 15 GB is `data/raw/`. |
| Railway attaches a volume to exactly one service | Taken from the brief's §1 and from Railway's model. Not verifiable from this repo. If it turns out a volume can attach to two services, fork 2 option (b) is still rejected, because the DuckDB single-writer constraint is the binding one. |
| The Railway setting that controls deploy overlap | Named nowhere in this repo. B2 must find the current setting in Railway's own documentation and set the overlap to zero (§2.5). |
| Railway Cron cold-start cost | Not measured. The recommendation against Cron does not rest on it; the single-writer and observability arguments are sufficient. |
| Gzipped size of the seeded database | Estimated at 60 to 100 MB from 160 MiB raw. DuckDB already compresses internally, so the real ratio could be much worse. Measure before choosing an upload mechanism. |
| Volume sizing at 10 GB | Extrapolated from 26 days of growth (160 MiB from 2026-08-18 to 2026-09-12, roughly 6 MB/day). Not a season measurement. |
| Whether all 18 panel scripts return a structured empty state against a schema-only warehouse | Not verified; verifying it requires running them, which this spec did not do. `make deploy-check` step 5 is the verification. |
| Whether `faster-whisper` on Railway CPU runs near 1x realtime | Estimated. The 11.7x to 12.3x MLX figure is measured and recorded in `registry.py`; the CPU figure is not. It does not change the recommendation, since (a) is free. |
| The unattributed projections ingest of 2026-09-09 16:26 UTC with `trigger=scheduler` while launchd was unloaded | **Closed. There is no second scheduler.** It was the settlement chain started from the Pipelines panel (fetch_run 77d3a20b, trigger=ui, log present under `data/warehouse/pipeline_logs/`). Its subprocess steps say `scheduler` because `fpl_edge/store/fetch_ledger.py:94` hardcodes that default and only `pipelines/runner.py:147` overrides it, so the `trigger` column is untrustworthy for every subprocess-written row until that default is fixed. The in-process scheduler on Railway is the only one; see the preamble to Section 13. |

## 13. Runbook: the first deploy

Written by agent B2 after the image passed `make deploy-check` on the owner's
Mac (build, boot on an empty volume, health 200, all 18 panels answering 200
as a payload or a named gap, one scheduler tick writing ledger rows). Section
12's open item about a second Mac scheduler is closed: BRIEF_SHARED 1b
established there is none, so the in-process scheduler is the only one.

### 13.1 Before you start

Generate the bearer secret and put it in the Mac keychain. The value never
goes into .env, which sits in the repo tree.

    python -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
    security add-generic-password -s fpl-edge-transcript-push -a "$USER" -w

Prove the image works locally first:

    make deploy-check

Note the boot time it prints and set `healthcheckTimeout` in railway.toml to
a comfortable multiple of it.

### 13.2 Create the service

1. Install the CLI and sign in yourself (`brew install railway`, then
   `railway login`). The token stays with you.
2. New Railway project, deploy from this repo. The builder is the Dockerfile;
   railway.toml already says so.
3. Attach a 10 GB volume mounted at `/app/data`. This is the only mount.
4. Confirm Replicas = 1. DuckDB permits one writer per file; a second replica
   fails to open the database at boot and restart-loops.
5. Set the deploy overlap to zero so the old container stops before the new
   one starts. Two containers on one volume is the same failure.

### 13.3 Variables

Every name the image reads. The Dockerfile bakes `FPL_EDGE_BOOT=1`,
`TMPDIR=/app/data/tmp`, `FPL_EDGE_DISABLE_PRIVATE=1` and `FPL_EDGE_REPO_SHA`.

| Name | What it does | Secret | First deploy |
|---|---|---|---|
| `PORT` | the port uvicorn binds | no | Railway sets it |
| `TRANSCRIPT_PUSH_TOKEN` | the bearer the Mac ASR worker presents | yes | set it |
| `TRANSCRIPT_PUSH_TOKEN_NEXT` | the second accepted value during a rotation | yes | leave unset |
| `FPL_EDGE_SCHEDULER` | `1` starts the in-process tick; unset means no scheduler | no | set to `1` |
| `FPL_EDGE_SCHEDULER_INTERVAL_S` | seconds between ticks; default 600 | no | leave unset |
| `FPL_EDGE_DISABLE_NETWORK_INGEST` | `1` makes every scheduled fetch record `no_source` with a named reason | no | set to `1`, clear after the seed |
| `FPL_EDGE_DATA_DIR` | which directory boot checks and seeds into, and the base of the per-user store; default `data` | no | leave unset |
| `FPL_EDGE_DATA_ROOT` | deprecated alias for `FPL_EDGE_DATA_DIR`. Still read when `FPL_EDGE_DATA_DIR` is unset, and warns once at boot. Two names for one directory let a deployment set one and put the per-user store off the volume, where it survives until the first redeploy | no | leave unset |
| `FPL_ENTRY_ID` | the default FPL entry when no user is signed in | no | set to your entry id |
| `ODDS_API_KEY` | the credit-metered Odds API key | yes | set it |
| `TELEGRAM_BOT_TOKEN` | outbox delivery | yes | set it |
| `TELEGRAM_ALLOWED_CHAT_ID` | which chat the outbox may send to | no | optional |
| `FPL_EDGE_ANALYSE_TOKEN_BUDGET` | reported tokens per analyse run before the batch stops; default 1500000 | no | optional |
| `FPL_EDGE_ANALYSIS_MODEL`, `FPL_EDGE_BRIEFING_MODEL`, `FPL_EDGE_CHAT_MODEL` | model overrides; defaults in config.py | no | leave unset |
| `FPL_EDGE_ANALYSE_BUDGET_S` | per-run wall budget for claim extraction; default 1800 | no | optional |
| `FPL_EDGE_TRANSCRIBE_BUDGET_S` | wall budget for the transcription task; default 3600 | no | optional |
| `FPL_EDGE_RAW` | relocate the raw archive root | no | leave unset |
| `FPL_EDGE_DAG_POLISH` | model-polish of delivered copy; spends tokens | no | leave unset |
| `FPL_THEME_MODE` | chart theme, `dark` by default | no | optional |

Google sign-in and the per-user model key add seven more. Two naming sets
reached the build, so both are read and either one configures the value. Set
one name per row and leave the other unset; setting both to different values
raises at startup rather than picking a winner.

| Name | Also accepted as | What it does | Secret | First deploy |
|---|---|---|---|---|
| `GOOGLE_CLIENT_ID` | | the OAuth client from the Google Console, section 13.7 | no | set it |
| `GOOGLE_CLIENT_SECRET` | | the secret from the same screen | yes | set it |
| `GOOGLE_REDIRECT_URI` | `OAUTH_REDIRECT_URL` | must match a registered URI byte for byte, scheme, host, port and path | no | `https://<service>.up.railway.app/auth/google/callback` |
| `SESSION_SECRET` | `SESSION_SIGNING_KEY` | signs the session cookie; rotating it signs everyone out | yes | set it |
| `USER_KEY_ENC_SECRET` | `KEY_ENCRYPTION_KEY` | AES-256-GCM key for the stored Anthropic keys, 32 bytes base64 | yes | set it |
| `USER_KEY_ENC_SECRET_PREV` | `KEY_ENCRYPTION_KEY_PREV` | the previous encryption secret, during a rotation window only | yes | leave unset |
| `OPERATOR_EMAIL` | `OWNER_EMAIL` | the one Google address that gets the operator routes | no | set to your own address |
| `PUBLIC_ENTRY_ID` | | the FPL entry an anonymous visitor's panels read | no | `4490171` |
| `FPL_EDGE_ANON_IS_OWNER` | | `1` answers unauthenticated requests as the operator; `0` forces the signed-out route set. Unset means `1` while no `GOOGLE_CLIENT_ID` exists and `0` once one does | no | leave unset |
| `AUTH_DB` | | where the sessions and encrypted keys live; default `<data dir>/auth/auth.sqlite3` | no | leave unset |
| `COOKIE_SECURE` | | overrides the Secure flag; derived from the redirect URI's scheme when unset | no | leave unset |
| `FPL_EDGE_VERIFY_USER_KEY` | | `0` stores a pasted key without checking it against Anthropic first | no | leave unset |

Generate the two secrets on your own machine and paste them straight into
Railway's secret store:

    python -c "import secrets;print(secrets.token_urlsafe(32))"                      # SESSION_SECRET
    python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"   # USER_KEY_ENC_SECRET

No `ANTHROPIC_*` variable is ever set on this service: not the key, not the
auth token, not the base URL. The server holds no model key of its own. Each
signed-in manager's key is stored encrypted under `USER_KEY_ENC_SECRET` and
reaches the CLI subprocess through `ClaudeAgentOptions.env` for one turn;
chat_agent.py and briefing_intel.py still scrub the `ANTHROPIC_*` names from
`os.environ`, which is what makes a bug that forgot to pass the per-user key
fail with an auth error instead of silently spending somebody else's plan. The
batch content analysis stays on the Mac under the operator's own credential
and never reads the per-user store.

No `FPL_*` credential either (`FPL_SESSION_COOKIE`, `FPL_ACCESS_TOKEN`,
`FPL_REFRESH_TOKEN`, `FPL_USERNAME`, `FPL_PASSWORD`). The three routes that
write them are operator-only on the access matrix, because the token store
writes one `.env` and cannot hold two managers' tokens.

### 13.4 First boot, then the seed

Deploy with `FPL_EDGE_SCHEDULER` unset and `FPL_EDGE_DISABLE_NETWORK_INGEST=1`,
so the first boot does nothing but come up. Watch `/api/health`: it returns 503
until the volume is mounted and writable, the warehouse file exists and every
migration set has run, then 200 with the boot report (`volume`, `promoted`,
`migrations`, `artefacts`, `scheduler`).

At that point the warehouse is schema-only. Every panel answers 200 with a
named gap; `fixture_ratings_refit` and `forecast_refresh` record honest error
rows until there is history; everything else records `no_source`.

Now send the real database. Checkpoint it on the Mac first, so no write-ahead
log is left beside it:

    uv run python -c "from fpl_edge.store import Warehouse
    with Warehouse() as wh: wh.sql('CHECKPOINT')"
    ls -l data/warehouse/fpl.duckdb*

One file, about 165 MiB, and no `.wal`. Upload it under the staging name. The
running container may hold `fpl.duckdb` open, and DuckDB replays a write-ahead
log against whatever file carries that name, so a direct overwrite is how a
good upload destroys a good database. Boot promotes the staged file instead,
before it opens any connection:

    railway ssh config                 # writes an OpenSSH block for the service
    scp data/warehouse/fpl.duckdb <the host that block names>:/app/data/warehouse/fpl.duckdb.incoming
    railway redeploy

`railway ssh "ls -la /app/data/warehouse/"` confirms the upload arrived whole
before the redeploy: the byte count must match the Mac's. The next boot
validates it (a readable DuckDB with rows in `dim_player`), drops the WAL of
the database it supersedes, and renames it into place atomically. The health
payload then carries the receipt:

    "promoted": {"from": "fpl.duckdb.incoming", "bytes": 173551616,
                 "players": 659, "replaced_bytes": 2359296, "wal_dropped": false}

A truncated or wrong-shaped upload fails the boot with the path named and
leaves the database that was already there untouched, so a bad copy costs a
restart and nothing else.

With the receipt in hand, set `FPL_EDGE_SCHEDULER=1`, clear
`FPL_EDGE_DISABLE_NETWORK_INGEST`, and redeploy. The first tick reads 36 hours
of owed firings, runs what is inside each task's stale window and records the
rest as `skipped_stale`.

### 13.5 The Mac

Retire the two scheduling agents (both already unloaded; this removes them):

    make undeploy-dag
    make undeploy

Install the ASR worker, now the only scheduled job on this machine:

    make transcribe-once FPL_EDGE_BASE_URL=https://<your-service>.up.railway.app
    make deploy-transcribe FPL_EDGE_BASE_URL=https://<your-service>.up.railway.app

`transcribe-once` prints the queue, the engine line and a per-item result. A
401 stops the run with exit 3; check the token on both sides.
`deploy-transcribe` writes a launchd agent that reads the token from the
keychain and runs one pass nightly at 12:00.

### 13.6 Rotating the bearer token

1. Set `TRANSCRIPT_PUSH_TOKEN_NEXT` on Railway to the new value.
2. Update the Mac keychain to the new value.
3. `make transcribe-once ...` and confirm a 200.
4. Move the new value into `TRANSCRIPT_PUSH_TOKEN` and clear `_NEXT`.

Two accepted values during the window mean the two sides never restart
together. Rotate quarterly, and at once on any suspicion.

### 13.7 Google sign-in: the owner's own steps

Five minutes, once, and nobody else can do them. An agent cannot sign in to
the Google Console as you, and no agent ever reads or writes the two values
this produces.

1. Open `console.cloud.google.com` and select a project, or create one called
   `i-test-season`.
2. APIs and Services, then OAuth consent screen. User type External. Fill in
   the app name, your support email and your developer contact email. Save.
3. On the Scopes step add `openid` and `.../auth/userinfo.email`, and nothing
   else. Save. While the app is in Testing, add your own Google address under
   Test users. Publishing is only needed when somebody other than a test user
   signs in.
4. APIs and Services, then Credentials, then Create credentials, then OAuth
   client ID. Application type Web application.
5. Under Authorised redirect URIs add both of these, exactly:
   - `https://<your-service>.up.railway.app/auth/google/callback`
   - `http://localhost:8321/auth/google/callback`

   Google permits `http` for `localhost` specifically, which is what makes
   local development work without a tunnel. A URI that differs by a trailing
   slash, by `http` against `https`, or by `www`, is a different URI and
   Google refuses it with `redirect_uri_mismatch`. That error is the most
   common failure here and it is always this.
6. Create. Copy the client id and the client secret into Railway's variables,
   and into your local `.env` for the localhost redirect.
7. Generate `SESSION_SECRET` and `USER_KEY_ENC_SECRET` with the two one-liners
   in 13.3 and set them in Railway's secret store. Generate them on your own
   machine.
8. Set `OPERATOR_EMAIL` to your own Google address and `PUBLIC_ENTRY_ID` to
   `4490171`.
9. Deploy, open the app and sign in once. That first sign-in is what writes
   the `users` row with `is_operator = 1`. Until it happens every operator
   route answers 403 for everybody, which is the correct state for a server
   nobody has claimed.
10. On the Account tab, paste your own Anthropic API key from
    `console.anthropic.com`. Chat answers 403 for a signed-in manager until
    they paste one of their own, and each manager's turns are billed to their
    own Anthropic account.
11. If anyone other than you is to use the app, publish the consent screen in
    the Console or add each person under Test users. Google blocks sign-in
    for anybody else while the app is in Testing.

To check the flow locally before deploying, set the same variables in `.env`
with `GOOGLE_REDIRECT_URI=http://localhost:8321/auth/google/callback`, run
`uv run fpl serve`, and open `http://localhost:8321/#account`. With no
`GOOGLE_CLIENT_ID` set at all the server keeps answering as the operator,
which is how the Mac has always run.

### 13.8 Rotating the two auth secrets

`SESSION_SECRET` invalidates every session cookie the moment it changes.
Every manager signs in again, which costs one click because Google does not
re-prompt for consent. Rotate it only on a suspected leak.

`USER_KEY_ENC_SECRET` makes every stored Anthropic key undecryptable. To
rotate without anyone re-pasting: set `USER_KEY_ENC_SECRET_PREV` to the old
value, set `USER_KEY_ENC_SECRET` to the new one, and redeploy. Each stored
key is decrypted with the old secret and re-encrypted at the new one the
first time it is used, so the window closes on its own and
`USER_KEY_ENC_SECRET_PREV` can be cleared afterwards. Rotating without the
`_PREV` value leaves every manager with a 409 on the Account tab telling them
to paste their key again, which is the honest answer and never a 500.
