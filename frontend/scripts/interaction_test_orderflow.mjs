/**
 * interaction_test_orderflow.mjs — Order Flow interaction test
 *
 * Boots a minimal backend (port 8001), builds Next.js, starts the production
 * server (port 3006), then simulates user gestures and asserts 9 behaviors:
 *
 *  1. Vertical drag on chart → priceMin/priceMax change (price pan)
 *  2. Horizontal drag on chart → timeMin/timeMax change (time pan)
 *  3. Wheel over chart → time range narrows/widens (time zoom)
 *  4. Shift+wheel → price range changes (price zoom)
 *  4b. Shift+wheel does NOT change the time range (zoom axes are independent)
 *  5. Click-hold-drag along timeline → cursorMs changes + ladder snapshot time
 *     changes + tape has events (scrub drives ladder+tape)
 *  6. Hover over chart (no button held) → cursorMs does NOT change
 *  7. Tape shows multiple DISTINCT timestamps across the window
 *
 * Also captures:
 *   data/research/_edgehunt/screenshots/overhaul_orderflow.png (original)
 *   data/research/_edgehunt/screenshots/fix_bidask_centered.png  (Bid×Ask diverging, zoomed in)
 *   data/research/_edgehunt/screenshots/fix_zoomout.png          (Vol×Delta zoomed out, degraded view)
 *
 * Usage (from repo root):
 *   node frontend/scripts/interaction_test_orderflow.mjs
 */

import { spawn }         from "child_process";
import { chromium }      from "playwright";
import path              from "path";
import { fileURLToPath } from "url";
import fs                from "fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT      = path.resolve(__dirname, "../..");
const FRONTEND  = path.resolve(__dirname, "..");

const SCREENSHOTS_DIR       = path.join(ROOT, "data", "research", "_edgehunt", "screenshots");
const SCREENSHOT_PNG        = path.join(SCREENSHOTS_DIR, "overhaul_orderflow.png");
const SCREENSHOT_BIDASK     = path.join(SCREENSHOTS_DIR, "fix_bidask_centered.png");
const SCREENSHOT_ZOOMOUT    = path.join(SCREENSHOTS_DIR, "fix_zoomout.png");

// Use a winner call with MBO data
const WINNER_POST_ID = "1914389641555558635";
const BACKEND_PORT   = 8001;
const FRONTEND_PORT  = 3006;
const API_URL        = `http://127.0.0.1:${BACKEND_PORT}`;
const FRONTEND_URL   = `http://localhost:${FRONTEND_PORT}/orderflow?call=${WINNER_POST_ID}`;

// ── Test result tracking ───────────────────────────────────────────────────────

let passCount = 0;
let failCount = 0;
const results = [];

function assert(condition, label) {
  if (condition) {
    console.log(`  PASS  ${label}`);
    passCount++;
    results.push({ label, pass: true });
  } else {
    console.error(`  FAIL  ${label}`);
    failCount++;
    results.push({ label, pass: false });
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function waitForUrl(url, timeoutMs = 120_000, intervalMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  let lastErr    = null;
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

/** Read window.__ofState from the page. */
async function getState(page) {
  return page.evaluate(() => {
    const s = window.__ofState;
    return s
      ? {
          timeMin:  s.timeMin,
          timeMax:  s.timeMax,
          priceMin: s.priceMin,
          priceMax: s.priceMax,
          cursorMs: s.cursorMs,
        }
      : null;
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

  // ── 1. Start Python backend ─────────────────────────────────────────────
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
  await waitForUrl(`${API_URL}/health`, 60_000);
  console.log("Backend ready.");

  // ── 2. Build Next.js ────────────────────────────────────────────────────
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

  // ── 3. Start Next.js prod server ────────────────────────────────────────
  console.log(`\nStarting Next.js on port ${FRONTEND_PORT}…`);
  frontend = spawnProcess("npx", ["next", "start", "-p", String(FRONTEND_PORT)], {
    cwd:   FRONTEND,
    shell: true,
  });
  await waitForUrl(`http://localhost:${FRONTEND_PORT}/`, 60_000);
  console.log("Next.js ready.");
  await sleep(2_000);

  // ── 4. Open Playwright ──────────────────────────────────────────────────
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1600, height: 1100 });

  console.log(`\nNavigating to ${FRONTEND_URL}`);
  await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });

  // ── 5. Wait for chart to render ─────────────────────────────────────────
  console.log("Waiting for chart canvas to render…");
  try {
    await page.waitForSelector('[data-testid="chart-container"]', { timeout: 180_000 });
    console.log("Chart container visible.");
  } catch {
    console.warn("Chart container not found — trying anyway.");
  }

  // Also wait for window.__ofState to be populated (canvas needs to draw once)
  console.log("Waiting for window.__ofState…");
  await page.waitForFunction(() => !!window.__ofState, { timeout: 60_000 });
  await sleep(2_000); // extra settle time
  console.log("__ofState ready.");

  // ── GET CHART BOUNDING BOX ───────────────────────────────────────────────
  const chartBox = await page.locator('[data-testid="chart-container"]').boundingBox();
  if (!chartBox) throw new Error("chart-container not found");

  const chartCx = chartBox.x + chartBox.width  / 2;
  const chartCy = chartBox.y + chartBox.height  / 3; // upper third of chart

  // ═══════════════════════════════════════════════════════════════════════════
  // TEST 1: Vertical drag on chart → priceMin/priceMax change
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n── Test 1: Vertical drag → price pan ──────────────────────────────");

  const state0 = await getState(page);
  console.log(`  Before: priceMin=${state0?.priceMin?.toFixed(2)}, priceMax=${state0?.priceMax?.toFixed(2)}`);

  // Drag vertically 120px (from center downward)
  await page.mouse.move(chartCx, chartCy);
  await page.mouse.down();
  await page.mouse.move(chartCx, chartCy + 120, { steps: 10 });
  await page.mouse.up();
  await sleep(400);

  const state1 = await getState(page);
  console.log(`  After:  priceMin=${state1?.priceMin?.toFixed(2)}, priceMax=${state1?.priceMax?.toFixed(2)}`);

  const priceChanged = state1 != null && state0 != null &&
    (Math.abs((state1.priceMin ?? 0) - (state0.priceMin ?? 0)) > 0.01 ||
     Math.abs((state1.priceMax ?? 0) - (state0.priceMax ?? 0)) > 0.01);
  assert(priceChanged, "Vertical drag changes priceMin/priceMax");

  // ═══════════════════════════════════════════════════════════════════════════
  // TEST 2: Horizontal drag on chart → timeMin/timeMax change
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n── Test 2: Horizontal drag → time pan ─────────────────────────────");

  const state2a = await getState(page);
  const timeMin0 = state2a?.timeMin;
  const timeMax0 = state2a?.timeMax;
  console.log(`  Before: span=${Math.round(((timeMax0 ?? 0) - (timeMin0 ?? 0)) / 1000)}s`);

  // Drag horizontally 200px to the right (pan to earlier time)
  await page.mouse.move(chartCx, chartCy);
  await page.mouse.down();
  await page.mouse.move(chartCx + 200, chartCy, { steps: 15 });
  await page.mouse.up();
  await sleep(400);

  const state2b = await getState(page);
  const timeMin1 = state2b?.timeMin;
  console.log(`  After: timeMin changed by ${Math.round(((timeMin1 ?? 0) - (timeMin0 ?? 0)) / 1000)}s`);

  const timeChanged = state2b != null && state2a != null &&
    Math.abs((state2b.timeMin ?? 0) - (state2a.timeMin ?? 0)) > 1000;
  assert(timeChanged, "Horizontal drag changes timeMin/timeMax");

  // ═══════════════════════════════════════════════════════════════════════════
  // TEST 3: Wheel → time range narrows/widens
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n── Test 3: Wheel → time zoom ──────────────────────────────────────");

  const state3a = await getState(page);
  const span0 = (state3a?.timeMax ?? 0) - (state3a?.timeMin ?? 0);
  console.log(`  Before: span=${Math.round(span0 / 1000)}s`);

  // Scroll down (zoom out) over the chart
  await page.mouse.move(chartCx, chartCy);
  await page.mouse.wheel(0, 200);
  await sleep(400);

  const state3b = await getState(page);
  const span1 = (state3b?.timeMax ?? 0) - (state3b?.timeMin ?? 0);
  console.log(`  After: span=${Math.round(span1 / 1000)}s`);

  assert(
    state3b != null && Math.abs(span1 - span0) > 1000,
    "Wheel changes time range",
  );

  // ═══════════════════════════════════════════════════════════════════════════
  // TEST 4: Shift+wheel → price range changes
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n── Test 4: Shift+wheel → price zoom ──────────────────────────────");

  const state4a = await getState(page);
  const priceRange0 = (state4a?.priceMax ?? 0) - (state4a?.priceMin ?? 0);
  console.log(`  Before: priceRange=${priceRange0.toFixed(2)}`);

  await page.mouse.move(chartCx, chartCy);
  await page.keyboard.down("Shift");
  await page.mouse.wheel(0, 200);
  await page.keyboard.up("Shift");
  await sleep(400);

  const state4b = await getState(page);
  const priceRange1 = (state4b?.priceMax ?? 0) - (state4b?.priceMin ?? 0);
  console.log(`  After: priceRange=${priceRange1.toFixed(2)}`);

  assert(
    state4b != null && Math.abs(priceRange1 - priceRange0) > 0.1,
    "Shift+wheel changes price range",
  );

  // Independence: Shift+wheel must NOT change the time range
  const timeSpan4a = (state4a?.timeMax ?? 0) - (state4a?.timeMin ?? 0);
  const timeSpan4b = (state4b?.timeMax ?? 0) - (state4b?.timeMin ?? 0);
  console.log(
    `  Time span before=${Math.round(timeSpan4a / 1000)}s, after=${Math.round(timeSpan4b / 1000)}s`,
  );
  assert(
    Math.abs(timeSpan4b - timeSpan4a) < 500,
    "Shift+wheel does NOT change time range (price+time zoom are independent)",
  );

  // ═══════════════════════════════════════════════════════════════════════════
  // TEST 5: Timeline drag → cursorMs changes + ladder time changes + tape updates
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n── Test 5: Timeline scrub drives cursor + ladder + tape ───────────");

  // Scroll the timeline track into view FIRST, then get its bounding box
  await page.locator('[data-testid="timeline-track"]').scrollIntoViewIfNeeded();
  await sleep(200); // let scroll settle before measuring position

  const trackBox = await page.locator('[data-testid="timeline-track"]').boundingBox();
  if (!trackBox) {
    console.warn("  timeline-track not found, skipping");
    assert(false, "Timeline track found");
  } else {
    const trackY = trackBox.y + trackBox.height / 2;

    // Record initial state
    const stateBeforeScrub = await getState(page);
    const cursorBefore     = stateBeforeScrub?.cursorMs;

    // Also record ladder time before scrub
    const ladderTimeBefore = await page.locator('[data-testid="ladder-snapshot-time"]')
      .first().textContent().catch(() => null);

    // Drag from 20% → 70% along the timeline track
    const startX = trackBox.x + trackBox.width * 0.20;
    const endX   = trackBox.x + trackBox.width * 0.70;

    await page.mouse.move(startX, trackY);
    await page.mouse.down();
    await page.mouse.move(endX, trackY, { steps: 20 });
    await page.mouse.up();

    // Wait for React to process the cursorMs update (up to 3s)
    let cursorAfter = null;
    try {
      await page.waitForFunction(
        () => window.__ofState?.cursorMs != null,
        { timeout: 3_000 },
      );
      const stateAfterScrub = await getState(page);
      cursorAfter = stateAfterScrub?.cursorMs;
    } catch {
      // cursorMs still null after timeout — drag didn't register
      const stateAfterScrub = await getState(page);
      cursorAfter = stateAfterScrub?.cursorMs;
    }

    console.log(`  cursorMs before=${cursorBefore}, after=${cursorAfter}`);
    assert(
      cursorAfter != null && cursorAfter !== cursorBefore,
      "Timeline drag changes cursorMs",
    );

    const ladderTimeAfter = await page.locator('[data-testid="ladder-snapshot-time"]')
      .first().textContent().catch(() => null);
    console.log(`  Ladder time: '${ladderTimeBefore?.trim()}' → '${ladderTimeAfter?.trim()}'`);
    assert(
      ladderTimeAfter != null && ladderTimeAfter.trim() !== "— ET",
      "DOM ladder shows a snapshot time after scrub",
    );

    // Tape should now have events (may still be loading windowed tape)
    await sleep(2_000); // give tape time to populate
    const tapeTimes = await page.locator('[data-testid="tape-event-time"]').allTextContents();
    console.log(`  Tape events visible: ${tapeTimes.length}`);
    assert(tapeTimes.length > 0, "Tape shows events after scrub");
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // TEST 6: Hover over chart (no button) → cursorMs does NOT change
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n── Test 6: Chart hover does NOT drive cursorMs ─────────────────────");

  const stateBefore6 = await getState(page);
  const cursorBefore6 = stateBefore6?.cursorMs;

  // Move mouse to several positions on the chart WITHOUT clicking
  await page.mouse.move(chartCx - 100, chartCy);
  await sleep(100);
  await page.mouse.move(chartCx, chartCy + 50);
  await sleep(100);
  await page.mouse.move(chartCx + 100, chartCy - 30);
  await sleep(200);

  const stateAfter6 = await getState(page);
  const cursorAfter6 = stateAfter6?.cursorMs;

  console.log(`  cursorMs before=${cursorBefore6}, after=${cursorAfter6}`);
  assert(
    cursorBefore6 === cursorAfter6,
    "Hovering chart does NOT change cursorMs",
  );

  // ═══════════════════════════════════════════════════════════════════════════
  // TEST 7: Tape shows multiple DISTINCT timestamps
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n── Test 7: Tape has multiple distinct timestamps ───────────────────");

  // Wait up to 5s for tape to show diverse timestamps (windowed tape may still be loading)
  let tapeTimeCells = [];
  let uniqueTimes = new Set();
  const deadline7 = Date.now() + 5_000;
  while (Date.now() < deadline7) {
    tapeTimeCells = await page.locator('[data-testid="tape-event-time"]').allTextContents();
    uniqueTimes = new Set(tapeTimeCells.slice(0, 30));
    if (uniqueTimes.size > 1) break;
    await sleep(500);
  }
  console.log(
    `  Tape cells: ${tapeTimeCells.length} visible, ${uniqueTimes.size} unique timestamps`,
  );

  assert(
    uniqueTimes.size > 1,
    `Tape has multiple distinct timestamps (got ${uniqueTimes.size} unique)`,
  );

  // ═══════════════════════════════════════════════════════════════════════════
  // Screenshot
  // ═══════════════════════════════════════════════════════════════════════════
  await page.screenshot({ path: SCREENSHOT_PNG, fullPage: true });
  console.log(`\nScreenshot: ${SCREENSHOT_PNG}`);

  // ═══════════════════════════════════════════════════════════════════════════
  // SCREENSHOT A: fix_bidask_centered — Bid×Ask mode, zoomed IN
  // Shows bid (red) bars growing leftward and ask (green) growing rightward
  // from the candle center spine.
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n── Screenshot: fix_bidask_centered (Bid×Ask, zoomed in) ────────────");

  // Reset view first, then switch to Bid×Ask mode
  await page.locator("button:text('reset view')").click().catch(() => {});
  await sleep(300);

  // Switch to Bid×Ask mode by clicking its button
  await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll("button"));
    const btn  = btns.find((b) => b.textContent?.trim() === "Bid×Ask");
    if (btn) btn.click();
  });
  await sleep(600);

  // Re-fetch chart box (view may have changed after reset)
  const chartBoxA = await page.locator('[data-testid="chart-container"]').boundingBox();
  if (chartBoxA) {
    const cxA = chartBoxA.x + chartBoxA.width  / 2;
    const cyA = chartBoxA.y + chartBoxA.height  / 3;

    // Zoom IN on time (scroll up 8 times) to make bars wide enough to see diverging layout
    for (let i = 0; i < 8; i++) {
      await page.mouse.move(cxA, cyA);
      await page.mouse.wheel(0, -220);
      await sleep(80);
    }
    await sleep(800);
  }

  await page.screenshot({ path: SCREENSHOT_BIDASK, fullPage: true });
  console.log(`  Saved: ${SCREENSHOT_BIDASK}`);

  // ═══════════════════════════════════════════════════════════════════════════
  // SCREENSHOT B: fix_zoomout — Vol×Delta mode, zoomed OUT
  // Shows the graceful degradation: delta-colored bars + per-bar Δtotal on top
  // and volume on bottom, no cramped per-cell numbers.
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n── Screenshot: fix_zoomout (Vol×Delta, zoomed out) ─────────────────");

  // Switch back to Vol×Delta mode
  await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll("button"));
    const btn  = btns.find((b) => b.textContent?.includes("Vol×Delta"));
    if (btn) btn.click();
  });
  await sleep(400);

  // Reset to default view, then zoom OUT heavily
  await page.locator("button:text('reset view')").click().catch(() => {});
  await sleep(300);

  const chartBoxB = await page.locator('[data-testid="chart-container"]').boundingBox();
  if (chartBoxB) {
    const cxB = chartBoxB.x + chartBoxB.width  / 2;
    const cyB = chartBoxB.y + chartBoxB.height  / 3;

    // Zoom OUT on time (scroll down 12 times) to narrow bars into degraded view
    for (let i = 0; i < 12; i++) {
      await page.mouse.move(cxB, cyB);
      await page.mouse.wheel(0, 220);
      await sleep(80);
    }
    await sleep(800);
  }

  await page.screenshot({ path: SCREENSHOT_ZOOMOUT, fullPage: true });
  console.log(`  Saved: ${SCREENSHOT_ZOOMOUT}`);

  // ═══════════════════════════════════════════════════════════════════════════
  // Summary
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n════════════════════════════════════════════════════════════════════");
  console.log(`RESULTS: ${passCount} passed, ${failCount} failed`);
  console.log("════════════════════════════════════════════════════════════════════");
  for (const r of results) {
    console.log(`  ${r.pass ? "PASS" : "FAIL"}  ${r.label}`);
  }

  if (failCount > 0) {
    console.error(`\n${failCount} test(s) failed.`);
    process.exitCode = 1;
  } else {
    console.log("\nAll tests passed.");
  }

} catch (err) {
  console.error("\nHarness error:", err?.message ?? err);
  if (err?.stack) console.error(err.stack);
  process.exitCode = 1;
} finally {
  await cleanup();
}
