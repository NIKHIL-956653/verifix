import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api/* to FastAPI on :8010, so the browser only ever talks to
// one origin and no API key or backend address is baked into frontend code.
export default defineConfig({
  plugins: [react()],
  server: {
    // 5178 rather than Vite's default 5173, which other local projects tend to hold.
    port: 5178,
    strictPort: false,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8010",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
