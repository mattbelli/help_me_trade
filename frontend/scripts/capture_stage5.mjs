/**
 * capture_stage5.mjs — Stage 5 Order Flow screenshot harness (REAL DATA)
 *
 * Starts minimal Python orderflow backend on a free port, builds Next.js
 * with NEXT_PUBLIC_API_URL pointing to that backend, starts Next.js on
 * another free port, then captures two screenshots:
 *   stage5_winner.png  — /orderflow?call=1914389641555558635
 *   stage5_loser.png   — /orderflow?call=1914572914399080659 (thin overnight)
 *
 * Run from repo root:
 *   node frontend/scripts/capture_stage5.mjs <bport> <fport>
 * or from frontend/:
 *   node scripts/capture_stage5.mjs <bport> <fport>
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
const WINNER_PNG = path.join(SCREENSHOTS_DIR, "stage5_winner.png");
const LOSER_PNG  = path.join(SCREENSHOTS_DIR, "stage5_loser.png");

const WINNER_CALL_ID = "1914389641555558635";
const LOSER_CALL_ID  = "1914572914399080659";

const BACKEND_PORT  = parseInt(process.argv[2] ?? "8001", 10);
const FRONTEND_PORT = parseInt(process.argv[3] ?? "8002", 10);
const API_URL       = `http://127.0.0.1:${BACKEND_PORT}`;
const FRONTEND_BASE = `http://localhost:${FRONTEND_PORT}`;

console.log(`Using backend port:  ${BACKEND_PORT}`);
console.log(`Using frontend port: ${FRONTEND_PORT}`);
console.log(`ROOT:                ${ROOT}`);
console.log(`FRONTEND:            ${FRONTEND}`);

// ── Helpers ────────────────────────────────────────────────────────────────────

async function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function waitForUrl(url, timeoutMs = 180_000, intervalMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  let lastErr = null;
  console.log(`  Polling ${url} …`);
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(4000) });
      if (res.status < 600) {
        console.log(`  Ready (${res.status})`);
        return;
      }
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
      else reject(new Error(`${cmd} ${args.join(" ")} exited with code ${code}`));
    });
  });
}

async function screenshotCall(context, callId, outPath, label) {
  const url = `${FRONTEND_BASE}/orderflow?call=${callId}`;
  console.log(`\n  Navigating to ${url} …`);

  const page = await context.newPage();

  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(`[pageerror] ${err.message}`));

  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
  } catch (e) {
    console.error(`  Navigation failed: ${e.message}`);
  }

  // Wait for canvas element to appear (footprint chart)
  let rendered = false;
  try {
    await page.waitForSelector("canvas", { timeout: 30_000 });
    console.log("  Canvas found — waiting 3s for drawing to complete…");
    rendered = true;
    await sleep(3_000);
  } catch {
    console.warn("  No <canvas> found within 30s — capturing current state.");
    await sleep(2_000);
  }

  // Check for React/Next.js error overlay
  const hasErrorOverlay = await page.evaluate(() => {
    return !!(document.querySelector("nextjs-portal") ||
              document.querySelector("[data-nextjs-dialog-overlay]") ||
              document.querySelector("#__NEXT_ERROR__"));
  });
  if (hasErrorOverlay) {
    console.error(`  !! React/Next.js error overlay detected for ${label}!`);
  }

  await page.screenshot({ path: outPath, fullPage: true });
  await page.close();

  const stat = fs.statSync(outPath);
  const sizeMB = (stat.size / 1024 / 1024).toFixed(2);
  console.log(`  Saved: ${path.basename(outPath)} (${stat.size} bytes / ${sizeMB} MB)`);

  if (consoleErrors.length) {
    console.warn(`  Browser console errors (${consoleErrors.length}):`);
    for (const e of consoleErrors.slice(0, 10)) {
      console.warn(`    ${e}`);
    }
  }

  return { rendered, hasErrorOverlay, consoleErrors, size: stat.size };
}

// ── Main ───────────────────────────────────────────────────────────────────────

let backend  = null;
let frontend = null;
let browser  = null;

async function cleanup() {
  console.log("\nCleaning up servers…");
  if (browser)  { try { await browser.close(); } catch {} }
  if (frontend) { try { frontend.kill("SIGTERM"); } catch {} }
  if (backend)  { try { backend.kill("SIGTERM");  } catch {} }
  await sleep(500);
}

try {
  fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

  // ── 1. Start Python backend ──────────────────────────────────────────────────
  console.log(`\n── 1. Starting orderflow backend on port ${BACKEND_PORT} ──`);
  backend = spawnProcess("python", ["scripts/minimal_orderflow_server.py"], {
    cwd:   ROOT,
    shell: true,
    env:   {
      ...process.env,
      ORDERFLOW_SERVER_PORT: String(BACKEND_PORT),
      PYTHONIOENCODING:      "utf-8",
    },
  });

  await waitForUrl(`${API_URL}/health`, 60_000);

  // ── 2. Verify real data ──────────────────────────────────────────────────────
  console.log("\n── 2. Verifying real data from backend ──");

  const callsRes = await fetch(`${API_URL}/api/orderflow/calls`);
  const callsText = await callsRes.text();
  console.log(`  GET /api/orderflow/calls → ${callsRes.status}  body_length=${callsText.length}`);
  if (!callsRes.ok) throw new Error(`Backend /api/orderflow/calls returned ${callsRes.status}`);

  const metaRes = await fetch(`${API_URL}/api/orderflow/${WINNER_CALL_ID}/meta`);
  const metaText = await metaRes.text();
  console.log(`  GET /api/orderflow/${WINNER_CALL_ID}/meta → ${metaRes.status}  body_length=${metaText.length}`);
  if (!metaRes.ok) throw new Error(`Backend meta endpoint returned ${metaRes.status}: ${metaText.slice(0, 200)}`);
  const meta = JSON.parse(metaText);
  console.log(`  Meta keys: ${Object.keys(meta).join(", ")}`);

  // ── 3. Build Next.js ─────────────────────────────────────────────────────────
  console.log(`\n── 3. Building Next.js with NEXT_PUBLIC_API_URL=${API_URL} ──`);
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

  // ── 4. Start Next.js production server ───────────────────────────────────────
  console.log(`\n── 4. Starting Next.js on port ${FRONTEND_PORT} ──`);
  frontend = spawnProcess("npx", ["next", "start", "-p", String(FRONTEND_PORT)], {
    cwd:   FRONTEND,
    shell: true,
    env:   {
      ...process.env,
      NEXT_PUBLIC_API_URL:   API_URL,
      NEXT_PUBLIC_API_TOKEN: process.env.NEXT_PUBLIC_API_TOKEN ?? "",
    },
  });

  await waitForUrl(FRONTEND_BASE, 60_000);
  console.log("Next.js ready.");
  await sleep(2_000);

  // ── 5. Launch Playwright ──────────────────────────────────────────────────────
  console.log("\n── 5. Launching Playwright chromium ──");
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } });

  // ── 6. Screenshot winner ──────────────────────────────────────────────────────
  console.log("\n── 6. Capturing winner (1914389641555558635) ──");
  const winnerResult = await screenshotCall(context, WINNER_CALL_ID, WINNER_PNG, "winner");

  // ── 7. Screenshot loser ───────────────────────────────────────────────────────
  console.log("\n── 7. Capturing loser (1914572914399080659) ──");
  const loserResult = await screenshotCall(context, LOSER_CALL_ID, LOSER_PNG, "loser");

  // ── 8. Summary ────────────────────────────────────────────────────────────────
  console.log("\n══ FINAL RESULTS ══════════════════════════════");
  const winnerOk = fs.existsSync(WINNER_PNG) && fs.statSync(WINNER_PNG).size > 50_000;
  const loserOk  = fs.existsSync(LOSER_PNG)  && fs.statSync(LOSER_PNG).size  > 50_000;

  console.log(`  stage5_winner.png : ${fs.existsSync(WINNER_PNG) ? fs.statSync(WINNER_PNG).size + " bytes" : "MISSING"} — ${winnerOk ? "PASS (>50KB)" : "FAIL"}`);
  console.log(`  stage5_loser.png  : ${fs.existsSync(LOSER_PNG)  ? fs.statSync(LOSER_PNG).size  + " bytes" : "MISSING"} — ${loserOk  ? "PASS (>50KB)" : "FAIL"}`);
  console.log(`  Winner canvas rendered : ${winnerResult.rendered}`);
  console.log(`  Loser  canvas rendered : ${loserResult.rendered}`);
  console.log(`  Winner error overlay   : ${winnerResult.hasErrorOverlay}`);
  console.log(`  Loser  error overlay   : ${loserResult.hasErrorOverlay}`);

  if (winnerResult.consoleErrors.length) {
    console.warn(`  Winner browser errors: ${winnerResult.consoleErrors.slice(0, 5).join(" | ")}`);
  }
  if (loserResult.consoleErrors.length) {
    console.warn(`  Loser  browser errors: ${loserResult.consoleErrors.slice(0, 5).join(" | ")}`);
  }

  if (!winnerOk || !loserOk) process.exitCode = 1;

} catch (err) {
  console.error("\nHarness error:", err?.message ?? err);
  if (err?.stack) console.error(err.stack);
  process.exitCode = 1;
} finally {
  await cleanup();
}
