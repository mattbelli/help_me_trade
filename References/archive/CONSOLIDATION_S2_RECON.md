# CONSOLIDATION_S2_RECON.md
*Stage 2 migration recon — 2026-06-30. Read-only. No code was changed.*
*Companion: `CONSOLIDATION_PLAN.md` (Stage 2 spec), `CONSOLIDATION_RECON.md` (overall current state).*

---

## 1. The Workspace Store — exact current shape

**File:** `frontend/lib/workspace/use-workspace-store.ts`

### State type (lines 22–40)
```ts
type WorkspaceState = {
  layout: WorkspaceLayoutItem[];   // the single flat array of widget instances
  hydrated: boolean;               // false until hydrate() resolves
  locked: boolean;                 // persisted to localStorage 'hmt-workspace-locked' only (NOT server)
  pickerOpen: boolean;             // UI-only, never persisted
  gridCols: number;                // NOT persisted — recomputed by ResizeObserver on every render
  hydrate: () => Promise<void>;
  setGridCols: (cols: number) => void;
  updateLayout: (layout: WorkspaceLayoutItem[]) => void;
  addWidget: (type: string) => boolean;
  updateWidgetConfig: (instanceId: string, patch: Partial<WidgetInstanceConfig>) => void;
  removeWidget: (id: string) => void;
  toggleLock: () => void;
  setPickerOpen: (open: boolean) => void;
  openPicker: () => void;
};
```

Initial value: `{ layout: DEFAULT_WORKSPACE_LAYOUT, hydrated: false, locked: false, pickerOpen: false, gridCols: GRID_DEFAULT_COLS (24) }` (line 141–148)

### Every action in detail

**`hydrate()` (lines 151–199)**
1. Try `fetchWorkspaceLayout()` → `GET /api/workspace/layout`
2. If server returns a non-empty array, `filterKnown()` (drops unknown widget types via `getWidgetDefinition`), `set({ layout, hydrated: true, locked: loadLocked() })`; also writes server result back to localStorage (keeps local cache in sync)
3. If server empty/unreachable, try `localStorageLayoutPersistence.load()`; if found, calls `scheduleServerSave()` to seed server from local
4. Else falls through to `DEFAULT_WORKSPACE_LAYOUT`

**`updateLayout(layout)` (lines 201–204)**
- `set({ layout })`
- calls `persistLayout(layout)` → immediate localStorage + debounced (500 ms) `saveWorkspaceLayout(layout)` to server

**`addWidget(type)` (lines 206–237)**
1. `getWidgetDefinition(type)` — returns false if unknown
2. If singleton and `layout.some(item => item.type === type)` → returns `false` (no-op)
3. If `multiInstance`, `nextInstanceId(type, layout)` generates `"${type}-N"` (max existing N + 1)
4. `findPlacement(layout, def.defaultSize, gridCols)` — scans for first non-overlapping slot
5. `defaultConfigForType(type)` — seeds chart `{channel:"none", timeframe: CHART_TIMEFRAMES[4].value, style:"candle"}` or watchlist `{channel:"none"}`
6. Clamps `w = Math.min(def.defaultSize.w, gridCols)`
7. Appends new `WorkspaceLayoutItem` to layout; `set({ layout: merged, pickerOpen: false })`; `persistLayout(merged)`

**`updateWidgetConfig(instanceId, patch)` (lines 239–247)**
- Maps layout, merges `patch` into matching `item.config`
- `set({ layout: next })`; `persistLayout(next)`

**`removeWidget(id)` (lines 249–254)**
- Filters by `item.i !== id`; if length unchanged, no-op
- `set({ layout: next })`; `persistLayout(next)`

**`toggleLock()` (lines 257–260)**
- Flips `locked`, calls `saveLocked(locked)` → writes to `localStorage["hmt-workspace-locked"]`
- Does NOT write to server, does NOT call `persistLayout`

**`openPicker()` / `setPickerOpen(open)` (lines 263–264)**
- Pure UI state; no persistence

### Every call into `persistLayout()`
| Site | Trigger |
|------|---------|
| `updateLayout` | RGL `onDragStop`/`onResizeStop` → `WorkspaceGrid.persistLayout` → `useWorkspaceStore.updateLayout` |
| `addWidget` | Widget picker select, `/add` command |
| `updateWidgetConfig` | Chart timeframe/style/symbol/channel changes; watchlist channel/list changes |
| `removeWidget` | Confirm-remove dropdown in `WidgetRemoveButton` |

`persistLayout` itself (lines 133–137): `localStorageLayoutPersistence.save(layout)` (immediate) + `scheduleServerSave(layout)` (500 ms debounce, module-level timer, last-write-wins).

### What consumes the store

| Consumer | Reads | Writes |
|---|---|---|
| `WorkspaceGrid` (workspace-grid.tsx:437–449) | `layout, hydrated, hydrate, locked, updateLayout, updateWidgetConfig, removeWidget, pickerOpen, setPickerOpen, setGridCols` | via callbacks |
| `WorkspaceWidgetPicker` (workspace-widget-picker.tsx:24–26) | `layout` (for singleton dedup), `addWidget, setPickerOpen` | — |
| `CommandConsole` (command-console.tsx:97) | `locked, toggleLock, openPicker` | — |
| `use-workspace-commands.ts` (line 43) | `openPicker` | — |

### What changes for `layout[] → pages + activePageId`

```ts
// NEW types
type WorkspacePage = { id: string; name: string; layout: WorkspaceLayoutItem[] };
type WorkspaceState = {
  pages: Record<string, WorkspacePage>;   // replaces `layout`
  activePageId: string;                   // which page is showing
  // ... everything else unchanged ...
};
```

Every action that currently takes/reads `layout` needs to scope to `pages[activePageId].layout`. Specifically:
- `hydrate()`: load pages map instead of flat layout; set `pages` + `activePageId`
- `updateLayout(layout)`: `pages[activePageId].layout = layout`
- `addWidget(type)`: `findPlacement` uses active page layout; `layout.some(...)` singleton check uses active page only; persists full pages map
- `updateWidgetConfig(instanceId, patch)`: maps over active page layout
- `removeWidget(id)`: filters active page layout
- `WorkspaceGrid` render: reads `pages[activePageId]?.layout ?? []` (the `layout` selector must be updated too, or WorkspaceGrid changes its subscription)
- `WorkspaceWidgetPicker`: reads active page layout for singleton dedup
- New actions: `addPage(name)`, `renamePage(id, name)`, `deletePage(id)`, `switchPage(id)`
- `persistLayout(layout)` becomes `persistPages(pages)` (or `persistActivePage` if you prefer incremental — but full pages map is simplest)

`gridCols`, `locked`, `pickerOpen`, `hydrated` stay flat (not per-page).

---

## 2. Persistence — layout-persistence.ts + api.ts

**File:** `frontend/lib/workspace/layout-persistence.ts`

### Type shapes (lines 5–25)

```ts
export type WidgetInstanceConfig = {
  channel?: ChannelId;             // "none"|"1"|"2"|"3"|"4"|"5"|"6"
  symbol?: string;                 // chart: manual symbol
  timeframe?: string;              // chart: ChartTimeframe.value
  style?: "candle" | "line";       // chart
  watchlistId?: string;            // watchlist: which list
  showSessionNodes?: boolean;      // chart studies toggle
};

export type WorkspaceLayoutItem = {
  i: string;        // INSTANCE id: "chart-1", "watchlist-2", "positions" (singletons use type as id)
  type: string;     // registry key: "chart", "positions", etc.
  x: number; y: number; w: number; h: number;
  minW?: number; minH?: number;
  config?: WidgetInstanceConfig;
};
```

### localStorage key
`STORAGE_KEY = "hmt-workspace-layout-v2"` (line 32) — bumped from `v1` when Batch 2 introduced 24-col grid + instance IDs.

### DEFAULT_WORKSPACE_LAYOUT (lines 34–107)
7 widgets on 24-col grid:
| i | type | x | y | w | h | config |
|---|------|---|---|---|---|--------|
| account-summary | account-summary | 0 | 0 | 24 | 3 | — |
| open-orders | open-orders | 0 | 3 | 12 | 5 | — |
| positions | positions | 12 | 3 | 12 | 5 | — |
| watchlist-1 | watchlist | 0 | 8 | 8 | 6 | `{channel:"1"}` |
| chart-1 | chart | 8 | 8 | 16 | 6 | `{channel:"1", timeframe:"1Day", style:"candle"}` |
| equity-curve | equity-curve | 0 | 14 | 12 | 5 | — |
| notifications | notifications | 12 | 14 | 8 | 6 | — |

### Validation
`isValidLayoutItem()` (lines 109–120): requires `typeof i === 'string'`, `typeof type === 'string'`, `x/y/w/h` are numbers. Config is not validated (extra fields are tolerated).

### Load/save
- `localStorageLayoutPersistence.load()`: reads `hmt-workspace-layout-v2`, JSON.parse, Array.isArray check, filters via `isValidLayoutItem`, returns null on any failure
- `localStorageLayoutPersistence.save(layout)`: `JSON.stringify(layout)` to key

### API client (frontend/lib/api.ts lines 888–917)
```ts
fetchWorkspaceLayout(): Promise<WorkspaceLayoutItem[] | null>
// GET /api/workspace/layout → data.layout (array) or null

saveWorkspaceLayout(layout: WorkspaceLayoutItem[]): Promise<void>
// PUT /api/workspace/layout, body: { layout }
```

### What changes for multi-page
- localStorage key bumps to `"hmt-workspace-v3"` (or separate key `"hmt-workspace-pages"`)
- Serialized format changes from `WorkspaceLayoutItem[]` to:
  ```ts
  {
    pages: Record<string, { name: string; layout: WorkspaceLayoutItem[] }>;
    activePageId: string;
  }
  ```
- `isValidLayoutItem` reused for individual page layout items
- Need a new `isValidPagesState()` validator
- Validation: require at least 1 page; if `activePageId` not found in pages, fall back to first page id

---

## 3. Backend — workspace_state

**File:** `db/workspace.py` (all functions)

### Table schema (lines 16–23)
```sql
CREATE TABLE IF NOT EXISTS workspace_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```
Standard KV table. One row per key, upsert on conflict.

### Current data shape
Single row: `key = 'layout'`, `value = JSON.stringify(WorkspaceLayoutItem[])`.

### Python functions
```python
load_workspace_layout(db_path) -> list | None
# SELECT value WHERE key='layout'; json.loads(); returns list or None on any error

save_workspace_layout(layout: list, db_path) -> None
# UPSERT key='layout', value=json.dumps(layout)
```

### API endpoints (api/main.py lines 2221–2255)
```
GET /api/workspace/layout   → { layout: load_workspace_layout() }
PUT /api/workspace/layout   → body { layout: [...] } → validates items → save_workspace_layout(layout)
```
Validation (lines 2226–2236): each item must have `str i`, `str type`, `num x,y,w,h`.

### Non-breaking multi-page extension — recommended approach

**Strategy: additive new key `'pages'`; old `'layout'` key stays untouched.**

Add to `db/workspace.py`:
```python
# Serialized shape for 'pages' key:
# {
#   "pages": {
#     "canvas": { "name": "Canvas", "layout": [...] },
#     "setup":  { "name": "Setup",  "layout": [] }
#   },
#   "activePageId": "canvas"
# }

def load_workspace_pages(db_path) -> dict | None:
    # 1. Try SELECT WHERE key='pages'
    # 2. If found → json.loads → return
    # 3. If not found → load_workspace_layout() → wrap:
    #    { pages: { canvas: { name:"Canvas", layout: old_layout } }, activePageId: "canvas" }
    # 4. Immediately save_workspace_pages(wrapped) so next call hits fast path
    # 5. Returns pages dict or None on failure

def save_workspace_pages(pages_state: dict, db_path) -> None:
    # UPSERT key='pages', value=json.dumps(pages_state)
```

Add to `api/main.py`:
```
GET /api/workspace/pages   → { pages_state: load_workspace_pages() }
PUT /api/workspace/pages   → body { pages_state: {...} } → validate → save_workspace_pages(...)
```

Old `GET/PUT /api/workspace/layout` stays in place — no rollback risk, old clients still work.

### Migration concern for founder's existing saved layout
**No breaking migration needed.** On first `GET /api/workspace/pages` after Stage 2 ships:
1. Server finds no `'pages'` key
2. Reads existing `'layout'` key (founder's real saved layout)
3. Wraps it: `{ pages: { canvas: { name: "Canvas", layout: <existing> } }, activePageId: "canvas" }`
4. Immediately writes this to `key='pages'`
5. Returns wrapped state to client
6. Client saves to localStorage under new key; subsequent loads hit the fast path

The old `'layout'` row becomes stale but is never deleted (safe). Rollback: simply remove the `'pages'` key; old endpoints serve the original layout unchanged.

---

## 4. WorkspaceToolbar — structure + the TITLEBAR cluster gap

**File:** `frontend/components/workspace/workspace-toolbar.tsx`

### Actual current structure (line 191)

```
<header h-[52px] relative flex items-center px-3.5>
  LEFT (flex items-center gap-2, min-w-0):
    logo ▰ + "help_me_trade" text
    broker dropdown chip (connected dot + broker name)
    "paper" badge (if isPaper)

  CENTER (absolute left-1/2 -translate-x-1/2, hidden below lg:):
    account selector dropdown (only if >1 account)
    privacy eye toggle
    ToolbarTile PORT
    ToolbarTile BP
    ToolbarTile DAY
    ToolbarTile UPL

  RIGHT (ml-auto flex gap-1.5 md:gap-5):
    NotificationBell
    SettingsDialog (collapsed)
    ThemeSwitcher
    ET clock span
</header>
```

### The PHASE_6_TITLEBAR center cluster — what it was meant to be (Batch 6, §4)

The brief called for **removing the old header row from workspace-grid.tsx** (containing "Workspace" h1 + "default layout" chip + "+ Add widget" button) and moving a compact version into the **toolbar center**, making the center:

> *"Workspace" label + "default layout" chip + lock toggle (Lock/LockOpen icon) + "+" icon button (openPicker)*

The brief also called for restructuring into three zones:
> LEFT = brand + broker + paper; CENTER = workspace cluster; RIGHT = portfolio stats + ThemeSwitcher + clock

**What was actually built (partial implementation):**
- The header row from workspace-grid.tsx WAS deleted (no such strip exists in the current workspace-grid.tsx)
- `locked`/`toggleLock` were added to the store
- `WidgetRemoveButton` confirm-before-close was added to workspace-grid.tsx

**What was NOT built:** The center toolbar zone was never changed — it still shows portfolio stats. Instead, the workspace cluster (label chip + lock + +) ended up in the **command console bar** (command-console.tsx lines 304–334):
```tsx
// Right side of the console form (lines 304–335):
<div className="ml-auto flex items-center gap-2">
  <span className="... md:inline">default layout</span>   // pill label
  <button onClick={toggleLock}>                             // Lock/LockOpen icon
    {locked ? <Lock /> : <LockOpen />}
  </button>
  <button onClick={openPicker}>                            // + Add widget
    <Plus />
  </button>
</div>
```

**Implication for Stage 2:** The page-switcher needs a home. Two options (flag, don't decide — see §Forks below).

### Where page tabs would hook in
- **Option A (matches PHASE_6_TITLEBAR intent):** Move the workspace cluster from the console bar INTO the toolbar center, displacing portfolio stats to the right zone. Center becomes: `[page tabs (cap 5)] [+ new page] [lock]`. Stats move to right (compressed). Toolbar right: stats row + bell + settings + theme + clock.
- **Option B (leave toolbar center alone):** Page tabs stay in the console bar, replacing the "default layout" pill with actual named tabs. Console bar becomes: `› [input] ... [tab1][tab2][+] [lock] [+widget]`. More cramped but zero toolbar rework.

---

## 5. Keybind Infrastructure

### Existing keybinds (ALL are ad-hoc via window.addEventListener — no library)

| Key | Handler file | Line | Action |
|-----|-------------|------|--------|
| `Cmd/Ctrl+K` | command-console.tsx | 173 | Toggle console open/closed |
| `/` (when not in input) | command-console.tsx | 181 | Focus console input |
| `Esc` | command-console.tsx | 161 | Close console + blur input |
| `Esc` | copilot-sidebar.tsx | ~43 | Close copilot sidebar |

No `react-hotkeys-hook` or similar library is installed. The pattern is:
```ts
useEffect(() => {
  function onKeyDown(e: globalThis.KeyboardEvent) { ... }
  window.addEventListener("keydown", onKeyDown);
  return () => window.removeEventListener("keydown", onKeyDown);
}, [deps]);
```

### Clean slots for page hot-swap keybinds

| Candidate binding | Conflicts | Notes |
|---|---|---|
| `Alt+1..5` | None in this codebase | Safe on all OSes; common in ThinkorSwim/desktop apps |
| `Ctrl+1..5` | None in codebase | Browser may use Ctrl+1-8 for tabs on some platforms |
| `Cmd+1..5` | None in codebase | Mac browser uses Cmd+1-8 for tabs — AVOID |
| `Cmd+[` / `Cmd+]` | None in codebase | Mac browser uses for history back/forward — AVOID |
| `Alt+[` / `Alt+]` | None in codebase | Clean; good as prev/next page alternative |

**Recommendation to flag:** `Alt+1-5` for direct page jump + `Alt+[` / `Alt+]` for prev/next — no collisions, consistent with desktop trading app conventions. Hook in cleanly in a new `usePageHotkeys()` hook (same pattern as console listener), called from `workspace/page.tsx` or a new `WorkspacePageProvider`.

---

## 6. Widget Picker + `/add` command

### WorkspaceWidgetPicker (workspace-widget-picker.tsx)

```ts
const layout = useWorkspaceStore((s) => s.layout);        // line 24 — reads active layout
const addWidget = useWorkspaceStore((s) => s.addWidget);  // line 25
const setPickerOpen = useWorkspaceStore((s) => s.setPickerOpen); // line 26
```

**Singleton dedup logic (lines 28–35):**
```ts
const singletonTypesOnBoard = new Set(
  layout.filter(item => !getWidgetDefinition(item.type)?.multiInstance).map(item => item.type)
);
const available = WIDGET_REGISTRY.filter(w => w.multiInstance || !singletonTypesOnBoard.has(w.id));
```

**Multi-page impact:** `layout` selector must change to read the active page's layout. Otherwise a singleton widget on Page 1 would incorrectly be blocked from Page 2.

**Decision: singleton blocking scope = per-page or global?** For Stage 2, per-page (default) is most natural. Call out as a fork only if the founder wants global uniqueness.

### `/add` command (use-workspace-commands.ts lines 139–145)

```ts
case "/add":
  openWidgetPicker();
  return { handled: true, output: "widget picker opened" };
```

**Stage 2 polish** (`/add <widget-id>` direct-add):

The existing command regex/switch already splits `value.split(/\s+/)[0]`. The fix is to intercept before the `switch`:
```ts
// After the existing WATCHLIST_ADD/REMOVE and THEME regexes, before the switch:
const addDirectMatch = /^\/add\s+(\S+)$/.exec(value);
if (addDirectMatch) {
  const widgetId = addDirectMatch[1].toLowerCase();
  const added = addWidget(widgetId);        // addWidget imported from store
  if (added) {
    return { handled: true, output: `added ${widgetId}` };
  }
  // Check if id exists but is a singleton already present:
  const def = getWidgetDefinition(widgetId);
  if (!def) return { handled: true, error: `unknown widget: ${widgetId}. try /add without args` };
  return { handled: true, error: `${widgetId} is already on this page` };
}
```

This also means `addWidget` needs to be accessible inside `useWorkspaceCommands` — it's a store action, add it to the existing `const openWidgetPicker = useWorkspaceStore(s => s.openPicker)` block.

Also add `WIDGET_REGISTRY` ids to `WORKSPACE_COMMANDS` autocomplete list (or generate dynamically from WIDGET_REGISTRY).

---

## 7. PHASE_6 Briefs — Prior Multi-Page Design?

**PHASE_6_CHANNELS_BRIEF.md (Batch 2):**
- "Channels" = color-coded symbol-linking ONLY (watchlist row click → linked chart symbol updates via `useChannelStore.setChannelSymbol`). Six color groups (none + 1-6), session-only in `use-channel-store.ts`.
- Zero multi-page concept. The "channels" are within-page widget sync groups, entirely different from workspace pages.
- Reusable for Stage 2: nothing directly, but the per-page channel state (`use-channel-store.ts`) might eventually want to be per-page (each page gets its own channel symbol state). That's a future enhancement, not needed for Stage 2.

**PHASE_6_GRID_MODEL_BRIEF.md (Batch 9):**
- Free-flowing grid (compactType=null, preventCollision=true), resolution-independent cols via `colsForLayout()`. No multi-page concept.
- Reusable: `colsForLayout` and `gridPixelWidth` work per-layout naturally. For multi-page, each page shares the same grid machinery — just swap `layout` in.

**Conclusion:** No prior multi-page design in any brief. Stage 2 builds the concept from scratch. The "channels" name is firmly claimed by symbol-linking; pages must be called "pages" (already locked by the CONSOLIDATION_PLAN terminology decision).

---

## Recommended Non-Breaking Migration (full path)

### Client-side (frontend)
1. **New types** (in `layout-persistence.ts` or a new `workspace-pages.ts`):
   ```ts
   export type WorkspacePage = { id: string; name: string; layout: WorkspaceLayoutItem[] };
   export type WorkspacePagesState = { pages: Record<string, WorkspacePage>; activePageId: string };
   ```
2. **New localStorage key** `"hmt-workspace-v3"` stores `WorkspacePagesState`.
3. **Store migration in `hydrate()`**: Try `GET /api/workspace/pages`; if missing, fall back to `GET /api/workspace/layout` and wrap. On first successful load, write to new localStorage key.
4. **Wrap the founder's existing single layout as "Canvas" page** on first load, id = `"canvas"`.
5. **Ship a second default page** `"setup"` (id = `"setup"`) with an empty layout (or the start of the Setup page widget set from Stage 4 — just an empty page for now is fine).
6. **Default for brand-new users** (`DEFAULT_WORKSPACE_LAYOUT` → `DEFAULT_PAGES_STATE`): two pages, canvas pre-populated as before.
7. All store actions scope to `pages[activePageId].layout`.

### Server-side (Python + API)
1. Add `load_workspace_pages()` + `save_workspace_pages()` to `db/workspace.py` (new key `'pages'`).
2. `load_workspace_pages()` auto-migrates: reads `'pages'` key if present; else reads `'layout'` key and wraps it into `{ pages: { canvas: { name: "Canvas", layout: old } }, activePageId: "canvas" }`, immediately persists to `'pages'` key.
3. Add `GET /api/workspace/pages` + `PUT /api/workspace/pages` to `api/main.py`.
4. Old `GET/PUT /api/workspace/layout` stay untouched.

---

## Forks to Resolve Before Building

### Fork 1: Page-switcher location in the toolbar — TABS vs DROPDOWN (MEDIUM)
Two options:
- **Tabs** (up to 5 pill/tab buttons, active highlighted) — scannable, ThinkorSwim-like, but cramped if names are long
- **Dropdown select** (label chip with dropdown) — compact, easier to implement, less scannable at a glance

Either lives in either the toolbar center OR the console bar (see Fork 2).

### Fork 2: Toolbar center zone — stats or page cluster (HIGH)
The center zone currently shows portfolio stats (PORT/BP/DAY/UPL). PHASE_6_TITLEBAR intended it for the workspace cluster.

- **Option A:** Move workspace cluster (page tabs + lock + +) INTO toolbar center, displace stats to right zone. Matches original TITLEBAR brief; gives page tabs the most prominent position. Requires rearranging right zone (stats + bell + settings + theme + clock = tight). Responsive concern on smaller screens.
- **Option B:** Leave toolbar center as portfolio stats (they're actually useful there); keep workspace cluster in the console bar (where lock/+ already live). Replace "default layout" pill with actual page tabs in the console bar. No toolbar rework.

### Fork 3: Rename / delete page UX (LOW)
How does the user rename or delete a page?
- Right-click on the tab → context menu (most standard)
- Click the tab label to inline-edit (common in VS Code)
- Settings icon next to the tab → mini dropdown

Any of these work. Call out the choice for the brief.

### Fork 4: Singleton scoping — per-page or global (LOW)
A widget like `positions` (singleton, no multi-instance) — can you add it to Page 2 if it's already on Page 1?
- **Per-page** (recommended default): each page has its own set; same singleton can appear on multiple pages. Most natural for "different views of different things."
- **Global**: one instance of each singleton across all pages. Prevents duplicated live data subscriptions.

### Fork 5: Keybind scheme (LOW)
`Alt+1-5` for direct page jump vs. `Alt+[/]` for prev/next vs. both. No collisions either way. Pick one to document in the brief.

---

## Key File Reference (Stage 2 touch points)

| File | What changes |
|---|---|
| `frontend/lib/workspace/layout-persistence.ts` | Add `WorkspacePage`, `WorkspacePagesState` types; new localStorage key + load/save logic; `DEFAULT_PAGES_STATE` |
| `frontend/lib/workspace/use-workspace-store.ts` | Replace `layout` with `pages + activePageId`; all mutation actions scope to active page; `hydrate()` loads pages; new page CRUD actions; `persistLayout` → `persistPages` |
| `frontend/lib/api.ts` | Add `fetchWorkspacePagesState()` + `saveWorkspacePagesState()` calling new `/api/workspace/pages` endpoints |
| `frontend/components/workspace/workspace-toolbar.tsx` | Add page-switcher UI (tabs or dropdown) — location depends on Fork 2; responsive |
| `frontend/components/workspace/command-console.tsx` | If option B: replace "default layout" pill with page tabs here instead |
| `frontend/components/workspace/workspace-widget-picker.tsx` | Change `layout` read to `pages[activePageId].layout` for singleton dedup |
| `frontend/lib/workspace/use-workspace-commands.ts` | Add `/add <widget-id>` direct-add path; add `addWidget` from store; extend autocomplete list |
| `frontend/app/workspace/page.tsx` | Add `usePageHotkeys()` hook call (or inline in this file) |
| `db/workspace.py` | Add `load_workspace_pages()` + `save_workspace_pages()` |
| `api/main.py` | Add `GET/PUT /api/workspace/pages` endpoints |
