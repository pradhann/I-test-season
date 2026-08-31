/* Entry point. The platform shell (web/dist/js/views/chat.js) appends a
   persistent #chat-root div into the view container and injects this bundle
   once; we mount once and survive tab switches because the shell re-parents
   the same div rather than rebuilding it. */

import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./chat.css";

const host = document.getElementById("chat-root");
if (host && !host.dataset.mounted) {
  host.dataset.mounted = "1";
  createRoot(host).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
