/**
 * screenshot_orderflow_stage5.mjs — Stage 5 Order Flow screenshot harness (REAL DATA)
 *
 * Takes TWO screenshots to exercise both a liquid call and a thin/overnight call:
 *   stage5_winner.png — winner call (liquid afternoon session): default load,
 *                        verifies footprint + DOM ladder + delta strip look correct.
 *   stage5_loser.png  — loser call (sparse ~2am overnight window with few trades):
 *                        verifies the robustness fixes don't crash on thin data.
 *
 * Relies on an already-running backend+frontend or starts them if needed.
 * Backend: Python minimal_orderflow_server.py on port 8011.
 * Frontend: Next.js production build on port 3005.
 *
 * Usage (from repo root):
 *   node frontend/scripts/screenshot_orderflow_stage5.mjs
 */

import { spawn }         from "child_process";
import { chromium }      from "playwright";
import path              from "path";
import { fileURLToPath } from "url";
import fs                from "fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT      = path.resolve(__dirname, "../..");
const FRONTEND  = path.resolve(__dirname, "..");

const WINNER_POST_ID = "1914389641555558635";
const LOSER_POST_ID  = "1914572914399080659";
const BACKEND_PORT   = 8011;
const FRONTEND_PORT  = 3005;
const API_URL        = `http://127.0.0.1:${BACKEND_PORT}`;

const SCREENSHOTS_DIR = path.join(ROOT, "data", "research", "_edgehunt", "screenshots");
const WINNER_PNG      = path.join(SCREENSHOTS_DIR, "stage5_winner.png");
const LOSER_PNG       = path.join(SCREENSHOTS_DIR, "stage5_loser.png");

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

/**
 * Fetch meta for a call. Returns null on failure (thin calls may 404 or error).
 */
async function fetchMeta(postId) {
  try {
    const res = await fetch(`${API_URL}/api/orderflow/${postId}/meta`, {
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
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

  // ── 1. Start Python backend on 8011 ──────────────────────────────────────
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

  // ── 2. Fetch meta for both calls ─────────────────────────────────────────
  const [winnerMeta, loserMeta] = await Promise.all([
    fetchMeta(WINNER_POST_ID),
    fetchMeta(LOSER_POST_ID),
  ]);

  if (winnerMeta) {
    console.log(`Winner meta: window ${new Date(winnerMeta.window_start).toISOString()} → ${new Date(winnerMeta.window_end).toISOString()}`);
  } else {
    console.warn("Could not fetch winner meta — will still render the page.");
  }

  if (loserMeta) {
    console.log(`Loser meta: window ${new Date(loserMeta.window_start).toISOString()} → ${new Date(loserMeta.window_end).toISOString()}`);
  } else {
    console.warn("Could not fetch loser meta — this call may be thin/sparse; will still attempt screenshot.");
  }

  // ── 3. Build Next.js with real backend URL ────────────────────────────────
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

  // ── 4. Start Next.js production server on port 3005 ──────────────────────
  console.log(`\nStarting Next.js on port ${FRONTEND_PORT}…`);
  frontend = spawnProcess("npx", ["next", "start", "-p", String(FRONTEND_PORT)], {
    cwd:   FRONTEND,
    shell: true,
  });

  console.log("Waiting for Next.js…");
  await waitForUrl(`http://localhost:${FRONTEND_PORT}/`, 60_000);
  console.log("Next.js ready.");
  await sleep(2_000);

  // ── 5. Open Playwright ────────────────────────────────────────────────────
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1600, height: 1100 });

  // ── 6. Screenshot A: winner call (default load) ──────────────────────────
  const winnerUrl = `http://localhost:${FRONTEND_PORT}/orderflow?call=${WINNER_POST_ID}`;
  console.log(`\nNavigating to winner: ${winnerUrl}`);
  await page.goto(winnerUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });

  console.log("Waiting for winner chart to render…");
  try {
    await page.waitForSelector("text=Chart (candles", { timeout: 180_000 });
    console.log("Winner chart card rendered.");
    await sleep(6_000);
  } catch {
    console.warn("Winner chart selector timed out — capturing current state.");
    await sleep(4_000);
  }

  await page.screenshot({ path: WINNER_PNG, fullPage: true });
  console.log(`Winner screenshot saved: ${WINNER_PNG}`);

  // ── 7. Screenshot B: loser call (thin/overnight — tests robustness) ──────
  const loserUrl = `http://localhost:${FRONTEND_PORT}/orderflow?call=${LOSER_POST_ID}`;
  console.log(`\nNavigating to loser (thin call): ${loserUrl}`);
  await page.goto(loserUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });

  console.log("Waiting for loser chart to render (may have sparse data)…");
  try {
    // For thin calls the chart may render quickly with "No book data in view" message —
    // use a shorter timeout and don't fail the harness if data is missing.
    await page.waitForSelector("text=Chart (candles", { timeout: 60_000 });
    console.log("Loser chart card rendered.");
    await sleep(5_000);
  } catch {
    console.warn("Loser chart selector timed out — this is expected for very thin calls.");
    await sleep(3_000);
  }

  await page.screenshot({ path: LOSER_PNG, fullPage: true });
  console.log(`Loser screenshot saved: ${LOSER_PNG}\n`);

} catch (err) {
  console.error("\nHarness error:", err?.message ?? err);
  process.exitCode = 1;
} finally {
  await cleanup();
}
