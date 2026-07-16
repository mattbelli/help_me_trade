/**
 * verify_fix_widgets.mjs — one-shot verify: React #185 fix for order-flow widgets
 *
 * Uses next dev (no build needed) + minimal Python backend.
 * Asserts zero React #185 / "Maximum update depth" errors.
 *
 * Usage (from repo root, PowerShell):
 *   $env:PYTHONIOENCODING="utf-8"
 *   node frontend/scripts/verify_fix_widgets.mjs
 */

import { spawn }         from "child_process";
import { chromium }      from "playwright";
import path              from "path";
import { fileURLToPath } from "url";
import fs                from "fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT      = path.resolve(__dirname, "../..");
const FRONTEND  = path.resolve(__dirname, "..");

const CALL_ID       = "1914389641555558635";
const BACKEND_PORT  = 8031;
const FRONTEND_PORT = 3001;
const API_URL       = `http://127.0.0.1:${BACKEND_PORT}`;
const FRONTEND_URL  = `http://localhost:${FRONTEND_PORT}/orderflow?call=${CALL_ID}`;

const SCREENSHOTS_DIR = path.join(ROOT, "data", "research", "_edgehunt", "screenshots");
const OUTPUT_PNG      = path.join(SCREENSHOTS_DIR, "widgets_fixed.png");

async function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function waitForUrl(url, timeoutMs = 90_000, intervalMs = 2000) {
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
  const tag  = `[${path.basename(String(args[args.length - 1] ?? cmd))}]`;
  proc.stdout?.on("data", (d) => process.stdout.write(`${tag} ${d}`));
  proc.stderr?.on("data", (d) => process.stderr.write(`${tag} ${d}`));
  proc.on("error", (e) => console.error(`${tag} spawn error:`, e.message));
  return proc;
}

let backend  = null;
let frontend = null;
let browser  = null;

async function cleanup() {
  if (browser)  { try { await browser.close();   } catch {} }
  if (frontend) { try { frontend.kill("SIGTERM"); } catch {} }
  if (backend)  { try { backend.kill("SIGTERM");  } catch {} }
}

try {
  fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

  // ── 1. Python backend on port 8031 ────────────────────────────────────────
  console.log(`\nStarting Python backend on port ${BACKEND_PORT}…`);
  backend = spawnProcess("python", ["scripts/minimal_orderflow_server.py"], {
    cwd:   ROOT,
    shell: true,
    env: {
      ...process.env,
      ORDERFLOW_SERVER_PORT: String(BACKEND_PORT),
      PYTHONIOENCODING:      "utf-8",
    },
  });

  console.log("Waiting for backend /health…");
  await waitForUrl(`${API_URL}/health`, 60_000);
  console.log("Backend ready.");

  // ── 2. next dev on port 3001 ──────────────────────────────────────────────
  console.log(`\nStarting next dev on port ${FRONTEND_PORT}…`);
  frontend = spawnProcess(
    "npx",
    ["next", "dev", "--turbo", "-p", String(FRONTEND_PORT)],
    {
      cwd:   FRONTEND,
      shell: true,
      env: {
        ...process.env,
        NEXT_PUBLIC_API_URL:   API_URL,
        NEXT_PUBLIC_API_TOKEN: process.env.NEXT_PUBLIC_API_TOKEN ?? "",
        PYTHONIOENCODING:      "utf-8",
      },
    },
  );

  console.log("Waiting for Next.js dev server…");
  await waitForUrl(`http://localhost:${FRONTEND_PORT}/`, 90_000);
  console.log("Next.js ready.");
  await sleep(1_500);

  // ── 3. Playwright — collect errors ────────────────────────────────────────
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1600, height: 1100 });

  const pageErrors  = [];
  const consoleErrs = [];

  page.on("pageerror", (err) => {
    pageErrors.push(err.message);
    console.error("[pageerror]", err.message.slice(0, 200));
  });
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      consoleErrs.push(msg.text());
      console.error("[console.error]", msg.text().slice(0, 200));
    }
  });

  console.log(`\nNavigating to ${FRONTEND_URL}…`);
  await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });

  // Wait up to 10s for canvas, then screenshot regardless
  console.log("Waiting for canvas or 10 s…");
  try {
    await page.waitForFunction(
      () => {
        const cvs = document.querySelectorAll("canvas");
        return Array.from(cvs).some((c) => c.width > 0 && c.height > 0);
      },
      { timeout: 10_000 },
    );
    console.log("Canvas found.");
  } catch {
    console.warn("Canvas not found within 10 s — continuing.");
  }

  await sleep(5_000);  // extra settle time for all widgets

  // ── 4. Screenshot ─────────────────────────────────────────────────────────
  await page.screenshot({ path: OUTPUT_PNG, fullPage: false });
  console.log(`\nScreenshot saved: ${OUTPUT_PNG}`);

  // ── 5. Check for React #185 ───────────────────────────────────────────────
  const reactErrors = [...pageErrors, ...consoleErrs].filter(
    (m) => m.includes("#185") || m.includes("Maximum update depth") || m.includes("too many re-renders"),
  );

  if (reactErrors.length > 0) {
    console.error("\nFAIL — React render loop errors detected:");
    for (const e of reactErrors) console.error(" ", e.slice(0, 300));
    process.exitCode = 1;
  } else {
    console.log("\nPASS — zero React #185 / Maximum update depth errors.");
  }

  // Report any other page errors
  const otherErrors = [...pageErrors, ...consoleErrs].filter(
    (m) => !m.includes("#185") && !m.includes("Maximum update depth") && !m.includes("too many re-renders"),
  );
  if (otherErrors.length > 0) {
    console.warn("\nOther console errors (non-fatal for this test):");
    for (const e of otherErrors) console.warn(" ", e.slice(0, 200));
  }

  // Verify canvas element exists (widgets rendered, not error screen)
  const hasCanvas = await page.evaluate(() => {
    const cvs = document.querySelectorAll("canvas");
    return Array.from(cvs).some((c) => c.width > 0 && c.height > 0);
  });
  if (hasCanvas) {
    console.log("Canvas element present and drawn — footprint widget rendered.");
  } else {
    console.warn("No drawn canvas found — footprint may not have loaded data yet.");
  }

} catch (err) {
  console.error("\nHarness error:", err?.message ?? err);
  process.exitCode = 1;
} finally {
  await cleanup();
}
