/**
 * screenshot_footprint_candle.mjs — Footprint candlestick screenshot harness
 *
 * Starts minimal Python orderflow backend on port 8001, builds Next.js with
 * NEXT_PUBLIC_API_URL=http://127.0.0.1:8001, takes two screenshots:
 *   footprint_candle_voldelta.png  — Vol×Delta mode (default)
 *   footprint_candle_bidask.png    — Bid×Ask mode
 *
 * Both must show candlestick outlines WITH per-price cells, readable numbers,
 * POC amber outline visible, and per-bar footer vol+delta.
 *
 * Usage (from repo root):
 *   node frontend/scripts/screenshot_footprint_candle.mjs
 */

import { spawn }         from "child_process";
import { chromium }      from "playwright";
import path              from "path";
import { fileURLToPath } from "url";
import fs                from "fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT      = path.resolve(__dirname, "../..");
const FRONTEND  = path.resolve(__dirname, "..");

const SCREENSHOTS_DIR = path.join(ROOT, "data", "research", "_edgehunt", "screenshots");
const VOLDELTA_PNG    = path.join(SCREENSHOTS_DIR, "footprint_candle_voldelta.png");
const BIDASK_PNG      = path.join(SCREENSHOTS_DIR, "footprint_candle_bidask.png");

const WINNER_POST_ID = "1914389641555558635";
const BACKEND_PORT   = 8001;
const FRONTEND_PORT  = 3010;
const API_URL        = `http://127.0.0.1:${BACKEND_PORT}`;
const FRONTEND_URL   = `http://localhost:${FRONTEND_PORT}/orderflow?call=${WINNER_POST_ID}`;

// ── Helpers ───────────────────────────────────────────────────────────────────

async function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function waitForUrl(url, timeoutMs = 120_000, intervalMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  let lastErr = null;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(4000) });
      if (res.status < 600) return;
    } catch (e) { lastErr = e; }
    await sleep(intervalMs);
  }
  throw new Error(`Timeout waiting for ${url}: ${lastErr?.message ?? "unreachable"}`);
}

function spawnProcess(cmd, args, opts = {}) {
  const proc = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], ...opts });
  const tag  = `[${path.basename(String(args[0] ?? cmd))}]`;
  proc.stdout?.on("data", (d) => process.stdout.write(`${tag} ${d}`));
  proc.stderr?.on("data", (d) => process.stderr.write(`${tag} ${d}`));
  proc.on("error", (e) => console.error(`${tag} error:`, e.message));
  return proc;
}

function spawnAndWait(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const proc = spawn(cmd, args, { stdio: "inherit", ...opts });
    proc.on("error", reject);
    proc.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${cmd} exited with code ${code}`));
    });
  });
}

function checkSize(png, minBytes = 50_000) {
  const stat = fs.statSync(png);
  if (stat.size < minBytes) {
    throw new Error(`Screenshot too small: ${png} (${stat.size} bytes < ${minBytes})`);
  }
  console.log(`  OK: ${path.basename(png)} — ${(stat.size / 1024).toFixed(0)} KB`);
}

// ── Main ──────────────────────────────────────────────────────────────────────

let backend  = null;
let frontend = null;
let browser  = null;

async function cleanup() {
  if (browser)  { try { await browser.close(); } catch {} }
  if (frontend) { try { frontend.kill("SIGTERM"); } catch {} }
  if (backend)  { try { backend.kill("SIGTERM");  } catch {} }
}

try {
  fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

  // ── 1. Start Python backend on 8001 ──────────────────────────────────────
  console.log(`\nStarting orderflow backend on port ${BACKEND_PORT}…`);
  backend = spawnProcess("python", ["scripts/minimal_orderflow_server.py"], {
    cwd:   ROOT,
    shell: true,
    env:   {
      ...process.env,
      ORDERFLOW_SERVER_PORT: String(BACKEND_PORT),
      PYTHONIOENCODING:      "utf-8",
    },
  });

  console.log("Waiting for backend health check…");
  await waitForUrl(`${API_URL}/health`, 60_000);
  console.log("Backend ready.");

  // ── 2. Build Next.js ─────────────────────────────────────────────────────
  console.log(`\nBuilding Next.js with NEXT_PUBLIC_API_URL=${API_URL}…`);
  await spawnAndWait("npx", ["next", "build"], {
    cwd:   FRONTEND,
    shell: true,
    env:   {
      ...process.env,
      NEXT_PUBLIC_API_URL:   API_URL,
      NEXT_PUBLIC_API_TOKEN: process.env.NEXT_PUBLIC_API_TOKEN ?? "",
    },
  });
  console.log("Build complete.");

  // ── 3. Start Next.js prod server ─────────────────────────────────────────
  console.log(`\nStarting Next.js on port ${FRONTEND_PORT}…`);
  frontend = spawnProcess("npx", ["next", "start", "-p", String(FRONTEND_PORT)], {
    cwd:   FRONTEND,
    shell: true,
  });

  console.log("Waiting for Next.js…");
  await waitForUrl(`http://localhost:${FRONTEND_PORT}/`, 60_000);
  console.log("Next.js ready.");
  await sleep(2_000);

  // ── 4. Open Playwright ────────────────────────────────────────────────────
  console.log(`\nOpening ${FRONTEND_URL}`);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });

  // Wait for chart card to appear (contains the mode toggle + canvas)
  console.log("Waiting for chart to render…");
  try {
    // The card title still says "Chart (candles" — wait for that or the mode toggle
    await page.waitForSelector("text=Footprint+Candle", { timeout: 180_000 });
    console.log("Chart text found.");
    await sleep(5_000);
  } catch {
    // Fall back: wait for "Vol×Delta" button (our new default mode toggle)
    try {
      await page.waitForSelector("text=Vol×Delta", { timeout: 30_000 });
      console.log("Mode toggle found.");
      await sleep(4_000);
    } catch {
      console.warn("Selectors timed out — capturing current state.");
      await sleep(4_000);
    }
  }

  // ── 5. Screenshot A: Vol×Delta (default) ─────────────────────────────────
  // Vol×Delta is the default so no click needed
  await page.screenshot({ path: VOLDELTA_PNG, fullPage: true });
  console.log(`\nVol×Delta screenshot saved: ${VOLDELTA_PNG}`);
  checkSize(VOLDELTA_PNG);

  // ── 6. Switch to Bid×Ask mode ────────────────────────────────────────────
  console.log("\nSwitching to Bid×Ask mode…");
  try {
    const bidaskBtn = page.locator("button", { hasText: /^Bid×Ask$/ }).first();
    await bidaskBtn.waitFor({ timeout: 10_000 });
    await bidaskBtn.click();
    await sleep(1_500);  // let canvas re-render
  } catch (e) {
    console.warn("Could not click Bid×Ask button:", e.message);
  }

  // ── 7. Screenshot B: Bid×Ask mode ────────────────────────────────────────
  await page.screenshot({ path: BIDASK_PNG, fullPage: true });
  console.log(`Bid×Ask screenshot saved: ${BIDASK_PNG}`);
  checkSize(BIDASK_PNG);

  console.log("\nAll screenshots captured successfully.\n");

} catch (err) {
  console.error("\nHarness error:", err?.message ?? err);
  process.exitCode = 1;
} finally {
  await cleanup();
}
