# PHASE 1C — Frontend Notification Surface Recon
*Generated 2026-06-29. Read-only survey; no code changed.*

---

## 0. TL;DR: what is broken right now

Phase 1b unified all backend emits through `signals/notify.py:notify()`, which broadcasts
`{"type":"notification", "kind":..., ...}` over WS. The three frontend consumers **all drop
these events silently** because they guard on the old type strings:

| Consumer | Current guard | Effect |
|---|---|---|
| `use-alert-stream.ts:45` | `msg.type !== "alert"` | drops every 1b notification |
| `use-execution-monitor.ts:64` | `msg.type !== "execution"` | drops every 1b notification |
| `use-notification-store.ts:160` (hydrate) | calls `GET /api/alerts/recent` | gets old-shape deque items, maps to wrong fields |

The bell renders the Zustand store; the store is fed by the two WS consumers plus `hydrate()`.
All three paths are now broken. The notification bell shows nothing new.

---

## 1. Shared WS plumbing

There is **no shared WS client**. Each consumer opens its own independent `WebSocket` to
`/ws/market` (via `marketWsUrl()` from `frontend/lib/api.ts:52`).

```
marketWsUrl() = apiUrlWithToken(`${API_BASE.replace(/^http/, "ws")}/ws/market`)
               appends ?token=<NEXT_PUBLIC_API_TOKEN> if set
```

The server endpoint is `api/main.py:1155-1159` (`/ws/market` → `market_hub.handle_client(ws)`).

**How a new WS frame flows:**  
Server → `market_hub.broadcast_alert(payload)` (market_stream.py:247) → sends
`{"type":"alert", **enriched}` to all clients. But because `**enriched` contains `**payload`
and Python dict literals resolve left-to-right, any `"type"` key in `payload` **overrides**
the leading `"type":"alert"`. So `notify()` → `_broadcast_ws({"type":"notification",...})`
→ clients receive `type:"notification"`. The old code path for rule/price alerts that called
`broadcast_alert()` directly WITHOUT a `type` key in the payload still arrives as
`type:"alert"`. Those paths were eliminated by Phase 1b.

**No existing `WsMessage` union type** exists in the frontend. Each consumer defines its own
local message type or uses `Record<string,unknown>`.

**`type:"notification_update"` handling:** zero. Backend emits this on every `POST
/notifications/{id}/read` and `POST /notifications/{id}/dismiss` (notify.py:27-40), but no
frontend handler listens for it. Cross-tab read sync is completely absent.

---

## 2. Consumer 1 — `use-alert-stream.ts`

**File:** `frontend/lib/workspace/use-alert-stream.ts`  
**Mounted in:** `frontend/app/workspace/page.tsx:20` (workspace page only, not AppShell)

### Current behavior
```
ws.onmessage → JSON.parse → guard: msg.type !== "alert" || !isRuleAlert(msg) → return
```
`isRuleAlert(msg)`: returns `false` if `msg.alert != null`, else `true` if any of
`msg.rule_name`, `msg.rule_id`, `msg.source` are truthy (line 24–27). This was designed to
exclude the old price-alert WS frames (which carried `msg.alert` = PriceAlert object) while
accepting rule-fired alerts.

On match, maps to `WorkspaceNotification`:
```ts
id        ← msg.id (fallback: "alert-{timestamp}-{random}")
ruleId    ← msg.rule_id
ruleName  ← msg.rule_name ?? "Alert"
action    ← msg.action (raw, opaque blob)
source    ← msg.source ?? "rule"
message   ← actionMessage(msg.action) — extracts string from action object
ts        ← msg.ts
read      ← false
```
Also calls `requestAlertNotifications()` on mount (asks OS notification permission).

Writes into `useNotificationStore.getState().addNotification(n)`.

### Desired (Phase 1c)
Guard becomes: `msg.type !== "notification"` → return.  
Kind filter: pass through kinds that represent rule/alert signals. Suggested:
`["rule_triggered", "price_alert", "rvol", "earnings", "schwab_health"]` (the "informational"
kinds vs the execution kinds handled by consumer 2). Exact set is an orchestrator decision
(see §6).

New shape mapping:
```
id        ← msg.id
ruleName  ← msg.title
message   ← msg.body
source    ← msg.kind
ts        ← msg.created_at
read      ← msg.read  (server-side read_at !== null)
kind      ← msg.kind   (new field on WorkspaceNotification)
severity  ← msg.severity
action_url ← msg.action_url
```

---

## 3. Consumer 2 — `use-execution-monitor.ts`

**File:** `frontend/lib/use-execution-monitor.ts`  
**Mounted in:** `frontend/components/trading/execution-monitor.tsx:6` which is in
`frontend/components/layout/app-shell.tsx:35` — **every page, not just workspace**.

### Current behavior
```
ws.onmessage → JSON.parse → guard: msg.type !== "execution" → return
```
On match, `executionToNotification(msg)` (line 27–46):
```ts
Reads: msg.status, msg.execution_id, msg.symbol, msg.side, msg.qty,
       msg.execution_mode, msg.strategy_id, msg.ts
Filters statuses: only "submitted"|"filled"|"dry_run"|"partially_filled" (executed)
               or "rejected"|"error" (rejected); drops others
Builds: id = "exec-{execution_id}", source = "execution",
        ruleName = strategy_id ?? "Manual trade",
        message = "Trade executed — {SIDE} {qty} {symbol} [mode]"
```
Writes to `useNotificationStore.getState().addNotification(n)`.

### Desired (Phase 1c)
Guard: `msg.type !== "notification"` → return.  
Kind filter: `!["fill","execution"].includes(msg.kind)` → return.  
(Status-based filter from `executionToNotification` is no longer needed — the backend now
only calls `notify()` for terminal/interesting statuses.)

New shape mapping (same as alert stream):
```
id         ← msg.id
ruleName   ← msg.title
message    ← msg.body
source     ← msg.kind   ("fill" | "execution")
kind       ← msg.kind
severity   ← msg.severity
action_url ← msg.action_url
ts         ← msg.created_at
read       ← msg.read
```
No longer needs `executionToNotification()` — can be deleted.

---

## 4. Consumer 3 — `notification-bell.tsx` + `use-notification-store.ts`

**Bell file:** `frontend/components/workspace/notification-bell.tsx`  
**Mounted in:** `frontend/components/workspace/workspace-toolbar.tsx:392` (workspace toolbar)  
**Store file:** `frontend/lib/workspace/use-notification-store.ts`

### Current bell behavior
- Subscribes to `useNotificationStore` Zustand store (no direct WS)
- On mount calls `hydrate()` once (idempotent via `hydrated` guard)
- Renders dropdown: each notification shows `n.ruleName` + `notificationLabel(n)` + relative time from `n.ts`
- `markRead(id)` / `markAllRead()`: **local-only** — updates Zustand + localStorage, NO API call
- Clicking a notification: calls `markRead(n.id)`, no navigation (action_url ignored)
- Unread count badge: `notifications.filter(n => !n.read).length`

### Current `hydrate()` in the store (lines 149–201)
1. Loads localStorage (`hmt:notifications:v1`) — sets state immediately
2. Calls `GET /api/alerts/recent` via `apiFetch`
3. Maps response `body.alerts[]` with fields `id`, `rule_id`, `rule_name`, `action`, `source`, `ts`
4. Merges with localStorage (byId dedup, sort by ts, cap 50)
5. Preserves read-state from localStorage (server returns all as `read: false`)

`GET /api/alerts/recent` (api/main.py:1162-1164) returns:
```
{ "alerts": [ enriched items from market_hub._recent_alerts deque (maxlen=50) ] }
```
The deque IS still being populated by `broadcast_alert()` (market_stream.py:254) with the
new notification-shaped payloads (since Phase 1b calls `broadcast_alert` via `_broadcast_ws`).
But the frontend mapper reads `a.rule_name` / `a.rule_id` — fields that don't exist in the
new shape (new shape has `title`/`kind`). So hydrate silently produces garbled or empty
`ruleName` / `message` for new-style entries.

**Persistence:** localStorage key `hmt:notifications:v1`. Schema: `{notifications:
WorkspaceNotification[]}`. Server read-state is NOT consulted — hydrate maps all server items
as `read:false` then overlays the local read map.

### Current `WorkspaceNotification` type (store, lines 10–19)
```ts
type WorkspaceNotification = {
  id: string;
  ruleId?: string;
  ruleName: string;   // ← used as primary title in bell + banner
  action?: unknown;   // ← raw action blob
  source: string;
  message?: string;   // ← secondary line
  ts: string;
  read: boolean;
};
```

### Desired (Phase 1c)

**Store changes:**
1. Replace `hydrate()` — call `GET /api/notifications?limit=50` instead of `/api/alerts/recent`;
   map new shape: `id`, `kind`, `title`, `body`, `severity`, `action_url`, `created_at`,
   `read` (boolean from `read_at !== null`), `strategy_id`, `ref_type`, `ref_id`.
2. Add WS listener for `type:"notification_update"` events — update read/dismissed state in
   store + localStorage without a full reload. (Currently there is no WS listener in the store
   itself; the two separate consumers do the WS work. 1c could add a third or consolidate.)
3. `markRead(id)` — add `POST /api/notifications/{id}/read` call (fire-and-forget acceptable;
   store update stays optimistic).
4. `markAllRead()` — add `POST /api/notifications/read-all`.
5. Drop localStorage persistence OR keep it only as the initial hydration seed (the server
   is now the source of truth).

**Type changes:**
```ts
// New WorkspaceNotification
type WorkspaceNotification = {
  id: string;
  kind: string;       // "rule_triggered"|"fill"|"execution"|"proposal"|"price_alert"|etc.
  title: string;      // replaces ruleName
  body: string;       // replaces message
  severity: string;   // "info"|"warning"|"error"|"success"
  source?: string;    // optional, can be derived from kind
  action_url?: string | null;
  ref_type?: string | null;
  ref_id?: string | null;
  created_at: string; // replaces ts
  read: boolean;
  dismissed?: boolean;
  // backward-compat shims (can alias, not duplicate)
};
```

**Bell render changes:**
- `n.ruleName` → `n.title`
- `notificationLabel(n)` → `n.body` (simplify — no longer needs the `actionMessage` path)
- `n.ts` → `n.created_at`
- Clicking a notification: optionally navigate to `n.action_url` if set (see §6 decision 3)

**Banner (`notification-banner.tsx`) render changes:**
- `banner.ruleName` → `banner.title`
- `notificationLabel(banner)` → `banner.body`

**`notificationLabel()` function:** can be deleted or simplified to `n.body || n.title` once
the old shape is gone.

---

## 5. Backend API endpoints (confirmed, exact shapes)

### New endpoints (Phase 1c will use)

**`GET /api/notifications`** (`notification_routes.py:21`)
```
Query params: limit (default 50, max 200), before (ISO timestamp for cursor pagination),
              account_id, strategy_id, include_dismissed (bool, default false)
Response: {
  "notifications": [
    {
      "id": str,          "kind": str,         "title": str,
      "body": str,        "severity": str,     "account_id": str|null,
      "strategy_id": str|null, "ref_type": str|null, "ref_id": str|null,
      "action_url": str|null,  "group_key": str|null,
      "data": dict,       "created_at": str (ISO),
      "read_at": str|null, "dismissed_at": str|null,
      "read": bool,        "dismissed": bool
    }, ...
  ],
  "unread": int
}
```

**`GET /api/notifications/unread-count`** (`notification_routes.py:42`)
```
Query params: account_id
Response: { "unread": int }
```

**`POST /api/notifications/{id}/read`** (`notification_routes.py:56`)
```
Body: none
Response: { "notification": <row> }  (404 if not found)
Side-effect: emits WS notification_update event
```

**`POST /api/notifications/read-all`** (`notification_routes.py:47`)
```
Body: { "account_id"?: str } (optional)
Response: { "updated": int }
```

**`POST /api/notifications/{id}/dismiss`** (`notification_routes.py:69`)
```
Body: none
Response: { "notification": <row> }  (404 if not found)
Side-effect: emits WS notification_update event
```

### Old endpoint being replaced

**`GET /api/alerts/recent`** (`api/main.py:1162`)
```
Response: { "alerts": [ <up to 50 enriched items from in-memory deque> ] }
```
The deque is populated by `market_hub.broadcast_alert()` for every outgoing WS alert frame.
Phase 1b payloads ARE appended (the deque is still alive), but with `type:"notification"`
shape. The endpoint has no DB backing — it returns whatever the in-memory deque holds since
last server restart. **This endpoint should be kept for now** (backward compat until 1c ships)
but the frontend must stop calling it.

### WS events (new shape)
```
type:"notification"        → full notification row (same as GET /api/notifications item)
type:"notification_update" → { type, id, read: bool, dismissed?: bool }
```
Auth: `?token=<NEXT_PUBLIC_API_TOKEN>` appended to WS URL if env var set. Same as today.

---

## 6. Frontend API client (`frontend/lib/api.ts`) — gaps

No functions for the new endpoints exist. Need to add:
```ts
// New types
export type ServerNotification = { id, kind, title, body, severity, action_url, ref_type,
  ref_id, created_at, read_at, dismissed_at, read, dismissed, data, ... }
export type NotificationsResponse = { notifications: ServerNotification[], unread: number }

// New functions
fetchNotifications(params?: { limit?, before?, include_dismissed? }): Promise<NotificationsResponse>
markNotificationRead(id: string): Promise<ServerNotification>
markAllNotificationsRead(): Promise<{ updated: number }>
dismissNotification(id: string): Promise<ServerNotification>
```

Auth pattern for all: `apiFetch(API_BASE + path, { method, headers, body })` — same as
every other endpoint in the file. `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_API_TOKEN` already
handled by `apiFetch()` + `apiAuthHeaders()`.

---

## 7. WS consolidation opportunity

All three consumers open independent WS connections:
- `use-alert-stream.ts` (workspace page only)
- `use-execution-monitor.ts` (every page via AppShell)
- `use-price-alert-monitor.ts` (every page via AppShell, separate file)

Phase 1c could consolidate the `notification` handler into a single shared hook and reduce
the number of open connections. This is an optional cleanup — functionally the three
connections work, they just triple the WS overhead. Recommend doing it but flagging as
optional so it doesn't block delivery.

---

## 8. Decisions for the Orchestrator

These are genuine forks, not mechanics:

**D1 — Kind routing between the two WS consumers.**  
`use-alert-stream` and `use-execution-monitor` will both see every `type:"notification"` WS
frame. Need a canonical kind→consumer mapping so no notification is ingested twice. Suggested
split:
- Execution consumer handles: `execution`, `fill`, `exit`, `rebalance`
- Alert-stream consumer handles: `rule_triggered`, `price_alert`, `rvol`, `earnings`,
  `schwab_health`, `proposal`, `report_ready`, and any unknown kind as fallback
OR: collapse both into a single "notification consumer" and eliminate the distinction. The two
consumers exist today because they served different event types; with the unified shape, the
split is arbitrary. **Recommend collapsing into one.**

**D2 — Read-state: optimistic vs server-confirmed.**  
Current: local-only (`markRead` writes localStorage, no API call).  
Option A: optimistic — update store immediately, fire API call fire-and-forget.  
Option B: server-confirmed — await the API before updating store.  
Optimistic (A) is strongly recommended here; `markRead` is UX, not financial.

**D3 — Click-navigation on action_url.**  
The bell currently just marks read on click. `action_url` is now populated by the backend
(`/positions/{id}`, `/executions/{id}`, `/approvals/{id}`, `/watchlist/{sym}`, `/reports/{id}`,
`/alerts`). Should clicking a notification navigate to that route?  
These are app-internal routes. If the bell is opened from the workspace, Next.js `router.push`
would work. Recommended: if `action_url` is set, navigate AND mark read; if null, just mark
read (same as today).

**D4 — localStorage: keep or drop.**  
With a server-backed inbox, localStorage read-state becomes redundant and a source of drift.
However, it provides instant hydration before the API response. Options:
- Keep as L1 cache only (hydrate from localStorage first, overlay server read-state on API
  response, drop the local read-state tracking in favor of server truth).
- Drop entirely (flash of empty bell until API responds, ~20–100 ms on local network).
Recommend: keep as L1 seed but let server `read` field win on hydrate; stop persisting to
localStorage after hydrate completes.

**D5 — Consolidate 3 WS connections to 1.**  
See §7. Flagged as optional cleanup for this phase. Main benefit: one reconnect loop to
maintain. Main risk: adds scope.

**D6 — Decommission `GET /api/alerts/recent`.**  
Once 1c ships and all FE consumers call `/api/notifications`, the old endpoint becomes dead
weight. It should be removed in a follow-up (not in 1c itself) to avoid breaking any
hypothetical external callers.

---

## 9. File inventory for Phase 1c

| File | Change type |
|------|-------------|
| `frontend/lib/workspace/use-notification-store.ts` | Major rewrite: new type, new hydrate, add markRead/markAllRead API calls, handle `notification_update` WS |
| `frontend/lib/workspace/use-alert-stream.ts` | Guard change + shape mapping |
| `frontend/lib/use-execution-monitor.ts` | Guard change + shape mapping (or delete if consolidating) |
| `frontend/components/workspace/notification-bell.tsx` | Render field renames (`ruleName→title`, `ts→created_at`, add action_url nav) |
| `frontend/components/workspace/notification-banner.tsx` | Render field renames (same) |
| `frontend/lib/api.ts` | Add `ServerNotification` type + 4 fetch functions |
| `frontend/lib/workspace/use-notification-store.ts` | Delete or simplify `notificationLabel()` helper |
