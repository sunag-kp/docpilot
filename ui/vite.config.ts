import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Relative asset URLs: the same build works at "/" (served by FastAPI) and under
  // "/docpilot/" (GitHub Pages demo).
  base: "./",
  server: {
    // The demo backend reads the evaluation corpus from ../eval.
    fs: { allow: [".."] },
    proxy: { "/v1": "http://localhost:8000", "/readyz": "http://localhost:8000" },
  },
  test: {
    environment: "jsdom",
  },
});
