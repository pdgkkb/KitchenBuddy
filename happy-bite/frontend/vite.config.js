import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on FastAPI (port 8000); Vite forwards to it.
// `host: true` so the kitchen tablet can reach the dev server over the LAN.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    fs: { allow: [".."] },                       // ../shared holds the catalogue
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/media": { target: "http://localhost:8000", changeOrigin: true }
    }
  }
});
