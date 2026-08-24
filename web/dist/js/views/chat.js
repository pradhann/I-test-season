/* Chat: the deterministic QuestionRouter, instantly; the agent on request.
   POST /api/chat gives the router's answer (text + optional PNGs, sometimes
   with markdown tables — parsed here into table.data, never innerHTML'd).
   When the router has no intent it says so and offers escalation; that
   renders as a button which streams /api/chat/stream (headless `claude -p`)
   token-by-token. Same router the Telegram bot uses: one brain, two panes. */

import { el, postJSON, errBox } from "/js/app.js";

// Questions the router actually answers (fpl_edge/interfaces/qa.py intents).
const SUGGESTIONS = [
  "review my team",
  "which defenders have the highest xpoints",
  "suggest me transfers",
  "which fixtures to target",
];

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

/* ---- the view ---- */

export default async function chat(host) {
  const wrap = el("section", "card");
  wrap.appendChild(el("h2", null, "Ask the engine"));
  wrap.appendChild(el("p", "sub",
    "Deterministic answers from the question router — the same brain as the " +
    "Telegram bot. Unrouted questions can escalate to the agent."));

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
  form.append(input, sendBtn);
  wrap.appendChild(form);
  host.appendChild(wrap);

  function msg(cls) {
    const m = el("div", `msg ${cls}`);
    log.appendChild(m);
    m.scrollIntoView({ block: "nearest" });
    return m;
  }

  function streamAgent(text, into) {
    const pre = el("pre");
    pre.textContent = "";
    into.appendChild(pre);
    const es = new EventSource(`/api/chat/stream?text=${encodeURIComponent(text)}`);
    const stop = () => { es.close(); };
    es.addEventListener("start", () => {
      into.appendChild(el("div", "sub", "agent is thinking (headless claude)…"));
    });
    es.addEventListener("token", ev => {
      pre.textContent += ev.data + "\n";
      pre.scrollTop = pre.scrollHeight;
    });
    es.addEventListener("error", ev => {
      if (ev.data) into.appendChild(el("div", "err", ev.data));
      stop();
    });
    es.addEventListener("done", ev => {
      into.querySelectorAll(".sub").forEach(n => n.remove());
      if (ev.data !== "0") into.appendChild(el("div", "err", `agent exited ${ev.data}`));
      stop();
    });
    es.onerror = stop;               // transport-level failure: stop retry loops
    if (!("EventSource" in window)) {
      into.appendChild(el("div", "err", "this browser has no EventSource; use POST /api/chat"));
    }
  }

  async function send() {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    msg("user").textContent = text;
    const bot = msg("bot");
    bot.appendChild(el("div", "sub", "…"));
    sendBtn.disabled = true;
    try {
      const res = await postJSON("/api/chat", { text });
      bot.textContent = "";
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
      if (!res.routed && res.escalation_available) {
        const ask = el("button", null, "Ask the agent instead");
        ask.onclick = () => {
          ask.remove();
          streamAgent(text, msg("bot"));
        };
        bot.appendChild(el("div")).appendChild(ask);
      }
    } catch (e) {
      bot.textContent = "";
      bot.appendChild(errBox(e));
    } finally {
      sendBtn.disabled = false;
      input.focus();
      bot.scrollIntoView({ block: "nearest" });
    }
  }

  sendBtn.onclick = send;
  input.addEventListener("keydown", ev => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); send(); }
  });
  input.focus();
}
