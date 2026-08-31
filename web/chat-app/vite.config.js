import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The sub-app is served by the platform server from web/dist/chat-app/ at
// /chat-app/. Fixed output filenames (no hashes) so the mount seam in
// web/dist/js/views/chat.js can reference them without reading a manifest.
export default defineConfig({
  plugins: [react()],
  base: "/chat-app/",
  build: {
    outDir: "../dist/chat-app",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: "assets/index.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/index[extname]",
      },
    },
  },
  server: {
    // Dev convenience: the API and the app's token stylesheet come from the
    // running platform server.
    proxy: {
      "/api": "http://localhost:8321",
      "/app.css": "http://localhost:8321",
    },
  },
});
