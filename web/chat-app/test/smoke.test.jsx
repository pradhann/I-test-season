/* Headless smoke test: mounts the real App in happy-dom against the recorded
   conversation fixture (a real transcript plus one synthetic turn whose text
   event carries a ```doc block), then unit-tests the exporter with a mocked
   asset fetch. Run via `npm test` (test/run.mjs bundles this with esbuild).

   No browser: this is the CI-grade floor. Live checks happen in the pane. */

import { Window } from "happy-dom";
import { readFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const here = dirname(fileURLToPath(import.meta.url));

const win = new Window({ url: "http://localhost/" });
for (const k of ["window", "document", "navigator", "HTMLElement", "Node",
  "Text", "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "customElements", "MutationObserver"]) {
  if (win[k] !== undefined) globalThis[k] = win[k];
}

const page = JSON.parse(readFileSync(join(here, "events.fixture.json"), "utf8"));
const convs = {
  conversations: [
    { conv_id: "abc123", title: "conversation",
      created: "2026-08-31T22:05:43Z", updated: "2026-08-31T22:10:21Z" },
  ],
};

globalThis.fetch = async (url, opts) => {
  const u = String(url);
  const body =
    u.startsWith("/api/conversations/abc123/events") ? page :
    u === "/api/conversations" && !opts ? convs :
    u === "/api/conversations" ? { conv_id: "new1", meta: { created: "2026-08-31T23:00:00Z" } } :
    u.endsWith("/chat") ? { started: true, seq: 20 } :
    {};
  return { ok: true, json: async () => body };
};

class FakeES {
  constructor(url) { FakeES.opened.push(url); setTimeout(() => this.onopen?.(), 0); }
  addEventListener() {}
  close() {}
}
FakeES.opened = [];
FakeES.CLOSED = 2;
FakeES.CONNECTING = 0;
globalThis.EventSource = FakeES;
win.EventSource = FakeES;

const { createRoot } = await import("react-dom/client");
const React = (await import("react")).default;
const { default: App } = await import("../src/App.jsx");
const { buildExportHtml, parseDoc, exportFilename } = await import("../src/doc.jsx");
const { splitDocSegments } = await import("../src/thread.jsx");

const host = document.createElement("div");
host.id = "chat-root";
document.body.appendChild(host);
createRoot(host).render(React.createElement(App));
await new Promise((r) => setTimeout(r, 300));

const html = host.innerHTML;
const docEvent = page.events.find((e) => e.seq === 18).payload.text;
const docSource = splitDocSegments(docEvent).find((s) => s.kind === "doc").text;

/* ---- exporter, with the asset fetch mocked ---- */
const exported = await buildExportHtml(docSource, {
  fetchAsset: async (id) => ({ kind: "svg", text: `<svg data-test="inline-${id}"><rect/></svg>` }),
  readToken: () => "",
  now: new Date("2026-08-31T12:00:00Z"),
});

const parsed = parseDoc(docSource);

const checks = {
  // phase 4 floor stays intact
  "sidebar item": html.includes("conv-item"),
  "derived title": html.includes("Chart the top 10 players"),
  "user msg": html.includes("user-msg"),
  "tool group collapsed": html.includes("3 tool calls"),
  "prose chart img": html.includes("/api/chat/assets/e48d18ade05f443c9e6490b4c982a561.svg"),
  "markdown bold": html.includes("<strong>Takeaway:</strong>"),
  "turn footer": html.includes("23.2s") && html.includes("$0.3682") && html.includes("claude-opus-5"),
  "composer": html.includes("textarea"),
  "sse attached": FakeES.opened.length > 0 && FakeES.opened[0].includes("after=19"),

  // phase 6: the ```doc block renders as a document card, not code
  "doc card present": html.includes("doc-card"),
  "doc masthead title": html.includes("GW3 Squad Review"),
  "doc outline present": html.includes("doc-outline") &&
    html.includes("Where the points came from") && html.includes("What to do next"),
  "doc export button": html.includes(">Export<"),
  "doc copy button": html.includes(">Copy markdown<"),
  "doc chart figure": html.split("doc-card")[1]?.includes("/api/chat/assets/e48d18ade05f443c9e6490b4c982a561.svg"),
  "doc table renders": html.includes("<td>Haaland</td>"),
  "inner sql stays code": html.includes("SELECT web_name, pts FROM gw3_returns"),
  "prose around doc survives": html.includes("Here is the weekly review") &&
    html.includes("That is the full review"),

  // parse layer
  "parse title": parsed.title === "GW3 Squad Review",
  "parse heading count": parsed.headingCount === 3,
  "filename kebab-dated": exportFilename(parsed.title, new Date("2026-08-31T12:00:00Z")) ===
    "gw3-squad-review-2026-08-31.html",

  // exporter output is standalone and has the SVG inlined
  "export has doctype": exported.startsWith("<!doctype html>"),
  "export title tag": exported.includes("<title>GW3 Squad Review</title>"),
  "export inlined svg": exported.includes('<svg data-test="inline-e48d18ade05f443c9e6490b4c982a561"'),
  "export no leftover slots": !exported.includes("data-chart="),
  "export table": exported.includes("<td>Haaland</td>"),
  "export inline style": exported.includes("<style>") && exported.includes("#16181b"),
};

let ok = true;
for (const [name, pass] of Object.entries(checks)) {
  console.log(pass ? "PASS" : "FAIL", name);
  if (!pass) ok = false;
}
if (!ok) {
  console.log("\n----- app html (first 5000) -----\n" + html.slice(0, 5000));
  console.log("\n----- export html (first 3000) -----\n" + exported.slice(0, 3000));
  process.exit(1);
}
console.log(`\n${Object.keys(checks).length} checks passed`);
