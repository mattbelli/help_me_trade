/**
 * screenshot_footprint_candle_fast.mjs — uses existing .next build (no rebuild).
 * Starts backend on 8001, prod server on 3010, shoots Vol×Delta + Bid×Ask.
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

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

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

function checkSize(png, minBytes = 50_000) {
  const stat = fs.statSync(png);
  if (stat.size < minBytes) throw new Error(`Too small: ${png} (${stat.size} bytes)`);
  console.log(`  OK: ${path.basename(png)} — ${(stat.size/1024).toFixed(0)} KB`);
}

let backend = null, frontend = null, browser = null;

async function cleanup() {
  if (browser)  { try { await browser.close(); } catch {} }
  if (frontend) { try { frontend.kill("SIGTERM"); } catch {} }
  if (backend)  { try { backend.kill("SIGTERM");  } catch {} }
}

try {
  fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

  // 1. Start backend on 8001
  console.log(`\nStarting backend on port ${BACKEND_PORT}…`);
  backend = spawnProcess("python", ["scripts/minimal_orderflow_server.py"], {
    cwd:   ROOT, shell: true,
    env:   { ...process.env, ORDERFLOW_SERVER_PORT: String(BACKEND_PORT), PYTHONIOENCODING: "utf-8" },
  });
  await waitForUrl(`${API_URL}/health`, 60_000);
  console.log("Backend ready.");

  // 2. Start Next.js prod server (uses existing .next build)
  console.log(`\nStarting Next.js on port ${FRONTEND_PORT}…`);
  frontend = spawnProcess("npx", ["next", "start", "-p", String(FRONTEND_PORT)], {
    cwd: FRONTEND, shell: true,
  });
  await waitForUrl(`http://localhost:${FRONTEND_PORT}/`, 60_000);
  console.log("Next.js ready.");
  await sleep(2_000);

  // 3. Playwright
  console.log(`\nOpening ${FRONTEND_URL}`);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });

  // Wait for chart data to load — look for the canvas or mode toggle
  console.log("Waiting for chart…");
  let chartReady = false;
  for (const sel of ["text=Vol×Delta", "text=Footprint+Candle", "canvas"]) {
    try {
      await page.waitForSelector(sel, { timeout: 180_000 });
      console.log(`Found: ${sel}`);
      chartReady = true;
      break;
    } catch {}
  }
  if (!chartReady) console.warn("Chart selector timeout — proceeding.");
  await sleep(6_000);  // let canvas paint complete

  // 4. Screenshot A — Vol×Delta (default mode, no click needed)
  await page.screenshot({ path: VOLDELTA_PNG, fullPage: true });
  console.log(`\nVol×Delta: ${VOLDELTA_PNG}`);
  checkSize(VOLDELTA_PNG);

  // 5. Click Bid×Ask
  console.log("\nSwitching to Bid×Ask…");
  try {
    // Try exact text first, fall back to partial
    const btn = page.locator("button").filter({ hasText: /Bid.Ask/ }).first();
    await btn.waitFor({ timeout: 10_000 });
    await btn.click();
    await sleep(2_000);
  } catch (e) { console.warn("Could not switch mode:", e.message); }

  // 6. Screenshot B — Bid×Ask
  await page.screenshot({ path: BIDASK_PNG, fullPage: true });
  console.log(`Bid×Ask: ${BIDASK_PNG}`);
  checkSize(BIDASK_PNG);

  console.log("\nDone.\n");

} catch (err) {
  console.error("\nError:", err?.message ?? err);
  process.exitCode = 1;
} finally {
  await cleanup();
}
