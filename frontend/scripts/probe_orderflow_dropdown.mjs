/**
 * probe_orderflow_dropdown.mjs — diagnose the empty call dropdown / no-hydration.
 */
import { chromium } from "playwright";

const URL = process.env.PROBE_URL ?? "http://127.0.0.1:3001/orderflow";

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.setViewportSize({ width: 1400, height: 1000 });

const apiHits = [];
const badReqs = [];
const jsChunks = [];
page.on("requestfinished", async (req) => {
  const u = req.url();
  const resp = await req.response();
  const st = resp?.status();
  if (u.includes("/api/orderflow")) apiHits.push(`${req.method()} ${u.replace(/^https?:\/\/[^/]+/, "")} -> ${st}`);
  if (/_next\/static\/.+\.js$/.test(u)) jsChunks.push(`${st} ${u.split("/").pop()}`);
  if (st && st >= 400) badReqs.push(`${st} ${u.replace(/^https?:\/\/[^/]+/, "")}`);
});
page.on("requestfailed", (req) => badReqs.push(`FAILED ${req.url().replace(/^https?:\/\/[^/]+/, "")} (${req.failure()?.errorText})`));

console.log(`Navigating to ${URL}`);
await page.goto(URL, { waitUntil: "load", timeout: 30_000 });
await page.waitForTimeout(8000);

// Did React hydrate? Probe a few signals.
const hydration = await page.evaluate(() => {
  const out = {};
  out.hasNextData = typeof window.__NEXT_DATA__ !== "undefined";
  out.reactRoot = !!document.querySelector("#__next, [data-reactroot], main, body > div");
  out.selectCount = document.querySelectorAll("select").length;
  const sel = document.querySelector("select");
  out.selectOptionCount = sel ? sel.querySelectorAll("option").length : -1;
  out.canvasCount = document.querySelectorAll("canvas").length;
  out.drawnCanvas = Array.from(document.querySelectorAll("canvas")).some((c) => c.width > 0 && c.height > 0);
  // Any visible error text?
  out.bodyHasError = /error|failed|cannot|undefined is not/i.test(document.body.innerText);
  return out;
});

console.log("\n===== RESULT =====");
console.log("API /api/orderflow hits:", apiHits.length ? apiHits : "(NONE)");
console.log("JS chunks loaded:", jsChunks.length, "—", jsChunks.slice(0, 6).join(", "));
console.log("bad/failed requests:", badReqs.length ? [...new Set(badReqs)].slice(0, 20) : "(none)");
console.log("hydration signals:", JSON.stringify(hydration, null, 2));

await browser.close();
