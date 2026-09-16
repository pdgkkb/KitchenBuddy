import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import happyBitePerf from "./vite.perf.js";

// In development the API runs on FastAPI (port 8000); Vite forwards to it.
// `host: true` so the kitchen tablet can reach the dev server over the LAN.
// 127.0.0.1, not localhost: on Windows Node resolves localhost to ::1 first,
// and uvicorn only listens on IPv4.
export default defineConfig({
  plugins: [happyBitePerf(), react()],
  build: {
    target: "es2022",                            // no down-levelling for browsers that don't run this app
    modulePreload: { polyfill: false },          // every supported browser preloads modules natively
    cssCodeSplit: true,                          // lazy screens bring their own CSS
    assetsInlineLimit: 0                         // fonts stay files: cacheable, preloadable
  },
  server: {
    host: true,
    port: 5173,
    fs: { allow: [".."] },                       // ../shared holds the catalogue
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/media": { target: "http://127.0.0.1:8000", changeOrigin: true }
    }
  }
});
