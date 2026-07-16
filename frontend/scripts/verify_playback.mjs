/**
 * verify_playback.mjs — prove the footprint reveals bars up to the playhead.
 * Loads a call, scrubs the timeline early vs late, and counts non-background
 * pixels in the footprint canvas. Early playhead must draw fewer pixels.
 */
import { chromium } from "playwright";

const URL = "http://127.0.0.1:3001/orderflow?call=1914389641555558635";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.setViewportSize({ width: 1400, height: 1100 });

const errs = [];
page.on("pageerror", (e) => errs.push(e.message.slice(0, 200)));

await page.goto(URL, { waitUntil: "load", timeout: 30_000 });
// wait for a drawn footprint canvas
await page.waitForFunction(
  () => Array.from(document.querySelectorAll("canvas")).some((c) => c.width > 0 && c.height > 200),
  { timeout: 20_000 },
).catch(() => {});
await page.waitForTimeout(4000);

// Count non-background pixels in the first (footprint) canvas.
async function fpPixels() {
  return page.evaluate(() => {
    const c = document.querySelector("canvas");
    if (!c) return -1;
    const ctx = c.getContext("2d");
    const { data } = ctx.getImageData(0, 0, c.width, c.height);
    // background #131519 = (19,21,25)
    let n = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (Math.abs(data[i] - 19) > 8 || Math.abs(data[i + 1] - 21) > 8 || Math.abs(data[i + 2] - 25) > 8) n++;
    }
    return n;
  });
}

async function scrub(frac) {
  const track = page.locator('[data-testid="timeline-track"]').first();
  const box = await track.boundingBox();
  if (!box) return false;
  await page.mouse.click(box.x + box.width * frac, box.y + box.height / 2);
  await page.waitForTimeout(1200);
  return true;
}

const okTrack = await scrub(0.95);            // playhead near end → most bars
const late = await fpPixels();
await scrub(0.05);                            // playhead near start → few bars
const early = await fpPixels();

console.log("===== PLAYBACK VERIFY =====");
console.log("track found:", okTrack);
console.log("fp non-bg pixels  late(95%):", late, "  early(5%):", early);
console.log("progressive reveal working:", okTrack && early >= 0 && late > 0 && early < late);
console.log("pageErrors:", errs.length ? errs : "(none)");

await browser.close();
