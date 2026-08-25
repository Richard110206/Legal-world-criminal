import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const BACKEND_TARGET = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: BACKEND_TARGET,
        changeOrigin: true,
      },
      "/ws": {
        target: BACKEND_TARGET,
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
