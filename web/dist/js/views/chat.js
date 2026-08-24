/* Chat: the deterministic QuestionRouter, instantly; the agent for the rest.
   POST /api/chat gives the router's answer (text + optional PNGs, sometimes
   with markdown tables — parsed here into table.data, never innerHTML'd).
   Unrouted questions auto-escalate to an agent CONVERSATION: POST
   /api/conversations/{id}/chat starts a server-side turn, and the pane
   follows /api/conversations/{id}/stream (SSE). The conversation id lives in
   localStorage; on load the transcript replays from /events and the stream
   re-attaches from the last seq — a reload mid-turn loses nothing. A chip on
   every answer says which brain produced it: "router" or "agent". */

import { el, getJSON, postJSON, errBox } from "/js/app.js";

// Questions the router actually answers (fpl_edge/interfaces/qa.py intents).
const SUGGESTIONS = [
  "review my team",
  "which defenders have the highest xpoints",
  "suggest me transfers",
  "which fixtures to target",
];

const CONV_KEY = "itest-conv-id";

/* ---- markdown-ish rendering: pipe tables become table.data, everything
   else stays text. All content lands via textContent — router output and
   agent output are data, not markup. ---- */

function isTableLine(line) {
  const t = line.trim();
  return t.startsWith("|") && t.endsWith("|") && t.length > 2;
}

function isSeparator(line) {
  return /^\s*\|[\s:|-]+\|\s*$/.test(line) && line.includes("-");
}

function cells(line) {
  const t = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return t.split("|").map(c => c.trim());
}

function renderTable(block) {
  const wrap = el("div", "scroll-x");
  const table = el("table", "data");
  let rows = block;
  const thead = el("thead");
  if (block.length >= 2 && isSeparator(block[1])) {
    const tr = el("tr");
    cells(block[0]).forEach(h => tr.appendChild(el("th", null, h)));
    thead.appendChild(tr);
    rows = block.slice(2);
  }
  table.appendChild(thead);
  const tbody = el("tbody");
  for (const line of rows) {
    if (isSeparator(line)) continue;
    const tr = el("tr");
    cells(line).forEach(c => tr.appendChild(el("td", null, c)));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function renderText(node, text) {
  const lines = String(text ?? "").split("\n");
  let i = 0;
  while (i < lines.length) {
    if (isTableLine(lines[i])) {
      const block = [];
      while (i < lines.length && isTableLine(lines[i])) block.push(lines[i++]);
      node.appendChild(renderTable(block));
    } else {
      const start = i;
      while (i < lines.length && !isTableLine(lines[i])) i++;
      const chunk = lines.slice(start, i).join("\n").replace(/\n{3,}/g, "\n\n");
      if (chunk.trim()) {
        const p = el("div");
        p.style.whiteSpace = "pre-wrap";
        p.textContent = chunk.trim();
        node.appendChild(p);
      }
    }
  }
}

/* Agent text may carry [chart:<id>] markers (id = uuid-hex, pinned by the
   regex, so the constructed URL cannot smuggle a path). Split the text on
   the markers; each text span goes through the table-aware renderer, each
   marker becomes an <img> served from /api/chat/assets/. */
function renderAgentText(node, text) {
  const parts = String(text ?? "").split(/\[chart:([0-9a-fA-F][0-9a-fA-F-]{7,63})\]/g);
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      if (parts[i].trim()) renderText(node, parts[i]);
    } else {
      const img = el("img");
      img.src = `/api/chat/assets/${parts[i]}.png`;
      img.alt = "chart";
      img.loading = "lazy";
      img.style.maxWidth = "100%";
      img.style.borderRadius = "6px";
      img.onerror = () => img.replaceWith(el("div", "sub", `chart ${parts[i]} unavailable`));
      node.appendChild(img);
    }
  }
}

/* ---- the view ---- */

export default async function chat(host) {
  const wrap = el("section", "card");
  wrap.appendChild(el("h2", null, "Ask the engine"));
  wrap.appendChild(el("p", "sub",
    "Deterministic answers from the question router — the same brain as the " +
    "Telegram bot. Unrouted questions escalate to the agent conversation, " +
    "which survives reloads and restarts."));

  const log = el("div", "chat-log");
  wrap.appendChild(log);

  // Suggested prompts: questions the router really matches.
  const chips = el("div", "filters");
  for (const s of SUGGESTIONS) {
    const b = el("button", "chip", s);
    b.onclick = () => { input.value = s; send(); };
    chips.appendChild(b);
  }
  wrap.appendChild(chips);

  const form = el("div", "filters");
  const input = el("input");
  input.type = "text";
  input.placeholder = "e.g. review my team";
  input.style.flex = "1";
  input.setAttribute("aria-label", "message");
  const sendBtn = el("button", "primary", "Send");
  const stopBtn = el("button", null, "Stop");
  stopBtn.style.display = "none";
  form.append(input, sendBtn, stopBtn);
  wrap.appendChild(form);

  const opts = el("div", "filters");
  const agentLabel = el("label", "sub");
  const agentToggle = el("input");
  agentToggle.type = "checkbox";
  agentLabel.append(agentToggle, " always ask the agent (skip the router)");
  const newConvBtn = el("button", "chip", "new conversation");
  opts.append(agentLabel, newConvBtn);
  wrap.appendChild(opts);
  host.appendChild(wrap);

  function msg(cls) {
    const m = el("div", `msg ${cls}`);
    log.appendChild(m);
    m.scrollIntoView({ block: "nearest" });
    return m;
  }

  function brainChip(m, brain) {
    m.appendChild(el("div", "provenance", brain));
  }

  /* ---- agent conversation state ---- */

  let convId = null;
  let lastSeq = -1;
  let es = null;               // the one EventSource for this conversation
  let pendingUser = null;      // user text we already rendered locally
  let agentMsg = null;         // the bot bubble the running turn writes into
  let segment = null;          // {node, buf} — the streaming text block
  let tools = new Map();       // tool_use id -> <details>
  let running = false;

  function setRunning(on) {
    running = on;
    stopBtn.style.display = on ? "" : "none";
    sendBtn.disabled = on;
  }

  function openAgentMsg() {
    if (!agentMsg) {
      agentMsg = msg("bot");
      brainChip(agentMsg, "agent");
    }
    return agentMsg;
  }

  function closeAgentMsg() {
    agentMsg = null;
    segment = null;
    tools = new Map();
  }

  function ensureSegment() {
    const m = openAgentMsg();
    if (!segment) {
      const node = el("div");
      node.style.whiteSpace = "pre-wrap";
      m.appendChild(node);
      segment = { node, buf: "" };
    }
    return segment;
  }

  const handlers = {
    user(p) {
      // The turn we started already drew its user bubble; replayed history
      // (or another tab's message) has not.
      if (pendingUser !== null && p.text === pendingUser) { pendingUser = null; return; }
      closeAgentMsg();
      msg("user").textContent = p.text;
    },
    init() {},
    delta(p) {
      const s = ensureSegment();
      s.buf += p.text;
      s.node.textContent = s.buf;
      s.node.scrollIntoView({ block: "nearest" });
    },
    text(p) {
      // The authoritative full block replaces whatever streamed in.
      const s = ensureSegment();
      s.node.textContent = "";
      s.node.style.whiteSpace = "";
      renderAgentText(s.node, p.text);
      segment = null;
    },
    tool_use(p) {
      const m = openAgentMsg();
      segment = null;
      const d = el("details", "tool-row");
      const sum = el("summary", "sub",
        `▸ ${p.name || "tool"} — ${p.input_preview || ""}`);
      d.appendChild(sum);
      m.appendChild(d);
      if (p.id) tools.set(p.id, d);
    },
    tool_result(p) {
      const d = p.tool_use_id && tools.get(p.tool_use_id);
      const pre = el("pre");
      pre.textContent = p.preview || "(empty result)";
      pre.style.whiteSpace = "pre-wrap";
      if (d) {
        if (p.is_error) d.querySelector("summary").classList.add("err");
        d.appendChild(pre);
      } else {
        const orphan = el("details", "tool-row");
        orphan.appendChild(el("summary", "sub", "▸ tool result"));
        orphan.appendChild(pre);
        openAgentMsg().appendChild(orphan);
      }
    },
    done(p) {
      const m = openAgentMsg();
      const bits = [];
      if (p.cost_usd != null) bits.push(`$${Number(p.cost_usd).toFixed(4)}`);
      if (p.duration_ms != null) bits.push(`${(p.duration_ms / 1000).toFixed(1)}s`);
      if (bits.length) m.appendChild(el("div", "provenance", bits.join(" · ")));
      closeAgentMsg();
      setRunning(false);
    },
    error(p) {
      openAgentMsg().appendChild(el("div", "err", p.message || "agent error"));
      closeAgentMsg();
      setRunning(false);
    },
  };

  function apply(event) {
    if (event.seq <= lastSeq) return;
    lastSeq = event.seq;
    const h = handlers[event.type];
    if (h) h(event.payload || {});
  }

  function attachStream() {
    if (es) es.close();
    if (!("EventSource" in window)) {
      log.appendChild(el("div", "err",
        "this browser has no EventSource; agent answers will not stream"));
      return;
    }
    es = new EventSource(`/api/conversations/${convId}/stream?after=${lastSeq}`);
    for (const type of Object.keys(handlers)) {
      es.addEventListener(type, ev => {
        try { apply(JSON.parse(ev.data)); } catch { /* malformed frame */ }
      });
    }
    // Transport errors: the browser retries on its own; the seq guard makes
    // its replayed frames idempotent. Nothing to do here.
  }

  async function ensureConv() {
    if (convId) return convId;
    const saved = localStorage.getItem(CONV_KEY);
    if (saved) {
      try {
        const page = await getJSON(`/api/conversations/${saved}/events?after=-1`);
        convId = saved;
        for (const ev of page.events || []) apply(ev);
        if (page.running) setRunning(true);
        attachStream();
        return convId;
      } catch { localStorage.removeItem(CONV_KEY); }
    }
    const created = await postJSON("/api/conversations", {});
    convId = created.conv_id;
    localStorage.setItem(CONV_KEY, convId);
    lastSeq = -1;
    attachStream();
    return convId;
  }

  async function sendAgent(text) {
    const id = await ensureConv();
    pendingUser = text;
    try {
      await postJSON(`/api/conversations/${id}/chat`, { text });
      setRunning(true);
    } catch (e) {
      pendingUser = null;
      const m = msg("bot");
      brainChip(m, "agent");
      if (String(e.message || e).includes("409")) {
        m.appendChild(el("div", "err",
          "a turn is already running in this conversation — stop it or wait"));
      } else {
        m.appendChild(errBox(e));
      }
    }
  }

  async function send() {
    const text = input.value.trim();
    if (!text || running) return;
    input.value = "";
    msg("user").textContent = text;

    if (agentToggle.checked) { await sendAgent(text); return; }

    const bot = msg("bot");
    bot.appendChild(el("div", "sub", "…"));
    sendBtn.disabled = true;
    try {
      const res = await postJSON("/api/chat", { text });
      bot.textContent = "";
      if (res.routed) {
        brainChip(bot, "router");
        renderText(bot, res.text);
        if (res.intent) bot.appendChild(el("div", "provenance", `intent: ${res.intent}`));
        for (const img of res.images || []) {
          const im = el("img");
          im.src = `data:${img.mime || "image/png"};base64,${img.base64}`;
          im.alt = img.filename || "chart";
          im.style.maxWidth = "100%";
          im.style.borderRadius = "6px";
          bot.appendChild(im);
        }
      } else {
        // No deterministic intent matched: escalate to the agent, visibly.
        bot.appendChild(el("div", "sub", "no router intent — asking the agent…"));
        await sendAgent(text);
      }
    } catch (e) {
      bot.textContent = "";
      bot.appendChild(errBox(e));
    } finally {
      if (!running) sendBtn.disabled = false;
      input.focus();
      bot.scrollIntoView({ block: "nearest" });
    }
  }

  stopBtn.onclick = async () => {
    if (!convId) return;
    try { await postJSON(`/api/conversations/${convId}/stop`, {}); }
    catch (e) { log.appendChild(errBox(e)); }
  };

  newConvBtn.onclick = async () => {
    if (es) { es.close(); es = null; }
    localStorage.removeItem(CONV_KEY);
    convId = null;
    lastSeq = -1;
    closeAgentMsg();
    setRunning(false);
    log.textContent = "";
    try { await ensureConv(); } catch (e) { log.appendChild(errBox(e)); }
  };

  sendBtn.onclick = send;
  input.addEventListener("keydown", ev => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); send(); }
  });

  // Reload → replay the persisted transcript and re-attach the live stream.
  try { await ensureConv(); } catch (e) { log.appendChild(errBox(e)); }
  input.focus();
}
