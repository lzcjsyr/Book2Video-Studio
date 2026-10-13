import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
    proxy: process.env.STUDIO_BACKEND_PORT
      ? {
          "/api": {
            target: `http://127.0.0.1:${process.env.STUDIO_BACKEND_PORT}`,
            headers: { Authorization: `Bearer ${process.env.STUDIO_TOKEN}` },
          },
        }
      : undefined,
    watch: { ignored: ["**/src-tauri/**", "**/backend/**", "**/build/**"] },
  },
});
