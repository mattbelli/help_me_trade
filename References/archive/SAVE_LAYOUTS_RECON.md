# SAVE_LAYOUTS_RECON.md
*Recon only — 2026-06-30. Read-only. No code changed.*
*Companion: `CONSOLIDATION_S2_RECON.md` (Stage 2 map), `CONSOLIDATION_PLAN.md` (overall plan).*

---

## Current state: Stage 2 is fully shipped

All multi-page infrastructure is live. Do NOT re-implement it.

| Subsystem | Status |
|---|---|
| `pages: Record<string, WorkspacePage>` + `activePageId` in store | ✅ shipped |
| `addPage`, `renamePage`, `deletePage`, `setActivePage` actions | ✅ shipped |
| localStorage `"hmt-workspace-v3"` via `localStoragePagesPersistence` | ✅ shipped |
| `fetchWorkspacePages` / `saveWorkspacePages` in api.ts | ✅ shipped |
| `GET/PUT /api/workspace/pages` in api/main.py | ✅ shipped |
| `load_workspace_pages` / `save_workspace_pages` in db/workspace.py | ✅ shipped |
| `PageSwitcher` pill tabs + right-click context menu (Rename, Delete) | ✅ shipped |

---

## 1. Store — `frontend/lib/workspace/use-workspace-store.ts`

### Current shape (lines 28–52)

```ts
type WorkspaceState = {
  pages: Record<string, WorkspacePage>;   // line 29
  activePageId: string;                   // line 30
  hydrated: boolean;                      // line 31
  locked: boolean;                        // line 32
  pickerOpen: boolean;                    // line 33
  gridCols: number;                       // line 34
  hydrate: () => Promise<void>;           // line 35
  getActiveLayout: () => WorkspaceLayoutItem[];  // line 36
  setGridCols: (cols: number) => void;
  updateLayout: (layout: WorkspaceLayoutItem[]) => void;
  addWidget: (type: string) => boolean;
  updateWidgetConfig: (instanceId: string, patch: Partial<WidgetInstanceConfig>) => void;
  removeWidget: (id: string) => void;
  addPage: (name?: string) => string | null;
  renamePage: (id: string, name: string) => void;
  deletePage: (id: string) => boolean;
  setActivePage: (id: string) => void;
  toggleLock: () => void;
  setPickerOpen: (open: boolean) => void;
  openPicker: () => void;
};
```

Initial values (lines 193–199): `pages: DEFAULT_PAGES_STATE.pages`, `activePageId: DEFAULT_PAGES_STATE.activePageId`, `hydrated: false`, `locked: false`, `pickerOpen: false`, `gridCols: GRID_DEFAULT_COLS`.

`persistPages(pagesState)` (lines 187–190): calls `localStoragePagesPersistence.save(state)` immediately + `scheduleServerSave(state)` (500 ms debounce, module-level timer). Every mutation action calls this.

### What to add

**New state fields** (add to `WorkspaceState` type and initial values):

```ts
savedPageTemplates: SavedPageTemplate[];         // starts as []
savedWorkspaces: SavedWorkspace[];               // starts as []
workspaceDirty: boolean;                         // starts as false
lastLoadedWorkspaceId: string | null;            // starts as null
```

**New actions** (add to type + implementation):

```ts
hydrateLibraries: () => Promise<void>;
// Fetches saved-pages + saved-workspaces from server.
// Separate from hydrate() to keep the main boot path lean.
// Falls back to localStorage on failure; no-op if already loaded.

savePageTemplate: (pageId: string, name: string) => void;
// Snapshot pages[pageId].layout → new SavedPageTemplate { id: uuid, name, layout, savedAt }.
// Prepend to savedPageTemplates. Persist libraries.

loadPageTemplate: (templateId: string, intoPageId: string) => void;
// COPY template.layout into pages[intoPageId].layout (page slot must exist).
// Does NOT set workspaceDirty = false (template load IS a mutation).
// Calls persistPages. Then persistLibraries (templates list unchanged, no save needed).

deletePageTemplate: (id: string) => void;
// Filter savedPageTemplates. Persist libraries.

renamePageTemplate: (id: string, name: string) => void;
// Map savedPageTemplates. Persist libraries.

saveWorkspace: (name: string) => string;
// Snapshot current { pages, activePageId } → new SavedWorkspace { id, name, pages, activePageId, savedAt }.
// Prepend to savedWorkspaces. Set workspaceDirty = false. Set lastLoadedWorkspaceId = new id.
// Persist libraries. Return new id.

loadWorkspace: (id: string) => boolean;
// Caller must check workspaceDirty and show confirm before calling if dirty.
// (Or: loadWorkspace returns false if dirty + caller confirms separately.)
// Snapshot: replace pages + activePageId from savedWorkspaces[id].pages + .activePageId.
// Set workspaceDirty = false. Set lastLoadedWorkspaceId = id.
// Calls persistPages (saves the now-active workspace to the live slot).

deleteWorkspace: (id: string) => void;
// Filter savedWorkspaces. If lastLoadedWorkspaceId === id, reset to null. Persist libraries.

renameWorkspace: (id: string, name: string) => void;
// Map savedWorkspaces. Persist libraries.
```

**`workspaceDirty` tracking**: Set `workspaceDirty = true` inside every action that calls `persistPages`:
`updateLayout`, `addWidget`, `updateWidgetConfig`, `removeWidget`, `addPage`, `renamePage`, `deletePage`, `setActivePage`.
Reset `workspaceDirty = false` in `saveWorkspace` and `loadWorkspace`.

**`persistLibraries` helper** (module-level, alongside `persistPages`):
```ts
function persistLibraries(savedPageTemplates: SavedPageTemplate[], savedWorkspaces: SavedWorkspace[]): void {
  localStorageSavedPagesPersistence.save(savedPageTemplates);
  localStorageSavedWorkspacesPersistence.save(savedWorkspaces);
  scheduleLibraryServerSave(savedPageTemplates, savedWorkspaces);
  // or two separate debounce timers, one per library
}
```

---

## 2. Persistence + API client

### `frontend/lib/workspace/layout-persistence.ts`

**New types** — add after `WorkspacePagesState` (line 36):

```ts
export type SavedPageTemplate = {
  id: string;          // crypto.randomUUID()
  name: string;        // user-given name
  layout: WorkspaceLayoutItem[];
  savedAt: string;     // ISO 8601
};

export type SavedWorkspace = {
  id: string;
  name: string;
  pages: Record<string, WorkspacePage>;   // same shape as WorkspacePagesState.pages
  activePageId: string;
  savedAt: string;
};
```

**New localStorage keys + persistence objects** — add after `localStoragePagesPersistence` (line 220):

```ts
const STORAGE_KEY_SAVED_PAGES     = "hmt-saved-pages";       // SavedPageTemplate[]
const STORAGE_KEY_SAVED_WORKSPACES = "hmt-saved-workspaces"; // SavedWorkspace[]

export const localStorageSavedPagesPersistence = {
  load(): SavedPageTemplate[] | null { ... },
  save(templates: SavedPageTemplate[]): void { ... },
};

export const localStorageSavedWorkspacesPersistence = {
  load(): SavedWorkspace[] | null { ... },
  save(workspaces: SavedWorkspace[]): void { ... },
};
```

Validation for both: `Array.isArray`, each item has `id: string`, `name: string`, `savedAt: string`, `layout: WorkspaceLayoutItem[]` or `pages: Record<string, WorkspacePage>`.

### `frontend/lib/api.ts`

**New imports** — add `SavedPageTemplate`, `SavedWorkspace` to the import from `./workspace/layout-persistence` (line 2).

**New functions** — add after `saveWorkspacePages` (line 953):

```ts
export async function fetchSavedPageTemplates(): Promise<SavedPageTemplate[] | null>
// GET /api/workspace/saved-pages → data.templates (array) or null

export async function saveSavedPageTemplates(templates: SavedPageTemplate[]): Promise<void>
// PUT /api/workspace/saved-pages, body: { templates }

export async function fetchSavedWorkspaces(): Promise<SavedWorkspace[] | null>
// GET /api/workspace/saved-workspaces → data.workspaces (array) or null

export async function saveSavedWorkspaces(workspaces: SavedWorkspace[]): Promise<void>
// PUT /api/workspace/saved-workspaces, body: { workspaces }
```

Debounce pattern: same 500 ms module-level timer as `scheduleServerSave` in the store.

---

## 3. Backend

### `db/workspace.py`

Existing table `workspace_state` (key TEXT PK, value TEXT, updated_at TEXT) handles everything — no new tables needed.

**New KV keys:**
- `'saved_pages'` → stores `JSON.dumps(list[SavedPageTemplate])` — a flat list of template dicts
- `'saved_workspaces'` → stores `JSON.dumps(list[SavedWorkspace])` — a flat list of workspace snapshot dicts

**New functions** (add after `save_workspace_pages`, line 155):

```python
def load_saved_page_templates(db_path: Path = DEFAULT_DB_PATH) -> list:
    """Return stored page templates list, or [] on absent/invalid (fail-soft)."""
    try:
        init_workspace_tables(db_path)
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM workspace_state WHERE key = 'saved_pages'"
            ).fetchone()
        if row is None:
            return []
        parsed = json.loads(row[0])
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def save_saved_page_templates(templates: list, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Upsert saved page templates. Last-write-wins."""
    init_workspace_tables(db_path)
    now = datetime.now(UTC).isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO workspace_state (key, value, updated_at) VALUES ('saved_pages', ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (json.dumps(templates), now),
        )
        conn.commit()


def load_saved_workspaces(db_path: Path = DEFAULT_DB_PATH) -> list:
    # Same pattern, key = 'saved_workspaces'


def save_saved_workspaces(workspaces: list, db_path: Path = DEFAULT_DB_PATH) -> None:
    # Same pattern, key = 'saved_workspaces'
```

Validation: kept lightweight on the server (trusts the client; validates only array type). No deep schema check needed for saved libraries — the client owns the structure.

### `api/main.py`

**New imports** — add to the import from `db.workspace` (line 103):
```python
from db.workspace import (
    ...,
    load_saved_page_templates,
    save_saved_page_templates,
    load_saved_workspaces,
    save_saved_workspaces,
)
```

**New endpoints** (add after `api_put_workspace_pages`, line 2308):

```python
@app.get("/api/workspace/saved-pages")
def api_get_saved_pages():
    return {"templates": load_saved_page_templates()}

@app.put("/api/workspace/saved-pages")
def api_put_saved_pages(body: dict = Body(...)):
    templates = body.get("templates")
    if not isinstance(templates, list):
        return JSONResponse({"error": "templates must be an array"}, status_code=400)
    save_saved_page_templates(templates)
    return {"ok": True}

@app.get("/api/workspace/saved-workspaces")
def api_get_saved_workspaces():
    return {"workspaces": load_saved_workspaces()}

@app.put("/api/workspace/saved-workspaces")
def api_put_saved_workspaces(body: dict = Body(...)):
    workspaces = body.get("workspaces")
    if not isinstance(workspaces, list):
        return JSONResponse({"error": "workspaces must be an array"}, status_code=400)
    save_saved_workspaces(workspaces)
    return {"ok": True}
```

All four endpoints are additive. Old endpoints untouched.

---

## 4. UI hooks

### `frontend/components/workspace/page-switcher.tsx`

**Current context menu structure** (lines 273–296): a `div.min-w-28.p-1.font-mono` containing:
- `<button>Rename</button>` (line 276)
- `<button>Delete</button>` (line 284, only if `canDelete`)
- Replaced by `<PageDeleteConfirm>` when `confirmDeleteId === contextMenu.pageId`

**Where to add:**

```
[Rename]
[Save as template…]   ← NEW
[Load template…]      ← NEW
[Delete]
```

**Implementation pattern for "Save as template…":**
Add a new state `saveTemplateForPageId: string | null` (alongside `confirmDeleteId`). When the user clicks "Save as template…", set `saveTemplateForPageId = contextMenu.pageId` and render a `TemplateSavePrompt` component in place of the menu content — same pattern as `PageDeleteConfirm` (lines 24–56). `TemplateSavePrompt` contains an `<input>` pre-filled with `page.name`, Enter commits `savePageTemplate(pageId, name)`, Escape cancels.

**Implementation pattern for "Load template…":**
Add a new state `loadTemplateForPageId: string | null`. When clicked, render a `TemplatePickerPanel` in place of the menu content — a scrollable list of `savedPageTemplates` (name + savedAt). Click on one calls `loadPageTemplate(templateId, pageId)` and closes. If `savedPageTemplates` is empty, show "No saved templates yet."

Both panels reuse the inline-replace pattern of `PageDeleteConfirm` so no new overlay/portal is needed. The menu popup is already `fixed z-[60]`.

**Inline rename pattern to reuse** (PagePill, lines 106–119):
```tsx
<input
  type="text"
  value={draft}
  onChange={(e) => setDraft(e.target.value)}
  onKeyDown={onKeyDown}  // Enter → commit, Esc → cancel
  onBlur={commit}
  className="max-w-[7rem] rounded-full border border-[var(--c-accent)] bg-[var(--bg-inset)] px-2 py-0.5 font-mono text-[10px] text-foreground outline-none"
/>
```
Mirror this exactly for the template name input inside `TemplateSavePrompt`.

### `frontend/components/workspace/workspace-toolbar.tsx` + `command-console.tsx`

**Current toolbar right zone** (line 391):
```tsx
<div className="ml-auto flex items-center gap-1.5 md:gap-5">
  <NotificationBell />
  <SettingsDialog collapsed />
  <ThemeSwitcher />
  <span ...>{clock}</span>
</div>
```

**Current console bar right cluster** (command-console.tsx line 334):
```tsx
<div className="ml-auto flex items-center gap-2">
  <PageSwitcher />
  <button onClick={toggleLock}>...</button>
  <button onClick={openPicker}>...</button>
</div>
```

**Where to put the workspace switcher** — see Fork 1 below. The recommended location is the **console bar**, immediately before `<PageSwitcher />`, so the cluster reads:
```tsx
<WorkspaceSwitcher />
<PageSwitcher />
[Lock]
[+ widget]
```

This keeps the workspace context (saved workspaces selector) adjacent to the page context (page tabs) without touching the toolbar's portfolio stats center or the compact right zone.

**WorkspaceSwitcher component** — new file `frontend/components/workspace/workspace-switcher.tsx`:

Reads `savedWorkspaces`, `workspaceDirty`, `lastLoadedWorkspaceId` from store. Uses the existing `DropdownMenu` pattern from `workspace-toolbar.tsx` (lines 208–294):

```tsx
<DropdownMenu>
  <DropdownMenuTrigger className="...pill chip...">
    {currentWorkspaceName}  {/* the name of lastLoadedWorkspaceId, or "Workspace" */}
  </DropdownMenuTrigger>
  <DropdownMenuContent side="bottom" align="end">
    {/* section: saved workspaces */}
    {savedWorkspaces.map(ws => (
      <DropdownMenuItem key={ws.id} onClick={() => handleLoadWorkspace(ws.id)}>
        {ws.name}
      </DropdownMenuItem>
    ))}
    {savedWorkspaces.length === 0 && (
      <div className="px-2 py-1 text-[10px] text-[var(--fg-faint)]">No saved workspaces</div>
    )}
    {/* divider + save action */}
    <DropdownMenuItem onClick={() => setSaveMode(true)}>
      Save workspace as…
    </DropdownMenuItem>
  </DropdownMenuContent>
</DropdownMenu>
```

When `saveMode` is true, replace DropdownMenuContent interior with a name `<input>` (same inline-rename pattern).

The `handleLoadWorkspace` handler: if `workspaceDirty`, show a `Dialog` confirm ("Replace current workspace? Unsaved changes will be lost.") before calling `loadWorkspace(id)`. Use the `Dialog` / `DialogContent` component from `@/components/ui/dialog`.

**Reusable UI primitives summary:**

| Primitive | File | Used for |
|---|---|---|
| `DropdownMenu` / `DropdownMenuTrigger` / `DropdownMenuContent` / `DropdownMenuItem` | `components/ui/dropdown-menu.tsx` | WorkspaceSwitcher dropdown |
| `Dialog` / `DialogContent` / `DialogClose` | `components/ui/dialog.tsx` | "Replace workspace?" confirm |
| `PageDeleteConfirm` pattern (inline panel swap) | `page-switcher.tsx:24–56` | TemplateSavePrompt, TemplatePickerPanel in context menu |
| `PagePill` inline `<input>` rename | `page-switcher.tsx:106–119` | Template name input, workspace save name input |
| `contextMenu`/`menuRef` + `pointerdown` dismiss | `page-switcher.tsx:164–188` | Already in place — no changes needed |

---

## 5. Data models (KV shapes)

### Saved Page Templates

**localStorage key**: `"hmt-saved-pages"` → `SavedPageTemplate[]`

**Server KV key**: `'saved_pages'` → same list JSON-encoded

```ts
type SavedPageTemplate = {
  id: string;                        // crypto.randomUUID()
  name: string;                      // e.g. "home base"
  layout: WorkspaceLayoutItem[];     // frozen snapshot
  savedAt: string;                   // ISO 8601
};
```

Stored as an array (not a Record) — ordering is insertion order (newest first via prepend). Unlimited. Loaded on first open of the library picker (lazy via `hydrateLibraries`).

### Saved Workspaces

**localStorage key**: `"hmt-saved-workspaces"` → `SavedWorkspace[]`

**Server KV key**: `'saved_workspaces'` → same list JSON-encoded

```ts
type SavedWorkspace = {
  id: string;
  name: string;
  pages: Record<string, WorkspacePage>;   // exact same shape as WorkspacePagesState.pages
  activePageId: string;
  savedAt: string;
};
```

Same conventions: array, newest first, unlimited, lazy-loaded.

---

## 6. Dirty-tracking recommendation

**Recommended approach: dirty flag (Option B)**

Add `workspaceDirty: boolean` (initial `false`) + `lastLoadedWorkspaceId: string | null` (initial `null`) to store state.

Set `workspaceDirty = true` in every action that calls `persistPages` (all widget mutations + page CRUD).
Reset `workspaceDirty = false` only in `saveWorkspace` and `loadWorkspace`.

Gate `loadWorkspace` in the UI (in `WorkspaceSwitcher.handleLoadWorkspace`): if `workspaceDirty`, open a `Dialog` confirm before dispatching `store.loadWorkspace(id)`. The action itself does NOT confirm — it just executes. Dirty-checking is a UI concern.

**Why not Option A (hash comparison):** The dirty flag is simpler and sufficient. There's no undo in this app, so no false positives from "you changed then changed back." The only false positive scenario is renaming a page and then renaming it back — acceptable.

**Why `lastLoadedWorkspaceId`:** Lets the `WorkspaceSwitcher` chip display the name of the currently loaded saved workspace (e.g. "home base" instead of generic "Workspace"). When the user saves a new workspace, set this to the new ID. When they modify and the dirty flag is set, the chip can show "home base*" or just show "Workspace" — implementation choice.

---

## 7. Forks

### Fork 1: Workspace switcher location — console bar vs. toolbar (MEDIUM)
- **Option A (recommended): Console bar** — before `<PageSwitcher />` in command-console.tsx line 334. Clusters workspace-level control (saved workspaces) next to page-level control (page tabs). Zero toolbar rework.
- **Option B: Toolbar right** — before `<NotificationBell />` in workspace-toolbar.tsx line 391. Slightly more prominent; more cramped on small screens; requires touching the toolbar.

### Fork 2: `hydrateLibraries` call site — on boot vs. lazy (LOW)
- **On boot** (inside `hydrate()`): one extra parallel fetch at startup. Simplest — libraries are immediately available.
- **Lazy** (first time the library UI opens): lighter boot. More complexity (loading state inside the picker).
- Recommendation: boot-time, keep it simple. Two extra tiny fetches do not block page render.

### Fork 3: WorkspaceSwitcher save-name input — inline in dropdown vs. separate Dialog (LOW)
- **Inline** (replace dropdown content with an `<input>`): consistent with PageDeleteConfirm / TemplateSavePrompt pattern; no extra overlay.
- **Dialog**: standard form UX; heavier.
- Recommendation: inline (consistent with the rest of the component family).

### Fork 4: "Load template" confirm (LOW)
Loading a template replaces the entire layout of the target page slot. If that page has widgets, show a brief confirm (same inline panel-swap pattern as PageDeleteConfirm). Alternatively, skip the confirm if the page is empty, confirm only if non-empty — same logic as the existing `handleDelete` (page-switcher.tsx line 199–213).

---

## 8. Touch-point summary

| File | What to add | Where |
|---|---|---|
| `frontend/lib/workspace/layout-persistence.ts` | `SavedPageTemplate`, `SavedWorkspace` types; `localStorageSavedPagesPersistence`, `localStorageSavedWorkspacesPersistence` | After line 36 (types), after line 220 (objects) |
| `frontend/lib/workspace/use-workspace-store.ts` | `savedPageTemplates`, `savedWorkspaces`, `workspaceDirty`, `lastLoadedWorkspaceId` state; 8 new actions; `persistLibraries` helper; set dirty in all mutation actions | After line 51 (type), after line 199 (initial), throughout mutation actions |
| `frontend/lib/api.ts` | `fetchSavedPageTemplates`, `saveSavedPageTemplates`, `fetchSavedWorkspaces`, `saveSavedWorkspaces` | After line 953 |
| `db/workspace.py` | `load_saved_page_templates`, `save_saved_page_templates`, `load_saved_workspaces`, `save_saved_workspaces` | After line 155 |
| `api/main.py` | 4 new endpoints + import additions | After line 2308; import line 103 |
| `frontend/components/workspace/page-switcher.tsx` | "Save as template…" + "Load template…" menu items + `TemplateSavePrompt` + `TemplatePickerPanel` state/components | Context menu div, line 273+ |
| `frontend/components/workspace/command-console.tsx` | `<WorkspaceSwitcher />` before `<PageSwitcher />` | Line 334 |
| `frontend/components/workspace/workspace-switcher.tsx` | NEW component — the workspace dropdown chip | New file |
