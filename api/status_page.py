"""The bare-bones HTML status page served at ``/`` on the API port (not the Next UI).

Extracted verbatim from ``api/main.py`` (2026-08-16 god-module split). Registered by
``api/main`` via ``app.include_router``.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["status-page"])


@router.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    from api.main import POLL_INTERVAL_SECONDS  # lazy: avoid circular import

    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Help Me Trade</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; background: #0f1115; color: #e8eaed; }
    h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
    .meta { color: #9aa0a6; font-size: 0.9rem; margin-bottom: 1.5rem; }
    .cards { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
    .card { background: #1a1d24; border: 1px solid #2a2f3a; border-radius: 8px; padding: 1rem 1.25rem; min-width: 140px; }
    .card label { display: block; color: #9aa0a6; font-size: 0.75rem; text-transform: uppercase; }
    .card span { font-size: 1.25rem; font-weight: 600; }
    table { width: 100%; border-collapse: collapse; margin-bottom: 2rem; }
    th, td { text-align: left; padding: 0.6rem 0.75rem; border-bottom: 1px solid #2a2f3a; }
    th { color: #9aa0a6; font-size: 0.8rem; text-transform: uppercase; }
  </style>
</head>
<body>
  <h1>Trading Diary</h1>
  <p class="meta" id="updated">Loading...</p>
  <div class="cards" id="account-cards"></div>
  <h2>Open positions</h2>
  <table>
    <thead><tr>
      <th>Symbol</th><th>Qty</th><th>Side</th><th>Price</th><th>Value</th><th>Unrealized P/L</th>
    </tr></thead>
    <tbody id="positions-body"></tbody>
  </table>
  <h2>Open orders</h2>
  <table>
    <thead><tr>
      <th>Symbol</th><th>Qty</th><th>Side</th><th>Type</th><th>Status</th><th>Filled</th>
    </tr></thead>
    <tbody id="orders-body"></tbody>
  </table>
  <script>
    function money(n) { return "$" + Number(n).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}); }
    function plClass(n) { return Number(n) >= 0 ? "color:#3dd68c" : "color:#f07178"; }

    async function refresh() {
      const res = await fetch("/api/snapshot");
      if (!res.ok) { document.getElementById("updated").textContent = "Waiting for first snapshot..."; return; }
      const data = await res.json();
      document.getElementById("updated").textContent =
        "Last updated: " + new Date(data.captured_at).toLocaleString() + " (polls every __POLL_SECONDS__s)";

      const a = data.account;
      document.getElementById("account-cards").innerHTML = [
        ["Cash", money(a.cash)],
        ["Buying power", money(a.buying_power)],
        ["Portfolio", money(a.portfolio_value)],
        ["Status", a.status],
      ].map(([label, val]) =>
        `<div class="card"><label>${label}</label><span>${val}</span></div>`
      ).join("");

      const posBody = document.getElementById("positions-body");
      if (!data.positions.length) {
        posBody.innerHTML = '<tr><td colspan="6" style="color:#9aa0a6">No open positions</td></tr>';
      } else {
        posBody.innerHTML = data.positions.map(p => `<tr>
          <td>${p.symbol}</td><td>${p.qty}</td><td>${p.side}</td>
          <td>${money(p.current_price)}</td><td>${money(p.market_value)}</td>
          <td style="${plClass(p.unrealized_pl)}">${money(p.unrealized_pl)}</td>
        </tr>`).join("");
      }

      const ordBody = document.getElementById("orders-body");
      if (!data.open_orders.length) {
        ordBody.innerHTML = '<tr><td colspan="6" style="color:#9aa0a6">No open orders</td></tr>';
      } else {
        ordBody.innerHTML = data.open_orders.map(o => `<tr>
          <td>${o.symbol}</td><td>${o.qty}</td><td>${o.side}</td>
          <td>${o.order_type}</td><td>${o.status}</td><td>${o.filled_qty}</td>
        </tr>`).join("");
      }
    }
    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>""".replace("__POLL_SECONDS__", str(POLL_INTERVAL_SECONDS))
