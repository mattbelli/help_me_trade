/**
 * screenshot_orderflow.mjs — Stage 4 Order Flow screenshot harness (REAL DATA)
 *
 * Starts the minimal Python orderflow backend on port 8011, builds Next.js
 * with NEXT_PUBLIC_API_URL=http://127.0.0.1:8011, then takes TWO screenshots:
 *   stage4_default.png  — default load: readable footprint + scrubber + selector visible.
 *   stage4_scrubbed.png — after moving scrubber to ~entry time: crosshair across panes
 *                         + DOM ladder updated to that snapshot + crosshair readout shown.
 *
 * Usage (from repo root):
 *   node frontend/scripts/screenshot_orderflow.mjs
 */

import { spawn }          from "child_process";
import { chromium }       from "playwright";
import path               from "path";
import { fileURLToPath }  from "url";
import fs                 from "fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT      = path.resolve(__dirname, "../..");
const FRONTEND  = path.resolve(__dirname, "..");

const SCREENSHOTS_DIR = path.join(ROOT, "data", "research", "_edgehunt", "screenshots");
const DEFAULT_PNG     = path.join(SCREENSHOTS_DIR, "stage4_default.png");
const SCRUBBED_PNG    = path.join(SCREENSHOTS_DIR, "stage4_scrubbed.png");

const WINNER_POST_ID = "1914389641555558635";
const BACKEND_PORT   = 8011;
const FRONTEND_PORT  = 3005;
const API_URL        = `http://127.0.0.1:${BACKEND_PORT}`;
const FRONTEND_URL   = `http://localhost:${FRONTEND_PORT}/orderflow?call=${WINNER_POST_ID}`;

// Footprint pane center (for crosshair hover): candle=180px, footprint=520px → center ~180+260=440
const FP_CENTER_X = 800;
const FP_CENTER_Y = 440;

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

  // ── 2. Fetch meta for the winner call (to get entry_ts for scrubber) ─────
  let entryTs = null;
  let windowStart = null;
  let windowEnd = null;
  try {
    const metaRes = await fetch(`${API_URL}/api/orderflow/${WINNER_POST_ID}/meta`);
    if (metaRes.ok) {
      const meta = await metaRes.json();
      entryTs     = meta.entry_ts;
      windowStart = meta.window_start;
      windowEnd   = meta.window_end;
      console.log(`Meta: window ${new Date(windowStart).toISOString()} → ${new Date(windowEnd).toISOString()}`);
      if (entryTs) console.log(`Entry time: ${new Date(entryTs).toISOString()}`);
    }
  } catch (e) {
    console.warn("Could not fetch meta:", e.message);
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

  // ── 5. Open Playwright and navigate ──────────────────────────────────────
  console.log(`\nOpening ${FRONTEND_URL}`);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1600, height: 1100 });

  await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });

  // Wait for chart to render
  console.log("Waiting for canvas chart to render…");
  try {
    await page.waitForSelector("text=Chart (candles", { timeout: 180_000 });
    console.log("Chart card rendered.");
    await sleep(6_000);
  } catch {
    console.warn("Chart selector timed out — capturing current state.");
    await sleep(4_000);
  }

  // ── 6. Screenshot A: default load ────────────────────────────────────────
  await page.screenshot({ path: DEFAULT_PNG, fullPage: true });
  console.log(`\nDefault screenshot saved: ${DEFAULT_PNG}`);

  // ── 7. Move scrubber to ~entry time, then hover canvas for crosshair ─────
  //       Target scrubber value: entry_ts if available, else 40% of window
  const scrubberTarget = entryTs ?? (windowStart && windowEnd
    ? windowStart + (windowEnd - windowStart) * 0.4
    : null);

  if (scrubberTarget && windowStart && windowEnd) {
    console.log(`\nMoving scrubber to entry time (~${new Date(scrubberTarget).toISOString()})…`);
    const scrubber = page.locator('input[type="range"]').first();

    try {
      await scrubber.waitFor({ timeout: 5_000 });
      await scrubber.evaluate(
        (el, { min, max, target }) => {
          const clamped = Math.max(min, Math.min(max, target));
          el.value = String(Math.round(clamped));
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        },
        { min: windowStart, max: windowEnd, target: scrubberTarget },
      );
      await sleep(1_500); // let React update DOM ladder + crosshair

      // Also hover the footprint canvas center so we get a crosshair + readout
      await page.mouse.move(FP_CENTER_X, FP_CENTER_Y);
      await sleep(800);
    } catch (e) {
      console.warn("Scrubber interaction failed:", e.message);
    }
  } else {
    // Fallback: just hover the canvas center
    console.log("\nNo meta window bounds — hovering canvas for crosshair…");
    await page.mouse.move(FP_CENTER_X, FP_CENTER_Y);
    await sleep(1_000);
  }

  // ── 8. Screenshot B: scrubbed view with crosshair ────────────────────────
  await page.screenshot({ path: SCRUBBED_PNG, fullPage: true });
  console.log(`Scrubbed screenshot saved: ${SCRUBBED_PNG}\n`);

} catch (err) {
  console.error("\nHarness error:", err?.message ?? err);
  process.exitCode = 1;
} finally {
  await cleanup();
}
