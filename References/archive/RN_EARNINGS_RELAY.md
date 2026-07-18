# RN relay — earnings on the phone (next-earnings + countdown + push)

From serv → Mac/RN agent. The founder missed MU's after-market earnings and wants earnings awareness on the
phone. Backend is done + deployed; this is what the phone should surface.

## 1. Earnings PUSH notifications (already live on serv)
serv now pushes phone alerts for every watchlist symbol's upcoming earnings at three milestones:
- **t14** — 2 weeks out: `📅 {SYM} earnings in 2 weeks — {date}`
- **t1** — day before: `⏰ {SYM} reports tomorrow ({date}, AMC/BMO when known)`
- **t0** — day of: `🔔 {SYM} reports TODAY ({date}, after close / before open when known)`

Payload `data` for deep-link on tap:
```jsonc
{ "type": "earnings", "symbol": "MU", "earnings_date": "2026-06-25", "milestone": "t0" }
```
Handle `type:"earnings"` → deep-link to that symbol's detail / watchlist row (same pattern as the price-alert
deep-link `type:"price_alert"`). One push per (symbol, date, milestone); a new earnings date re-arms.

## 2. Next-earnings + countdown in the phone company-info panel
The phone fundamentals panel currently shows only the PAST earnings date. The watchlist market payload
(`GET /api/watchlist/market`) already returns per symbol:
- `next_earnings: string | null` (YYYY-MM-DD) — the upcoming report date
- `last_earnings: string | null` — the prior report date
- plus `pe`, `eps`, `div_yield`, `market_cap`

Please add **next earnings + a countdown** to the phone panel (mirrors what web is getting now):
- Show `next_earnings` with a countdown: `· today` / `· 1d` / `· {n}d` (days from today).
- Color when near: ≤1d red, ≤3d amber, ≤14d default, else dim.
- Optionally surface an at-a-glance **earnings-soon badge** on the watchlist row itself when within ~14 days
  (`E·{n}d`, same color band) — that's the discoverability win the founder asked for.

No new endpoint — it's all in the existing `/api/watchlist/market` payload. Web is implementing the same
row-badge + panel-countdown; match the labels/bands so the two platforms feel consistent:
- badge: `E·today` / `E·1d` / `E·{n}d`, shown only when `0 ≤ daysUntil ≤ 14`
- bands: `≤1d` red, `≤3d` amber, `≤14d` dim
