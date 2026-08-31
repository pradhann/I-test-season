/* Thin client over the conversation API (fpl_edge/platform/app.py).
   Shapes: see docs/platform/CHAT_ARCHITECTURE.md and the live server. */

async function json(r) {
  if (!r.ok) {
    let detail = "";
    try {
      const body = await r.json();
      detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body);
    } catch {
      try { detail = await r.text(); } catch { /* opaque */ }
    }
    const err = new Error(detail || `HTTP ${r.status}`);
    err.status = r.status;
    throw err;
  }
  return r.json();
}

export const listConversations = () =>
  fetch("/api/conversations").then(json);

export const createConversation = () =>
  fetch("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  }).then(json);

export const getEvents = (convId, after = -1) =>
  fetch(`/api/conversations/${convId}/events?after=${after}`).then(json);

export const startTurn = (convId, text) =>
  fetch(`/api/conversations/${convId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  }).then(json);

export const stopTurn = (convId) =>
  fetch(`/api/conversations/${convId}/stop`, { method: "POST" }).then(json);

export const streamUrl = (convId, after) =>
  `/api/conversations/${convId}/stream?after=${after}`;

export const assetUrl = (chartId, ext) => `/api/chat/assets/${chartId}.${ext}`;
