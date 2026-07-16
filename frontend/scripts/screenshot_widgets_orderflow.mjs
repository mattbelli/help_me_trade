/**
 * screenshot_widgets_orderflow.mjs
 *
 * Screenshot the four order-flow workspace widgets on the /orderflow page.
 *
 * Backend: Python minimal_orderflow_server.py on port 8021.
 * Frontend: Next.js production build on port 3021.
 *
 * Output: data/research/_edgehunt/screenshots/widgets_orderflow.png
 *         (1600px wide, showing footprint+DOM+tape+heatmap in terminal style)
 *
 * Usage (from repo root, PowerShell):
 *   $env:PYTHONIOENCODING="utf-8"
 *   node frontend/scripts/screenshot_widgets_orderflow.mjs
 */

import { spawn }         from "child_process";
import { chromium }      from "playwright";
import path              from "path";
import { fileURLToPath } from "url";
import fs                from "fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT      = path.resolve(__dirname, "../..");
const FRONTEND  = path.resolve(__dirname, "..");

const CALL_ID      = "1914389641555558635";  // winner call — has dense MBO data
const BACKEND_PORT = 8021;
const FRONTEND_PORT = 3021;
const API_URL      = `http://127.0.0.1:${BACKEND_PORT}`;

const SCREENSHOTS_DIR = path.join(ROOT, "data", "research", "_edgehunt", "screenshots");
const OUTPUT_PNG      = path.join(SCREENSHOTS_DIR, "widgets_orderflow.png");

// ── Helpers ────────────────────────────────────────────────────────────────────

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
  if (browser)  { try { await browser.close();        } catch {} }
  if (frontend) { try { frontend.kill("SIGTERM");      } catch {} }
  if (backend)  { try { backend.kill("SIGTERM");       } catch {} }
}

try {
  fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

  // ── 1. Start Python backend on 8021 ───────────────────────────────────────
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

  // Quick sanity check on the winner call
  try {
    const metaRes = await fetch(`${API_URL}/api/orderflow/${CALL_ID}/meta`, {
      signal: AbortSignal.timeout(8000),
    });
    if (metaRes.ok) {
      const meta = await metaRes.json();
      console.log(`Call meta: level=${meta.level} direction=${meta.direction}`);
    }
  } catch (e) {
    console.warn("Meta prefetch failed (non-fatal):", e.message);
  }

  // ── 2. Build Next.js pointing to the real backend ─────────────────────────
  console.log(`\nBuilding Next.js with NEXT_PUBLIC_API_URL=${API_URL}…`);
  await spawnAndWait("npx", ["next", "build"], {
    cwd:   FRONTEND,
    shell: true,
    env:   {
      ...process.env,
      NEXT_PUBLIC_API_URL:   API_URL,
      NEXT_PUBLIC_API_TOKEN: process.env.NEXT_PUBLIC_API_TOKEN ?? "",
      PYTHONIOENCODING:      "utf-8",
    },
  });
  console.log("Build complete.");

  // ── 3. Start Next.js on port 3021 ─────────────────────────────────────────
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
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1600, height: 1100 });

  // ── 5. Navigate to the four-widget orderflow page ─────────────────────────
  const url = `http://localhost:${FRONTEND_PORT}/orderflow?call=${CALL_ID}`;
  console.log(`\nNavigating to: ${url}`);
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });

  // ── 6. Wait for the footprint canvas to be drawn ──────────────────────────
  // The footprint widget's call selector autofills and the canvas renders.
  // Data load can take up to 90s.
  console.log("Waiting for footprint canvas…");
  try {
    await page.waitForFunction(
      () => {
        // Check that at least one canvas element has been drawn (non-zero size)
        const canvases = document.querySelectorAll("canvas");
        return Array.from(canvases).some(
          (c) => c.width > 0 && c.height > 0,
        );
      },
      { timeout: 200_000 },
    );
    console.log("Canvas rendered.");
    // Extra wait for the full data to paint (book/tape can lag)
    await sleep(8_000);
  } catch {
    console.warn("Canvas render wait timed out — capturing current state.");
    await sleep(4_000);
  }

  // ── 7. Take screenshot ────────────────────────────────────────────────────
  await page.screenshot({ path: OUTPUT_PNG, fullPage: false });
  console.log(`\nScreenshot saved: ${OUTPUT_PNG}`);

} catch (err) {
  console.error("\nHarness error:", err?.message ?? err);
  process.exitCode = 1;
} finally {
  await cleanup();
}
