/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API runs in its own container; the dev server proxies /api to it, SSE included.
const api = process.env.STRATAL_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    // The source is bind-mounted from Windows, where file events do not reach the container.
    watch: { usePolling: true, interval: 300 },
    // The fonts are bundled in ../assets/fonts (DESIGN-SYSTEM.md, open point 1).
    fs: { allow: [".", "../assets"] },
    proxy: { "/api": { target: api, changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    css: false,
  },
});
