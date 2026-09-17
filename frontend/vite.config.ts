/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { port: 5173 },
  test: {
    environment: "./src/test/env.ts",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
