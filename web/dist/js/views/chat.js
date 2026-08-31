/* Chat view: a thin mount for the built React sub-app (source in
   web/chat-app/, built to web/dist/chat-app/ with fixed asset names —
   /chat-app/assets/index.js and /chat-app/assets/index.css — so this seam
   never needs a manifest).

   Re-entry: the hash router clears #view on every navigation, so the mounted
   React tree would die with it. Instead the app lives in one persistent
   #chat-root div that this view re-parents into the fresh container; the
   bundle mounts into it exactly once (guarded by data-mounted) and survives
   tab switches with its state — open SSE stream included. */

const JS_HREF = "/chat-app/assets/index.js";
const CSS_HREF = "/chat-app/assets/index.css";

let chatRoot = null;

function injectOnce() {
  if (!document.querySelector(`link[href="${CSS_HREF}"]`)) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = CSS_HREF;
    document.head.appendChild(link);
  }
  if (!document.querySelector(`script[src="${JS_HREF}"]`)) {
    const script = document.createElement("script");
    script.type = "module";
    script.src = JS_HREF;
    document.head.appendChild(script);
  }
}

export default function chat(host) {
  if (!chatRoot) {
    chatRoot = document.createElement("div");
    chatRoot.id = "chat-root";
  }
  host.appendChild(chatRoot); // appended before the module loads: the bundle
  injectOnce();               // finds #chat-root on first execution
}
