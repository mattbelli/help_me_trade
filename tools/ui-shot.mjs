#!/usr/bin/env node
/**
 * ui-shot — screenshot a workspace widget from the /dev/gallery render harness.
 *
 * Coding agents ship widget UI blind. This gives them eyes: one command that
 * renders a widget against frozen fixtures and writes a PNG the agent can open.
 *
 *   node tools/ui-shot.mjs positions
 *   node tools/ui-shot.mjs orders --state empty
 *   node tools/ui-shot.mjs chart --viewport 1600x1000
 *   node tools/ui-shot.mjs --all
 *
 * Prints the absolute PNG path as its ONLY stdout line (everything else goes to
 * stderr), so `PNG=$(node tools/ui-shot.mjs positions)` just works.
 *
 * Requires the Next dev server:  cd frontend && npm run dev -- -p 3055
 */
import { mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..");
const FRONTEND = join(REPO, "frontend");
const OUT_DIR = join(FRONTEND, ".ui-shots");

const STATES = ["default", "empty", "loading", "error"];
const DEFAULT_BASE = "http://localhost:3055";
const DEFAULT_VIEWPORT = { width: 1600, height: 1000 };
/** Fixture fetches resolve instantly; this covers effects, rAF-deferred charts
 *  and recharts' first measured layout. */
const DEFAULT_SETTLE_MS = 1500;

function die(message) {
  process.stderr.write(`ui-shot: ${message}\n`);
  process.exit(1);
}

function parseArgs(argv) {
  const opts = {
    widget: null,
    all: false,
    state: "default",
    base: DEFAULT_BASE,
    viewport: { ...DEFAULT_VIEWPORT },
    settle: DEFAULT_SETTLE_MS,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--all") {
      opts.all = true;
    } else if (arg === "--state" || arg === "-s") {
      opts.state = argv[++i];
    } else if (arg === "--viewport" || arg === "-v") {
      const m = /^(\d+)x(\d+)$/.exec(argv[++i] ?? "");
      if (!m) die("--viewport wants WxH, e.g. --viewport 1600x1000");
      opts.viewport = { width: Number(m[1]), height: Number(m[2]) };
    } else if (arg === "--base" || arg === "-b") {
      opts.base = argv[++i];
    } else if (arg === "--settle") {
      opts.settle = Number(argv[++i]);
    } else if (arg === "--help" || arg === "-h") {
      process.stderr.write(
        "usage: node tools/ui-shot.mjs <widget-id> [--state default|empty|loading|error]\n" +
          "                             [--viewport WxH] [--base URL] [--settle MS]\n" +
          "       node tools/ui-shot.mjs --all [--state ...]\n",
      );
      process.exit(0);
    } else if (arg.startsWith("-")) {
      die(`unknown flag ${arg}`);
    } else if (opts.widget == null) {
      opts.widget = arg;
    } else {
      die(`unexpected argument ${arg}`);
    }
  }

  if (!opts.all && !opts.widget) {
    die("give a widget id (e.g. `positions`) or --all");
  }
  if (!STATES.includes(opts.state)) {
    die(`--state must be one of ${STATES.join(", ")}`);
  }
  opts.base = opts.base.replace(/\/+$/, "");
  return opts;
}

async function loadPlaywright() {
  try {
    return (await import(join(FRONTEND, "node_modules", "playwright", "index.mjs")))
      .chromium;
  } catch {
    try {
      return (await import("playwright")).chromium;
    } catch {
      die(
        "playwright is not installed.\n" +
          "  fix: cd frontend && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm i -D playwright@1.61.0",
      );
    }
  }
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const chromium = await loadPlaywright();

  const target = opts.all
    ? `${opts.base}/dev/gallery?state=${opts.state}`
    : `${opts.base}/dev/gallery?widget=${encodeURIComponent(opts.widget)}&state=${opts.state}`;

  const selector = opts.all
    ? '[data-testid="gallery-sheet"]'
    : `[data-testid="widget-frame-${opts.widget}"]`;

  await mkdir(OUT_DIR, { recursive: true });
  const name = opts.all ? "_contact-sheet" : opts.widget;
  const outPath = join(
    OUT_DIR,
    `${name}-${opts.state}-${opts.viewport.width}x${opts.viewport.height}.png`,
  );

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: opts.viewport,
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });

  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" || msg.type() === "warning") {
      consoleErrors.push(`${msg.type()}: ${msg.text()}`);
    }
  });

  try {
    const response = await page
      .goto(target, { waitUntil: "domcontentloaded", timeout: 30_000 })
      .catch((err) => {
        // First line only — playwright appends a multi-line call log that
        // buries the one thing the caller needs: start the dev server.
        const reason = String(err.message).split("\n")[0];
        throw new Error(
          `could not reach ${target}\n  ${reason}\n` +
            `  fix: cd frontend && npm run dev -- -p ${new URL(opts.base).port || 3055}`,
        );
      });

    if (response && response.status() >= 400) {
      throw new Error(
        `${target} returned HTTP ${response.status()} — is this a dev build? ` +
          "/dev/gallery 404s in production on purpose.",
      );
    }

    await page.waitForSelector(selector, { timeout: 20_000 }).catch(() => {
      throw new Error(
        opts.all
          ? "the gallery contact sheet never rendered"
          : `no widget frame for "${opts.widget}" — is that id in frontend/lib/workspace/widget-registry.tsx?`,
      );
    });

    await page.waitForTimeout(opts.settle);

    const element = await page.$(selector);
    await element.screenshot({ path: outPath, animations: "disabled" });
  } catch (err) {
    await browser.close();
    die(err.message);
  }

  await browser.close();

  if (consoleErrors.length > 0) {
    process.stderr.write(
      `ui-shot: ${consoleErrors.length} console message(s) while rendering:\n` +
        consoleErrors.slice(0, 10).map((l) => `  ${l}\n`).join(""),
    );
  }

  process.stdout.write(`${outPath}\n`);
}

main();
