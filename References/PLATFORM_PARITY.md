# Platform Parity — Web vs Phone (living doc)

**Purpose:** one place to see what exists on the **web app** (this repo, `frontend/`) vs the **phone app** (separate
React-Native repo, owned by the Mac agent), so we never lose track of "we built X on web, but it's not on phone yet."

**Discipline (agreed):** every time we add/change a feature on either platform, update this doc in the same round —
add a row to the right table and bump the **Delta** section. Web side is authoritative (we see it directly); phone
side reflects the latest Mac-agent relays and is marked ⚠️ where unconfirmed.

_Last updated: 2026-06-25._

---

## Where things live on WEB (so "where is it?" stops happening)
The web app has **two surfaces**: the **Workspace** (the draggable widget grid at `/workspace` — where the founder
actually lives) and **full pages** (the left-nav routes). Plus widgets that are *registered but not placed by default*.

### Workspace widgets (12 registered)
| Widget | On default grid? |
|---|---|
| account-summary, open-orders, positions, watchlist, chart, equity-curve | ✅ default |
| **price-alerts, reports, scans, memory, agent-proposals, rvol-scanner (Volume Scanner)** | ⚠️ registered, NOT on default grid (must be added) |

### Full pages (left-nav)
Overview `/` · Trading · Charts · Performance · Research · Memories · Agents · **Rules** · **Automations** · Trades · Workspace

---

## Feature matrix

| Feature | Web | Phone | Notes |
|---|---|---|---|
| Watchlist (RVOL, earnings badges, company-info panel) | ✅ widget + /trading | ✅ | company-info "last earnings" fix is backend → both get it |
| Charts (intraday/daily) | ✅ (+ maximize-to-full-view from grid) | ✅ | both use `/api/bars`; web chart widget now has a maximize button → full interactive chart |
| **Post-market (extended-hours) candles** | ✅ | ✅ (auto via /api/bars) | backend change → both |
| **Session-node Levels overlay on chart** | ✅ "Levels" toggle | ⚠️ TBD | web-only UI; phone needs its own toggle |
| Trade proposals (Approve/Reject) | ✅ agent-proposals widget | ✅ Live Activity (Dynamic Island) | |
| **Strategy cockpit (arm/configure)** | ✅ Settings + Automations | ⚠️ TBD | web-only |
| **Morning levels report** | ✅ push + reports widget + Automations | ✅ push only | neither has a persistent inbox (see Delta) |
| Earnings notifications (2wk/1d/day-of) | ✅ push (+ watchlist badge) | ✅ push | |
| Price alerts | ✅ widget (+ chart-created) | ✅ push | |
| **Automations cockpit page** | ✅ /automations | ❌ | web-only (new) |
| **Notifications / inbox** | ⚠️ bell (proposals + rule-fires only; not clickable; no reports) | ❌ push only, ephemeral | BOTH need work (see Delta) |
| Live price streaming (WS) | ✅ | ✅ | |

---

## Delta — web has it, phone doesn't (or both incomplete)
Work to relay to the Mac agent / build on phone:
1. **Session-node Levels overlay** on the phone chart (web shipped the toggle).
2. **Strategy cockpit** (arm/configure strategies) — web-only; phone has no strategy controls.
3. **Automations cockpit** — web-only; decide phone equivalent.
4. **Notifications/inbox — both platforms are incomplete:**
   - Phone: reports/alerts are ephemeral push; founder wants a **persistent inbox** (until deleted).
   - Web: the bell only captures proposals + "rule triggered", entries **aren't clickable / have no detail**, and
     **reports don't register there at all**.
5. **Reports** beyond the morning levels (EOD recap, etc.) — neither platform yet.

## Known web-internal discoverability gaps (not parity, but "where is it")
- 6 widgets registered but off the default grid (price-alerts, reports, scans, memory, agent-proposals, rvol-scanner).
- Rich pages (Automations, Performance, Research) live in the nav but the founder lives in the Workspace → easy to miss.
- _Open design question: how the Workspace and the full pages integrate into one experience._
