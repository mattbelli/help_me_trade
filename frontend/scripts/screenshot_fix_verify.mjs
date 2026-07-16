/**
 * screenshot_fix_verify.mjs — Post-fix verification screenshots.
 *
 * Captures two screenshots to data/research/_edgehunt/screenshots/:
 *   fix_winner_long.png  — winner (long) call: must show LONG/green/up arrow.
 *   fix_short.png        — a short call: must show SHORT/red/down arrow.
 *
 * Prerequisites: frontend must already be built with NEXT_PUBLIC_API_URL
 * pointing to BACKEND_PORT.  Run `npm run build` in frontend/ first, or
 * use the full screenshot_orderflow.mjs which includes the build step.
 *
 * Usage (from repo root):
 *   node frontend/scripts/screenshot_fix_verify.mjs
 */

import { spawn }         from "child_process";
import { chromium }      from "playwright";
import path              from "path";
import { fileURLToPath } from "url";
import fs                from "fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT      = path.resolve(__dirname, "../..");
const FRONTEND  = path.resolve(ROOT, "frontend");

const SCREENSHOTS_DIR = path.join(ROOT, "data", "research", "_edgehunt", "screenshots");
const WINNER_PNG      = path.join(SCREENSHOTS_DIR, "fix_winner_long.png");
const SHORT_PNG       = path.join(SCREENSHOTS_DIR, "fix_short.png");

const WINNER_POST_ID = "1914389641555558635";
// First short call with MBO data (2025-04-22 short @ 5240)
const SHORT_POST_ID  = "1914572914399080659";

const BACKEND_PORT  = 8009;
const FRONTEND_PORT = 3009;
const API_URL       = `http://127.0.0.1:${BACKEND_PORT}`;

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
    } catch (e) {
      lastErr = e;
    }
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

async function screenshotCall(page, postId, outPath, label) {
  const url = `http://localhost:${FRONTEND_PORT}/orderflow?call=${postId}`;
  console.log(`\n[${label}] Navigating to ${url}`);
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });

  console.log(`[${label}] Waiting for chart to render…`);
  try {
    await page.waitForSelector("text=Chart (candles", { timeout: 180_000 });
    await sleep(5_000);
  } catch {
    console.warn(`[${label}] Chart selector timed out — capturing current state.`);
    await sleep(3_000);
  }

  await page.screenshot({ path: outPath, fullPage: true });
  const stat = fs.statSync(outPath);
  console.log(`[${label}] Saved: ${outPath} (${(stat.size / 1024).toFixed(0)} KB)`);
  return stat.size;
}

// ── Main ──────────────────────────────────────────────────────────────────────

let backend  = null;
let frontend = null;
let browser  = null;

async function cleanup() {
  if (browser)  { try { await browser.close(); } catch {} }
  if (frontend) { try { frontend.kill(); } catch {} }
  if (backend)  { try { backend.kill();  } catch {} }
}

try {
  fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

  // ── 1. Start Python backend on port 8001 ──────────────────────────────────
  console.log(`\nStarting orderflow backend on port ${BACKEND_PORT}…`);
  backend = spawnProcess(
    "python",
    [path.join(ROOT, "scripts", "minimal_orderflow_server.py")],
    {
      cwd: ROOT,
      env: {
        ...process.env,
        ORDERFLOW_SERVER_PORT: String(BACKEND_PORT),
        PYTHONIOENCODING:      "utf-8",
      },
    },
  );

  await waitForUrl(`${API_URL}/health`, 60_000);
  console.log("Backend ready.");

  // ── 2. Start Next.js (uses already-built .next with port 8001 baked in) ──
  // Invoke next/dist/bin/next directly so we don't need shell:true or .cmd wrappers.
  console.log(`\nStarting Next.js on port ${FRONTEND_PORT}…`);
  const nextBin = path.join(FRONTEND, "node_modules", "next", "dist", "bin", "next");
  frontend = spawnProcess(
    process.execPath,   // same node binary already running
    [nextBin, "start", "-p", String(FRONTEND_PORT)],
    { cwd: FRONTEND },
  );

  await waitForUrl(`http://localhost:${FRONTEND_PORT}/`, 60_000);
  console.log("Next.js ready.");
  await sleep(2_000);

  // ── 3. Open Playwright ────────────────────────────────────────────────────
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1600, height: 1100 });

  // ── 4. Winner (LONG) screenshot ───────────────────────────────────────────
  const winnerSize = await screenshotCall(page, WINNER_POST_ID, WINNER_PNG, "WINNER/LONG");

  // ── 5. Short call screenshot ──────────────────────────────────────────────
  const shortSize = await screenshotCall(page, SHORT_POST_ID, SHORT_PNG, "SHORT");

  // ── 6. Result summary ─────────────────────────────────────────────────────
  console.log("\n=== RESULT ===");
  console.log(`fix_winner_long.png : ${(winnerSize / 1024).toFixed(0)} KB`);
  console.log(`fix_short.png       : ${(shortSize  / 1024).toFixed(0)} KB`);

  const pass = winnerSize >= 50_000 && shortSize >= 50_000;
  if (!pass) {
    if (winnerSize < 50_000) console.warn("WARNING: fix_winner_long.png < 50 KB");
    if (shortSize  < 50_000) console.warn("WARNING: fix_short.png < 50 KB");
    process.exitCode = 1;
  } else {
    console.log("Both screenshots > 50 KB — PASS");
  }

} catch (err) {
  console.error("\nHarness error:", err?.message ?? err);
  process.exitCode = 1;
} finally {
  await cleanup();
}
