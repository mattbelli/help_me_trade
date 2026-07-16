/**
 * screenshot_footprint_2col.mjs — capture two-column footprint screenshots.
 *
 * Starts the minimal Python orderflow backend on port 8001, starts the
 * already-built Next.js production server (reuses the build in .next/), then
 * takes two screenshots:
 *   footprint_2col_voldelta.png  — default Vol×Delta mode (two-column: vol left, delta+bar right)
 *   footprint_2col_bidask.png    — Bid×Ask mode (bid left / ask right, aggressor view)
 *
 * Usage (from repo root, after `npm run build` in frontend/):
 *   node frontend/scripts/screenshot_footprint_2col.mjs
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
const VOLDELTA_PNG    = path.join(SCREENSHOTS_DIR, "footprint_2col_voldelta.png");
const BIDASK_PNG      = path.join(SCREENSHOTS_DIR, "footprint_2col_bidask.png");

const WINNER_POST_ID = "1914389641555558635";
const BACKEND_PORT   = 8001;
const FRONTEND_PORT  = 3009;
const API_URL        = `http://127.0.0.1:${BACKEND_PORT}`;
const BASE_URL       = `http://localhost:${FRONTEND_PORT}`;
const PAGE_URL       = `${BASE_URL}/orderflow?call=${WINNER_POST_ID}`;

async function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function waitForUrl(url, timeoutMs = 90_000, intervalMs = 1500) {
  const deadline = Date.now() + timeoutMs;
  let lastErr = null;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(3000) });
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

  // ── 1. Start Python backend on port 8001 ──────────────────────────────────
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

  console.log("Waiting for backend health…");
  await waitForUrl(`${API_URL}/health`, 60_000);
  console.log("Backend ready.");

  // ── 2. Start Next.js production server (uses existing .next/ build) ───────
  console.log(`\nStarting Next.js production server on port ${FRONTEND_PORT}…`);
  frontend = spawnProcess("npx", ["next", "start", "-p", String(FRONTEND_PORT)], {
    cwd:   FRONTEND,
    shell: true,
    env:   {
      ...process.env,
      NEXT_PUBLIC_API_URL: API_URL,
    },
  });

  console.log("Waiting for Next.js…");
  await waitForUrl(`${BASE_URL}/`, 60_000);
  console.log("Next.js ready.");
  await sleep(1_500);

  // ── 3. Open browser and navigate ──────────────────────────────────────────
  console.log(`\nOpening ${PAGE_URL}`);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1600, height: 1100 });

  await page.goto(PAGE_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });

  // Wait for the canvas to render (footprint canvas appears after data loads)
  console.log("Waiting for footprint canvas…");
  try {
    // The footprint canvas is inside a div with the orderflow chart
    await page.waitForSelector("canvas", { timeout: 120_000 });
    console.log("Canvas found.");
    await sleep(5_000); // let all draws settle
  } catch {
    console.warn("Canvas selector timed out — capturing current state.");
    await sleep(3_000);
  }

  // ── 4. Screenshot A: Vol×Delta mode (default) ─────────────────────────────
  // Vol×Delta is the default mode — no click needed.
  // Hover the chart center so the crosshair is active.
  await page.mouse.move(800, 400);
  await sleep(600);

  await page.screenshot({ path: VOLDELTA_PNG, fullPage: true });
  const voldeltaStat = fs.statSync(VOLDELTA_PNG);
  console.log(`\nVol×Delta screenshot: ${VOLDELTA_PNG} (${voldeltaStat.size} bytes)`);

  // ── 5. Switch to Bid×Ask mode, then screenshot B ──────────────────────────
  console.log("\nSwitching to Bid×Ask mode…");
  // Click the "Bid×Ask" button in the mode toggle bar
  try {
    await page.click("button:has-text('Bid×Ask')", { timeout: 5_000 });
    await sleep(800); // let canvas redraw
  } catch (e) {
    console.warn("Could not click Bid×Ask button:", e.message);
    // Try by text content match
    try {
      const btn = page.locator("text=Bid×Ask").first();
      await btn.click({ timeout: 3_000 });
      await sleep(800);
    } catch (e2) {
      console.warn("Fallback click also failed:", e2.message);
    }
  }

  await page.mouse.move(800, 400);
  await sleep(600);

  await page.screenshot({ path: BIDASK_PNG, fullPage: true });
  const bidaskStat = fs.statSync(BIDASK_PNG);
  console.log(`Bid×Ask screenshot: ${BIDASK_PNG} (${bidaskStat.size} bytes)\n`);

  if (voldeltaStat.size < 50_000) console.warn(`WARNING: Vol×Delta screenshot may be too small (${voldeltaStat.size}B < 50KB).`);
  if (bidaskStat.size  < 50_000) console.warn(`WARNING: Bid×Ask screenshot may be too small (${bidaskStat.size}B < 50KB).`);

  console.log("Done.");

} catch (err) {
  console.error("\nHarness error:", err?.message ?? err);
  process.exitCode = 1;
} finally {
  await cleanup();
}
