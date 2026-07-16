/**
 * capture_stage5.mjs — Stage 5 Order Flow screenshot harness (REAL DATA)
 *
 * Starts minimal Python orderflow backend on a free port, builds Next.js
 * with NEXT_PUBLIC_API_URL pointing to that backend, starts Next.js on
 * another free port, then captures two screenshots:
 *   stage5_winner.png  — /orderflow?call=1914389641555558635
 *   stage5_loser.png   — /orderflow?call=1914572914399080659 (thin overnight)
 *
 * Ports are passed as command-line args: node capture_stage5.mjs <bport> <fport>
 * e.g.  node scripts/capture_stage5.mjs 8001 8002
 */

import { spawn }         from "child_process";
import { chromium }      from "playwright";
import path              from "path";
import { fileURLToPath } from "url";
import fs                from "fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT      = path.resolve(__dirname, "..");
const FRONTEND  = path.resolve(ROOT, "frontend");

const SCREENSHOTS_DIR = path.join(ROOT, "data", "research", "_edgehunt", "screenshots");
const WINNER_PNG = path.join(SCREENSHOTS_DIR, "stage5_winner.png");
const LOSER_PNG  = path.join(SCREENSHOTS_DIR, "stage5_loser.png");

const WINNER_CALL_ID = "1914389641555558635";
const LOSER_CALL_ID  = "1914572914399080659";

const BACKEND_PORT  = parseInt(process.argv[2] ?? "8001", 10);
const FRONTEND_PORT = parseInt(process.argv[3] ?? "8002", 10);
const API_URL       = `http://127.0.0.1:${BACKEND_PORT}`;
const FRONTEND_BASE = `http://localhost:${FRONTEND_PORT}`;

console.log(`Using backend port: ${BACKEND_PORT}`);
console.log(`Using frontend port: ${FRONTEND_PORT}`);

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
        console.log(`  OK (${res.status})`);
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

async function screenshotCall(page, callId, outPath) {
  const url = `${FRONTEND_BASE}/orderflow?call=${callId}`;
  console.log(`\nNavigating to ${url} …`);

  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(`[pageerror] ${err.message}`));

  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });

  // Wait for footprint canvas or chart to render
  let rendered = false;
  try {
    await page.waitForSelector("canvas", { timeout: 30_000 });
    console.log("  Canvas found.");
    rendered = true;
  } catch {
    console.warn("  No <canvas> found — capturing whatever is on screen.");
  }

  // Additional wait for drawing to complete
  await sleep(3_000);

  // Check for React error overlay
  const errorOverlay = await page.$("nextjs-portal") ?? await page.$("[data-nextjs-dialog-overlay]");
  if (errorOverlay) {
    console.error("  React/Next.js error overlay detected!");
  }

  await page.screenshot({ path: outPath, fullPage: true });
  const stat = fs.statSync(outPath);
  console.log(`  Screenshot saved: ${outPath} (${stat.size} bytes)`);

  if (consoleErrors.length) {
    console.warn(`  Browser console errors (${consoleErrors.length}):`);
    for (const e of consoleErrors.slice(0, 10)) {
      console.warn(`    ${e}`);
    }
  }

  return { rendered, consoleErrors, size: stat.size };
}

// ── Main ───────────────────────────────────────────────────────────────────────

let backend  = null;
let frontend = null;
let browser  = null;

async function cleanup() {
  console.log("\nCleaning up…");
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
  console.log("Backend ready.\n");

  // ── 2. Verify real data ──────────────────────────────────────────────────────
  console.log("── 2. Verifying real data ──");
  const callsRes = await fetch(`${API_URL}/api/orderflow/calls`);
  console.log(`  GET /api/orderflow/calls → ${callsRes.status}`);
  if (!callsRes.ok) throw new Error("Backend /api/orderflow/calls returned non-200");

  const metaRes = await fetch(`${API_URL}/api/orderflow/${WINNER_CALL_ID}/meta`);
  console.log(`  GET /api/orderflow/${WINNER_CALL_ID}/meta → ${metaRes.status}`);
  if (!metaRes.ok) throw new Error("Backend meta endpoint returned non-200");
  const meta = await metaRes.json();
  console.log(`  Meta keys: ${Object.keys(meta).join(", ")}`);

  // ── 3. Build Next.js with real backend URL ────────────────────────────────────
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
  console.log("Build complete.\n");

  // ── 4. Start Next.js production server ───────────────────────────────────────
  console.log(`── 4. Starting Next.js on port ${FRONTEND_PORT} ──`);
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
  console.log("Next.js ready.\n");
  await sleep(2_000);

  // ── 5. Ensure Playwright chromium ─────────────────────────────────────────────
  console.log("── 5. Launching Playwright chromium ──");
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } });

  // ── 6. Screenshot winner ──────────────────────────────────────────────────────
  console.log("\n── 6. Capturing winner screenshot ──");
  const winnerPage = await context.newPage();
  const winnerResult = await screenshotCall(winnerPage, WINNER_CALL_ID, WINNER_PNG);
  await winnerPage.close();

  // ── 7. Screenshot loser ───────────────────────────────────────────────────────
  console.log("\n── 7. Capturing loser screenshot ──");
  const loserPage = await context.newPage();
  const loserResult = await screenshotCall(loserPage, LOSER_CALL_ID, LOSER_PNG);
  await loserPage.close();

  // ── 8. Report ─────────────────────────────────────────────────────────────────
  console.log("\n══ RESULTS ══");
  const winnerOk = fs.existsSync(WINNER_PNG) && fs.statSync(WINNER_PNG).size > 50_000;
  const loserOk  = fs.existsSync(LOSER_PNG)  && fs.statSync(LOSER_PNG).size  > 50_000;
  console.log(`  stage5_winner.png: ${fs.existsSync(WINNER_PNG) ? fs.statSync(WINNER_PNG).size + " bytes" : "MISSING"} — ${winnerOk ? "PASS" : "FAIL"}`);
  console.log(`  stage5_loser.png:  ${fs.existsSync(LOSER_PNG)  ? fs.statSync(LOSER_PNG).size  + " bytes" : "MISSING"} — ${loserOk  ? "PASS" : "FAIL"}`);
  console.log(`  Winner canvas rendered: ${winnerResult.rendered}`);
  console.log(`  Loser  canvas rendered: ${loserResult.rendered}`);
  if (winnerResult.consoleErrors.length) console.warn(`  Winner browser errors: ${winnerResult.consoleErrors.join(" | ")}`);
  if (loserResult.consoleErrors.length)  console.warn(`  Loser  browser errors: ${loserResult.consoleErrors.join(" | ")}`);

  if (!winnerOk || !loserOk) process.exitCode = 1;

} catch (err) {
  console.error("\nHarness error:", err?.message ?? err);
  process.exitCode = 1;
} finally {
  await cleanup();
}
