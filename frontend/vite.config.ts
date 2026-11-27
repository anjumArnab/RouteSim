import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // In development the backend runs separately on port 8000.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
