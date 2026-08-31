# Chat — the architecture spec

Status: BUILT 2026-08-31. Phases 1-4, 6, 7 shipped and live-verified the
same day the spec closed; phase 5's surface half shipped (the router route
is deleted and pinned gone) with the deep module untangling tracked as its
own task. Commits: 0a3757a fb61c78 c9dc564 fc90f59 5d79fc6 ac9652c.

The owner's framing: chat is the most important feature of the platform and
needs the most work. The current implementation is two brains bolted together
and neither is the right one.

---

## 1 · What exists, honestly

| Piece | State | Verdict |
|---|---|---|
| `fpl_edge/interfaces/qa_router.py` + `qa.py` | Regex intent router, built for the Telegram era. Answers a fixed menu deterministically. | **DELETE as a brain.** Its good answers become tool implementations the agent calls. Telegram surface retires with it (owner: "not a useful feature anyways"). |
| `fpl_edge/platform/chat_agent.py` (846 lines) | Turn-as-server-side-job, persist-then-broadcast `events.jsonl`, `claude_session_id` resume, honest error events. Already imitates Argus §1.3. | **KEEP the design, replace the engine.** Its flaw is driving a headless `claude -p` subprocess: no control channel (stop = SIGTERM), no hooks, hand-parsed stream-json, MCP through a spawned process. |
| `fpl_mcp/` | 37 tools over the warehouse: xpts, EO, creators, claims, transfers, dossiers, watchlist, ideas, raw SQL `query`, … | **KEEP.** This is the data surface. Registration moves in-process. |
| `make_chart` (fpl_mcp/tools/chat_tools.py) | 4-kind spec plotter (bar/barh/line/scatter) → PNG | **DELETE.** Replaced by the python_viz tool (§4). |
| `web/dist/js/views/chat.js` (400 lines) | `textContent` + `<img>` rendering, router/agent split UI | **REPLACE** with the built chat sub-app (§5). |
| `docs/platform/argus_architecture.md` | 650-line study of the vendored Argus codebase with the FPL mapping | The reference. Sections cited below as A§n. |

---

## 2 · Decisions (DECIDED with owner)

1. **One brain.** Every message goes to the agent. The router is deleted;
   Telegram retired. Deterministic answers the router did well (team review,
   top-by-position, creator summaries) become ordinary tools.
2. **Python Agent SDK** (`claude-agent-sdk`) replaces the CLI subprocess.
   Same Claude Code engine underneath — the owner's Max-plan CLI login remains
   the auth; the server still holds no API key. Gains: `interrupt()` via the
   control channel, hooks, in-process MCP tool registration
   (`create_sdk_mcp_server`), typed message stream instead of hand-parsed
   stream-json, `resume` semantics owned by the SDK.
3. **Zero-build is relaxed for the chat pane only.** The chat/artifact UI
   becomes a small built sub-app (compiled once, served static from
   `web/dist/chat-app/`). The rest of the platform stays vanilla ES modules.
4. **Charts: the agent writes real Python under an enforced theme** (§4).
   Not a spec renderer — flexibility with house style guaranteed by the
   harness, not the model.
5. **Conversations first.** V1 ships great conversations that produce great
   documents. The Argus scripts/pinning primitive (versioned re-runnable
   analyses) is v2, built once we see which analyses actually get re-run.
6. **Player profile data: Understat first** — per-player/per-match shots, xG,
   xA, on-demand async fetch cached in the warehouse; surfaces in chat AND the
   xPoints player drawer. FBRef only if a needed metric is missing. FPL-lens
   metrics (involvement, threat, minutes risk), never IRL scouting.

---

## 3 · The agent loop (Argus §1, in Python)

```
POST /api/chat/conversations/:id/turn
  → ChatAgent.start_turn()             turn = server-side job (kept)
    → agent_sdk.query(prompt, options) claude-agent-sdk, streaming
       options.resume = stored session_id
       options.mcp_servers = in-process fpl toolbelt (§3.2)
       options.setting_sources = []    # deployment owns config, like Argus
       options.system_prompt = derived from registered capabilities
```

- **Durable transcript is ours; SDK session is a cache** (A§1.1). Events land
  in the existing `events.jsonl` layout (persist-then-broadcast, kept
  verbatim); if resume fails with a lost session, rebuild a recovery prompt
  from our own transcript and start a fresh SDK session — the conversation
  never dies with the cache.
- **Replay-buffer SSE** (kept from today's implementation, it already works):
  reload mid-turn replays the buffer then follows live.
- **Interrupt is graceful**: SDK `interrupt()` first, hard kill after grace.
- **Tool registry, explicit** (A§1.2): one Python registry file listing tool
  families — warehouse query tools (the 37, curated), python_viz, artifact
  tools, understat fetch. Adding authority = one reviewable line. No
  auto-discovery.
- **Permission posture**: the agent gets NO Bash, NO file write outside its
  conversation workspace, NO warehouse write. Tools are the only hands.
  (The python_viz sandbox is the one place code executes — see §4.)

### 3.2 The toolbelt, curated

The 37 tools move from "spawned MCP process" to in-process registration.
Curation pass at migration: collapse near-duplicates, give every tool the
`useWhen` guidance field Argus puts on sources (A§1.2), and organize by
family: **squad** (my team, plans), **market** (prices, EO, transfers),
**players** (dossier, form, projections, understat profile), **fixtures**
(board, detail), **creators** (consensus, claims, track record), **elite**
(cohort picks/transfers), **analysis** (raw SQL query with the leakage guard,
python_viz), **memory** (watchlist, ideas, saved analyses).

The question range this serves (owner's list, made concrete):
"who do the elite own that I don't" · "compare Saka vs Palmer for the next 6"
· "what did the Wire say about my defence" · "who's transferring out Haaland
this week" · "is Semenyo's xG sustainable" (understat) · "build me a
wildcard draft under 100.5" (solver tool, existing) · "chart every club's
attack run" (python_viz over the fixtures panel data).

---

## 4 · python_viz: real Python, house style enforced

The owner's ask: not spec'd bar charts — the agent writes real plotting code
producing Athletic/Opta-grade visuals.

- **A sandboxed subprocess tool**: `python_viz(code, caption)` runs the code
  in a fresh interpreter with resource limits (CPU seconds, memory, no
  network), a read-only data seam, and `fpl_theme` pre-imported.
- **The theme is the contract**: `fpl_theme` styles matplotlib globally on
  import (typeface stack, Athletic-style grids — horizontal only, recessive;
  club colours via the same 20-entry map the UI uses; diverging ramps matching
  the app; annotation helpers: `title_block()`, `footer_source()`,
  `label_last_point()`, badge placement). The agent writes content; the theme
  owns the look. Output: SVG preferred (crisp in both UI themes), PNG
  fallback; files land in the conversation's assets dir exactly as
  `make_chart` PNGs do today.
- **Data reaches the sandbox as files, not credentials** (Argus's
  credential-free runner, A§2.4, scaled down): the tool call names which
  panel/query results to materialize as parquet/CSV into the sandbox before
  the code runs. The sandbox never opens the warehouse.
- The dataviz skill's rules apply (no dual axes, colour follows entity,
  neutral-midpoint diverging) — encoded in `fpl_theme` docs the agent sees.

---

## 5 · The chat sub-app (built, small, high quality)

Stack: **Vite + React** (boring, proven; Preact-swappable later if size
matters). Lives in `web/chat-app/`, builds to `web/dist/chat-app/`; the
existing shell mounts it in the Chat tab via one script tag. The build step
is `npm run build`, committed output, no server-side toolchain.

Rendering, Claude-Code/Cowork grade:
- **Streaming markdown** with proper typography (the artifact-design skill's
  standards): headings, tables, footnotes, code blocks.
- **Tool-call trace cards**: every tool_use/tool_result pair renders as a
  collapsible card tagged by family (like Claude Code's tool rows) — the
  provenance IS the UI. Result payloads capped, expandable.
- **Chart blocks**: SVG/PNG from python_viz inline, click to lighten/expand,
  caption + "show the code" flip.
- **Documents**: when the agent produces a report-shaped answer it emits a
  `<!-- doc -->` fenced artifact; the pane renders it as a paged document
  with a sticky outline (the drawer act-nav pattern from Fixtures), export
  to standalone HTML.
- **Conversation sidebar**: list, titles (agent-generated after turn 1),
  pin, search over transcripts (SQL over events.jsonl loaded to a local
  index; v2: FTS).
- Both themes via the same tokens (`--ink`, `--surface`, …) imported into
  the sub-app's CSS.

---

## 6 · Understat player profiles (feeds chat AND xPoints)

- `fpl_edge/ingest/understat.py`: on-demand fetch of a player's season +
  per-match shots/xG/xA/key-passes, name-resolved through the existing strict
  resolver (never edit distance), cached append-only in the warehouse
  (`fact_player_understat`, PIT-stamped). Async: first click triggers fetch,
  UI shows "fetching…" then fills; subsequent reads are warehouse-local.
- Chat gets `player_profile(code)` returning the FPL-relevant read: shot
  volume trend, xG vs returns (finishing luck), involvement, minutes
  pattern. The xPoints player drawer gets the same panel — one panel script,
  two consumers, per house rule.

---

## 7 · Deletions

- `qa_router.py` routing layer + Telegram bot surface (the good answer
  implementations are salvaged into tools first).
- `make_chart` and the router's PNG pipeline.
- `web/dist/js/views/chat.js` after the sub-app lands.
- The dual-brain "router vs agent" split in `app.py` routes.

## 8 · Build order (each step shippable)

1. Add `claude-agent-sdk`; swap the subprocess for the SDK inside the
   existing ChatAgent contract (events.jsonl unchanged) — the current UI
   keeps working against the better engine.
2. In-process toolbelt registration + curation + derived system prompt.
3. python_viz sandbox + fpl_theme; delete make_chart.
4. Chat sub-app v1: streaming markdown + trace cards + charts + sidebar.
5. Router/Telegram deletion (salvage pass first).
6. Documents/artifacts + export.
7. Understat ingest + player_profile panel (chat + xPoints drawer).

## 9 · Final decisions (closed with owner, 2026-08-31)

1. **Ambient squad context — always.** Every conversation carries entry
   4490171's live state (current XV, bank, chips, next deadline) in its
   system context, refreshed per turn. "Should I sell Watkins" needs zero
   lookups. Implementation: one panel-backed context builder, cached per
   deadline so the per-turn cost is a read, not a crawl.
2. **Opus always.** Every turn on the strongest model; quota is the price
   and chat is the platform's most important feature. No routing logic.
3. **Start clean.** The old events.jsonl conversations are kept on disk
   untouched, but the new sidebar starts empty — they were router-era
   tests, not history worth carrying.
