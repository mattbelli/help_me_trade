# Workspaces & saved pages — spec

*Status: BUILT 2026-07-14 (branch `feat/workspaces`) — founder review pending. Server sync
activates at the next hmt-backend restart (new endpoints); until then workspaces run
per-device off localStorage with the legacy pages PUT keeping the server blob fresh.
Written from the founder's ask:*
"save individual pages, and save groups of pages, which would be called workspaces. If I'm on my
widescreen monitor I could open a specific workspace for that; on my laptop, a space for that."*

## What exists today (the foundation)

- A **page** is already a named grid of widgets: `WorkspacePage { id, name, layout }`
  (`frontend/lib/workspace/layout-persistence.ts`). Pages are created (`+ pg`), renamed
  (double-click / context menu), and deleted in the bottom-strip `PageSwitcher`; cap 5.
- The full set of pages autosaves as one blob `WorkspacePagesState { pages, activePageId }` to
  localStorage (`hmt-workspace-v3`) **and** the server (`PUT /api/workspace/pages`, 500 ms
  debounce, flush on tab-hide) via `useWorkspaceStore.persistPages`. Hydration is server-first.

So "save individual pages" is 80% built — pages persist and have names. What's missing:
1. **Workspaces**: a named *group of pages* you can switch between.
2. **Per-device choice**: the widescreen picks workspace A, the laptop workspace B — without the
   two devices fighting over one global "active" pointer.
3. **Page reuse**: get a page you built in one workspace into another (the useful remainder of
   "save individual pages" — duplicate + copy-to-workspace).

## Design

### Model
```
WorkspaceSnapshot { id: string; name: string; pages_state: WorkspacePagesState }
WorkspacesState   { workspaces: Record<id, WorkspaceSnapshot>; default_workspace_id: string }
```
- The **live editing state is always the active workspace's `pages_state`** — same autosave
  debounce as today, just written under `workspaces[activeId].pages_state`. No explicit "save"
  step, no dirty/synced divergence: a workspace is always exactly what you last left it.
- **Active workspace is per-device**: `localStorage["hmt-active-workspace"]`. The server keeps
  only `default_workspace_id` (used by a fresh device / after clearing storage). This is the
  monitor-vs-laptop unlock — each device remembers its own workspace, all content is shared.
- Caps: 8 workspaces × existing 5 pages. Deleting the last workspace is blocked; deleting the
  active one switches to the default.

### Server
- Storage joins the existing settings blob (same pattern as watchlist settings + custom themes):
  `GET/PUT /api/workspace/workspaces` ⇄ `{ workspaces_state }`, normalized/validated in
  `config/settings.py` (unknown keys preserved).
- **Migration**: on first GET after deploy, if no workspaces exist but `/api/workspace/pages`
  has a pages blob, wrap it as workspace `ws-default` named "Main" and return that. The old
  `/api/workspace/pages` endpoint stays as a read/write alias for the default workspace until
  the mobile shell and any stragglers are repointed (grep before removing).

### Frontend
- `useWorkspaceStore` grows `workspaces`, `activeWorkspaceId`, `switchWorkspace(id)`,
  `addWorkspace(name, {duplicateCurrent})`, `renameWorkspace`, `deleteWorkspace`,
  `setDefaultWorkspace`. `persistPages` writes into the active workspace and PUTs the whole
  `workspaces_state` (debounced, unchanged cadence). `switchWorkspace` flushes the pending save
  first, then swaps `pages_state` in (no refetch needed — the store holds all workspaces; they
  are small).
- **UI — workspace selector** in the bottom strip, left of the page pills (that's where page
  navigation already lives): current workspace name as a flat lowercase word + chevron.
  Menu: workspace list (click = switch, ✓ on active, ★ on default), `new workspace`
  (empty), `duplicate current`, context menu per row: rename / set default / delete.
  Styling follows the strip's existing PageSwitcher idiom — words, not pills.
- **Page reuse**: PageSwitcher context menu gains `duplicate page` (into this workspace, subject
  to cap) and `copy to workspace →` (submenu of other workspaces; lands as last page, auto-
  renamed "<name> 2" on collision). This covers "save individual pages" without a separate
  library concept; a standalone page library can layer on later if wanted.

### Out of scope (deliberately)
- Auto-selecting a workspace by screen size (founder picks manually; revisit if he asks).
- Sharing/export, versioning/undo, per-widget-config overrides per workspace.
- Mobile: the phone keeps its bottom-tab shell; workspaces are a desktop-grid concept. The
  mobile watch tab keeps reading the default workspace's first page (today's behavior via the
  pages alias).

## Verify
- Unit: settings normalize/migration round-trip tests (new + legacy blob).
- Manual: create "widescreen" + "laptop" workspaces on two browser profiles, confirm each
  remembers its own active workspace and edits sync to both; kill localStorage → lands on
  default; copy a page across workspaces; delete active workspace.
