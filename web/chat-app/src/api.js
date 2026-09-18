/* Thin client over the conversation API (fpl_edge/platform/app/routes_chat.py).
   Shapes: see docs/platform/CHAT_ARCHITECTURE.md and the live server.

   CSRF. Every state changing request carries the `itest_csrf` cookie back in
   an X-CSRF-Token header, which the server compares against the digest on the
   session row. The cookie is readable by this page on purpose: a cross-site
   POST cannot read this origin's cookies and so has nothing to echo. An
   anonymous caller has no session to ride on and sends no token.

   The stream is a GET and carries none: EventSource cannot set headers, which
   is why that route is a GET in the first place.

   This file is the source. The served artefact is
   web/dist/chat-app/assets/index.js and it is a build output, so this change
   is live only after `npm run build` in web/chat-app. */

function csrfToken() {
  for (const part of String(document.cookie || "").split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === "itest_csrf") return decodeURIComponent(rest.join("="));
  }
  return "";
}

/* Headers for a state changing request. Content-Type only when there is a
   body, so a bare POST stays a bare POST. */
function writeHeaders(withBody = false) {
  const headers = withBody ? { "Content-Type": "application/json" } : {};
  const token = csrfToken();
  if (token) headers["X-CSRF-Token"] = token;
  return headers;
}

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
    headers: writeHeaders(true),
    body: "{}",
  }).then(json);

export const getEvents = (convId, after = -1) =>
  fetch(`/api/conversations/${convId}/events?after=${after}`).then(json);

export const startTurn = (convId, text) =>
  fetch(`/api/conversations/${convId}/chat`, {
    method: "POST",
    headers: writeHeaders(true),
    body: JSON.stringify({ text }),
  }).then(json);

export const deleteConversation = (convId) =>
  fetch(`/api/conversations/${convId}`, {
    method: "DELETE", headers: writeHeaders(),
  }).then(json);

export const stopTurn = (convId) =>
  fetch(`/api/conversations/${convId}/stop`, {
    method: "POST", headers: writeHeaders(),
  }).then(json);

export const streamUrl = (convId, after) =>
  `/api/conversations/${convId}/stream?after=${after}`;

export const assetUrl = (chartId, ext) => `/api/chat/assets/${chartId}.${ext}`;
