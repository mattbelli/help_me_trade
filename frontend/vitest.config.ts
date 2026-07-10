import { defineConfig } from "vitest/config";
import path from "node:path";

// Scoped to the chart engine's pure modules — no jsdom, no React. The rest of
// the frontend is verified by typecheck + the headless-browser screenshot loop.
export default defineConfig({
  test: {
    include: ["lib/chart-engine/**/*.test.ts"],
    environment: "node",
  },
  resolve: {
    alias: { "@": path.resolve(__dirname) },
  },
});
