import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built files go straight into the Python package so the API (and the desktop app) serve them.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../waferguard/api/static", emptyOutDir: true, chunkSizeWarningLimit: 800 },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/health": "http://localhost:8000",
    },
  },
});
