"""Hairline-dropout probe: measure watchlist row-divider paint strength on the LIVE app.

The "grid lines vanish on alternate rows" bug is fractional row pitch — Chromium
pixel-snaps each 1px border edge independently, and a fractional pitch collapses
every other border to zero height (paints NOTHING — reads as missing, not faint).
DOM probes cannot see it; only pixels can. This drives live :3001 over CDP,
isolates the watchlist widget (sibling widgets + canvases poison the numbers —
measured 2026-07-16), screenshots it, and scores each row boundary's divider
contrast. Healthy = every gap ~100%. Broken = alternating [100, 0, 100, 0, ...].

Usage: .venv/bin/python tools/ui_hairline_probe.py [url]  (default http://localhost:3001)
Exits 0 when all measured row gaps carry a visible divider, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import statistics
import subprocess
import sys
import tempfile
import urllib.request

import websockets
from PIL import Image

CDP_PORT = 9333
SETTLE_SECONDS = 12  # workspace needs data fetches + first paint

ISOLATE_JS = """
(() => {
  const el = [...document.querySelectorAll('[class*="grid"] > div')].find(
    (d) => d.querySelector('[data-widget="watchlist"]') || /watchlist/i.test(d.getAttribute('data-widget-id') || '')
  ) || [...document.querySelectorAll('div')].find((d) => {
    const r = d.getBoundingClientRect();
    return r.width > 200 && r.height > 300 && d.querySelector('table, [class*="border-b"]')
      && /\\b(SPY|QQQ|AAPL)\\b/.test(d.textContent || '');
  });
  if (!el) return JSON.stringify({ error: 'watchlist widget not found' });
  // Hide everything that is not an ancestor or descendant of the widget.
  const keep = new Set();
  for (let n = el; n; n = n.parentElement) keep.add(n);
  el.querySelectorAll('*').forEach((n) => keep.add(n));
  document.body.querySelectorAll('*').forEach((n) => {
    if (!keep.has(n)) n.style.setProperty('display', 'none', 'important');
  });
  // Canvases inside the kept chain still overpaint — kill them too.
  el.querySelectorAll('canvas').forEach((c) => c.style.setProperty('display', 'none', 'important'));
  // checkVisibility guards against the visibility:hidden ghost row-list that
  // poisoned earlier measurements (rects exist, pixels never paint).
  const rows = [...el.querySelectorAll('[class*="border-b"]')].filter((r) => {
    const b = r.getBoundingClientRect();
    return b.height > 20 && b.height < 60 && b.width > 150 &&
      (r.checkVisibility ? r.checkVisibility({ visibilityProperty: true, opacityProperty: true }) : true);
  });
  const rects = rows.slice(0, 14).map((r) => {
    const b = r.getBoundingClientRect();
    return { x: b.x, y: b.y, w: b.width, h: b.height, bottom: b.bottom };
  });
  const wb = el.getBoundingClientRect();
  return JSON.stringify({ widget: { x: wb.x, y: wb.y, w: wb.width, h: wb.height }, rows: rects });
})()
"""


async def cdp(ws, id_, method, params=None):
    await ws.send(json.dumps({"id": id_, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == id_:
            return msg.get("result", {})


def row_luma(img: Image.Image, x0: int, x1: int, y: int) -> float:
    """Median luma across a horizontal scanline — robust to sparse text glyphs."""
    px = img.load()
    vals = []
    for x in range(x0, x1, 3):
        r, g, b = px[x, y][:3]
        vals.append(0.299 * r + 0.587 * g + 0.114 * b)
    return statistics.median(vals)


async def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3001"
    profile = tempfile.mkdtemp(prefix="hairline-probe-")
    proc = subprocess.Popen(
        [
            "/usr/bin/chromium-browser",
            "--headless=new",
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile}",
            "--window-size=1680,1050",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                targets = json.load(
                    urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=1)
                )
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                await asyncio.sleep(0.3)
        else:
            print("chromium debug port never came up")
            return 1

        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=2**26) as ws:
            await cdp(ws, 1, "Page.enable")
            await cdp(ws, 2, "Page.navigate", {"url": url})
            await asyncio.sleep(SETTLE_SECONDS)
            iso = await cdp(
                ws, 3, "Runtime.evaluate", {"expression": ISOLATE_JS, "returnByValue": True}
            )
            info = json.loads(iso["result"]["value"])
            if "error" in info:
                print(f"isolation failed: {info['error']}")
                return 1
            await asyncio.sleep(1)  # let hidden siblings actually unpaint
            shot = await cdp(ws, 4, "Page.captureScreenshot", {"format": "png"})
            img = Image.open(io.BytesIO(base64.b64decode(shot["data"]))).convert("RGB")
            img.save("/tmp/hairline_probe_last.png")

            rows = info["rows"]
            if len(rows) < 4:
                print(f"only {len(rows)} candidate rows — not enough to judge parity")
                return 1
            rows = [r for r in rows if r["bottom"] + 2 < img.height]
            if len(rows) < 4:
                print("rows fall outside the screenshot — viewport too short")
                return 1
            strengths = []
            for r in rows:
                # x-range PER ROW — a shared range from a wider element lets
                # neighboring widgets dominate the median and hide the divider.
                x0 = max(0, int(r["x"]) + 8)
                x1 = min(img.width - 1, int(r["x"] + r["w"]) - 8)
                yb = int(round(r["bottom"]))
                # Divider = strongest scanline within ±2px of the border edge,
                # scored against the row interior as background.
                bg = row_luma(img, x0, x1, max(0, int(r["y"] + r["h"] / 2)))
                candidates = [
                    abs(row_luma(img, x0, x1, yb + dy) - bg)
                    for dy in (-2, -1, 0, 1)
                    if 0 <= yb + dy < img.height
                ]
                strengths.append(max(candidates) if candidates else 0.0)
            # Uniform data rows only — header/toolbar rects have other heights
            # and much stronger borders that would drown the hairline scale.
            mode_h = statistics.mode(round(r["h"]) for r in rows)
            data = [
                (s, r) for s, r in zip(strengths, rows) if round(r["h"]) == mode_h
            ]
            strengths = [s for s, _ in data]
            heights = [round(r["h"], 2) for _, r in data]
            print(f"row heights: {heights}")
            print(f"divider contrast (raw luma delta): {[round(s, 1) for s in strengths]}")
            median_s = statistics.median(strengths)
            # Zero-height snapped borders paint NOTHING — contrast ~0. A painted
            # hairline on these themes is a small but clear delta (>=2 luma).
            weak = [i for i, s in enumerate(strengths) if s < max(2.0, median_s * 0.4)]
            if weak:
                print(f"FAIL — weak/missing dividers under rows {weak}")
                return 1
            print(f"PASS — all {len(strengths)} row boundaries paint a divider")
            return 0
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
