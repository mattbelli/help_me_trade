import { defineConfig } from "vitest/config";
import path from "node:path";

// Scoped to pure, dependency-free modules — no jsdom, no React. The rest of the
// frontend is verified by typecheck + the headless-browser screenshot loop.
export default defineConfig({
  test: {
    include: [
      "lib/chart-engine/**/*.test.ts",
      "lib/bars-cache-persist.test.ts",
      "lib/workspace/watchlist-sort.test.ts",
    ],
    environment: "node",
  },
  resolve: {
    alias: { "@": path.resolve(__dirname) },
  },
});
