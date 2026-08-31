/* Thread rendering: prose blocks (streaming markdown + chart figures +
   document cards), grouped tool-trace cards, turn footers, honest error
   blocks. Charts/lightbox live in charts.jsx; document cards in doc.jsx. */

import React, { useState } from "react";
import { RichText } from "./charts.jsx";
import { DocumentCard } from "./doc.jsx";

export { Lightbox } from "./charts.jsx";

/* ---------------- prose, with ```doc fences as document cards ----------------

   A fenced block with language `doc` is a report, not code (phase 6
   contract). Inner code fences inside the doc are tracked so a ```sql block
   does not close the document; a bare ``` outside any inner fence does. An
   unterminated doc fence mid-stream renders with what has arrived. */

export function splitDocSegments(text) {
  const lines = String(text ?? "").split("\n");
  const segs = [];
  let buf = [];
  let mode = "md";       // md | doc
  let mdFence = false;   // inside an ordinary fence in md mode
  let innerFence = false; // inside a code fence within the doc

  const flush = (kind) => {
    const chunk = buf.join("\n");
    if (chunk.trim()) segs.push({ kind, text: chunk });
    buf = [];
  };

  for (const line of lines) {
    if (mode === "md") {
      if (!mdFence && /^```doc\s*$/.test(line)) {
        flush("md");
        mode = "doc";
        innerFence = false;
        continue;
      }
      if (/^```/.test(line)) mdFence = !mdFence;
      buf.push(line);
    } else if (innerFence) {
      if (/^```\s*$/.test(line)) innerFence = false;
      buf.push(line);
    } else if (/^```\s*$/.test(line)) {
      flush("doc");
      mode = "md";
    } else {
      if (/^```/.test(line)) innerFence = true;
      buf.push(line);
    }
  }
  flush(mode);
  return segs;
}

export function Prose({ text, streaming, onOpenChart }) {
  const segs = splitDocSegments(text);
  return (
    <div className={"prose" + (streaming ? " streaming" : "")}>
      {segs.map((s, i) =>
        s.kind === "doc"
          ? <DocumentCard key={i} source={s.text} onOpenChart={onOpenChart} />
          : <RichText key={i} text={s.text} onOpenChart={onOpenChart} />)}
      {streaming && <span className="caret" aria-hidden="true" />}
    </div>
  );
}

/* ---------------- tool traces ---------------- */

// §3.2 families; colour tags the family, the name carries the meaning.
const FAMILY_RULES = [
  ["analysis", /^(query|python_viz|run_analysis|save_analysis|list_analyses|engine_status|make_chart)/],
  ["players", /^(player_|projection_|xpts_|set_piece)/],
  ["squad", /^(get_team_|get_manager_|suggest_transfers|weekly_decision)/],
  ["market", /^(ownership_|price)/],
  ["fixtures", /^fixture/],
  ["creators", /^(fpl_creator|fpl_content|fpl_player_claims|summarise_fpl|fetch_youtube)/],
  ["elite", /^get_expert/],
  ["memory", /^(watchlist_|submit_idea|review_ideas|track_ideas|mark_idea)/],
];

export function toolMeta(rawName) {
  const name = String(rawName || "tool").replace(/^mcp__.*?__/, "");
  for (const [family, re] of FAMILY_RULES) {
    if (re.test(name)) return { name, family };
  }
  return { name, family: "other" };
}

function ToolCall({ call }) {
  const [open, setOpen] = useState(false);
  const { name, family } = toolMeta(call.name);
  const status = call.settled ? (call.isError ? "error" : "ok") : "running";
  return (
    <div className={`tool-card fam-${family}${call.isError ? " is-error" : ""}`}>
      <button
        type="button"
        className="tool-head"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="tool-caret">{open ? "▾" : "▸"}</span>
        <span className="tool-name">{name}</span>
        <span className="tool-preview">{call.input}</span>
        {status === "running" && <span className="tool-status run">running</span>}
        {status === "error" && <span className="tool-status err">error</span>}
      </button>
      {open && (
        <div className="tool-body">
          {call.input && (
            <div className="tool-section">
              <div className="tool-k">input</div>
              <pre>{call.input}</pre>
            </div>
          )}
          <div className="tool-section">
            <div className="tool-k">{call.isError ? "error" : "result"}</div>
            <pre>{call.settled ? (call.result || "(empty result)") : "…"}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

export function ToolGroup({ calls }) {
  const [open, setOpen] = useState(false);
  if (calls.length === 1) return <div className="tool-group">{calls.map((c) => <ToolCall key={c.id} call={c} />)}</div>;
  const pending = calls.some((c) => !c.settled);
  const errors = calls.filter((c) => c.isError).length;
  if (!open) {
    return (
      <div className="tool-group">
        <button type="button" className="tool-card tool-head collapsed-group"
                aria-expanded={false} onClick={() => setOpen(true)}>
          <span className="tool-caret">▸</span>
          <span className="tool-name">{calls.length} tool calls</span>
          <span className="tool-preview">
            {calls.map((c) => toolMeta(c.name).name).join(" · ")}
          </span>
          {pending && <span className="tool-status run">running</span>}
          {errors > 0 && <span className="tool-status err">{errors} failed</span>}
        </button>
      </div>
    );
  }
  return (
    <div className="tool-group">
      <button type="button" className="group-collapse" aria-expanded={true}
              onClick={() => setOpen(false)}>
        ▾ {calls.length} tool calls
      </button>
      {calls.map((c) => <ToolCall key={c.id} call={c} />)}
    </div>
  );
}

/* ---------------- turn chrome ---------------- */

export function TurnFooter({ item }) {
  const bits = [];
  if (item.durationMs != null) bits.push(`${(item.durationMs / 1000).toFixed(1)}s`);
  if (item.costUsd != null) bits.push(`$${Number(item.costUsd).toFixed(4)}`);
  if (item.model) bits.push(item.model);
  if (!bits.length) return null;
  return <div className="turn-footer">{bits.join(" · ")}</div>;
}

export function ErrorBlock({ message }) {
  // Error events carry remediation text — shown verbatim.
  return <div className="chat-error">{message}</div>;
}

export function UserMsg({ text }) {
  return <div className="user-msg">{text}</div>;
}

export function Thinking() {
  return (
    <div className="thinking" aria-live="polite">
      <span className="thinking-dot" />
      <span className="thinking-dot" />
      <span className="thinking-dot" />
    </div>
  );
}
