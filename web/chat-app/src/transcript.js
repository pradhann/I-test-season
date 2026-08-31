/* Event stream -> renderable transcript.

   The server persists an ordered event log per conversation (seq-numbered,
   replayable). This module folds that log into a list of display items:

     {kind:"user",  text}
     {kind:"prose", text, streaming}        assistant markdown (deltas fold in;
                                            the authoritative `text` event
                                            replaces the accumulated deltas)
     {kind:"tools", calls:[{id,name,input,result,isError,settled}]}
                                            consecutive tool calls group into
                                            one card stack
     {kind:"done",  costUsd, durationMs, numTurns, model}
     {kind:"error", message}

   Pure functions over a state object so a replayed transcript and a live
   stream go through exactly the same code path. */

export function emptyTranscript() {
  return { items: [], lastSeq: -1, running: false, model: null };
}

function last(items) {
  return items.length ? items[items.length - 1] : null;
}

/* End any in-flight streaming prose block (a tool call or a new user message
   interrupts it; the eventual `text` event may then start a fresh block —
   which is also what the backend emits, one text event per completed block). */
function sealStreaming(items) {
  const tail = last(items);
  if (tail && tail.kind === "prose" && tail.streaming) {
    return items.slice(0, -1).concat([{ ...tail, streaming: false }]);
  }
  return items;
}

export function applyEvent(state, event) {
  if (!event || typeof event.seq !== "number" || event.seq <= state.lastSeq) {
    return state;
  }
  const p = event.payload || {};
  let items = state.items;
  let model = state.model;
  let running = state.running;

  switch (event.type) {
    case "user": {
      items = sealStreaming(items).concat([{ kind: "user", text: p.text ?? "" }]);
      running = true;
      break;
    }
    case "init": {
      if (p.model) model = p.model;
      running = true;
      break;
    }
    case "delta": {
      const tail = last(items);
      if (tail && tail.kind === "prose" && tail.streaming) {
        items = items.slice(0, -1).concat([
          { ...tail, text: tail.text + (p.text ?? "") },
        ]);
      } else {
        items = items.concat([{ kind: "prose", text: p.text ?? "", streaming: true }]);
      }
      break;
    }
    case "text": {
      // Authoritative block: replaces whatever streamed in for this segment.
      const tail = last(items);
      if (tail && tail.kind === "prose" && tail.streaming) {
        items = items.slice(0, -1).concat([
          { kind: "prose", text: p.text ?? "", streaming: false },
        ]);
      } else {
        items = items.concat([{ kind: "prose", text: p.text ?? "", streaming: false }]);
      }
      break;
    }
    case "tool_use": {
      items = sealStreaming(items);
      const call = {
        id: p.id || `seq-${event.seq}`,
        name: p.name || "tool",
        input: p.input_preview || "",
        result: null,
        isError: false,
        settled: false,
      };
      const tail = last(items);
      if (tail && tail.kind === "tools") {
        items = items.slice(0, -1).concat([
          { ...tail, calls: tail.calls.concat([call]) },
        ]);
      } else {
        items = items.concat([{ kind: "tools", calls: [call] }]);
      }
      break;
    }
    case "tool_result": {
      const settled = {
        result: p.preview ?? "",
        isError: !!p.is_error,
        settled: true,
      };
      let matched = false;
      // Results follow their tool_use closely; search from the tail.
      const next = items.slice();
      for (let i = next.length - 1; i >= 0 && !matched; i--) {
        const it = next[i];
        if (it.kind !== "tools") continue;
        const j = it.calls.findIndex((c) => c.id === p.tool_use_id);
        if (j >= 0) {
          const calls = it.calls.slice();
          calls[j] = { ...calls[j], ...settled };
          next[i] = { ...it, calls };
          matched = true;
        }
      }
      if (matched) {
        items = next;
      } else {
        // Orphan result (should not happen; render honestly rather than drop).
        items = items.concat([{
          kind: "tools",
          calls: [{ id: p.tool_use_id || `seq-${event.seq}`, name: "tool result",
                    input: "", ...settled }],
        }]);
      }
      break;
    }
    case "done": {
      items = sealStreaming(items).concat([{
        kind: "done",
        costUsd: p.cost_usd ?? null,
        durationMs: p.duration_ms ?? null,
        numTurns: p.num_turns ?? null,
        model,
      }]);
      running = false;
      break;
    }
    case "error": {
      items = sealStreaming(items).concat([
        { kind: "error", message: p.message || "agent error" },
      ]);
      running = false;
      break;
    }
    default:
      break; // unknown event types are skipped, seq still advances
  }

  return { items, lastSeq: event.seq, running, model };
}

export function applyEvents(state, events) {
  let s = state;
  for (const ev of events || []) s = applyEvent(s, ev);
  return s;
}

/* Client-side title: the first user message, truncated. The backend has no
   title API yet (§5 lists agent-generated titles as future work). */
export function deriveTitle(state) {
  const first = state.items.find((it) => it.kind === "user");
  if (!first) return null;
  const t = first.text.replace(/\s+/g, " ").trim();
  return t.length > 64 ? t.slice(0, 61).trimEnd() + "…" : t || null;
}
