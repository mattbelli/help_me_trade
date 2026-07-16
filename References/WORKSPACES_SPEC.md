# Workspaces & saved pages — spec

*Status: v2 BUILT 2026-07-14 (page LIBRARY + linked workspaces, branch `feat/page-library`) —
founder review pending. Server sync activates at the next hmt-backend restart (new endpoints);
until then workspaces run per-device off localStorage with the legacy pages PUT keeping the
server blob fresh. Written from the founder's asks:*
"save individual pages, and save groups of pages, which would be called workspaces. If I'm on my
widescreen monitor I could open a specific workspace for that; on my laptop, a space for that."
*and (v2, same morning):* "I want the user to be able to name pages and save those independent
of workspaces — then be able to load those pages into a workspace and save that workspace.
Pages are a sub-genre of workspaces." *Founder calls: pages are LINKED (edits apply everywhere,
with a warning + an option to detach a copy); manager UI = expanding bottom-strip menu.*

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

### Model (v2 — normalized: page library + linked references)
```
WorkspacePage     { id; name; layout }                       // first-class, library-owned
WorkspaceSnapshot { id; name; pageIds: string[]; activePageId }
WorkspacesState   { version: 2; pages: Record<id, WorkspacePage>;   // the LIBRARY
                    workspaces: Record<id, WorkspaceSnapshot>; defaultWorkspaceId }
```
- **Pages are LINKED, not owned** (founder call): a workspace is an ordered list of pageIds
  into the shared library. The same page in two workspaces is ONE object — edit it anywhere,
  every workspace sees it. The escape hatch is `forkPage` ("detach a copy here"): clones the
  page and swaps the reference in the current workspace only.
- **Shared-page warning**: one-time per page per device (localStorage
  `hmt-shared-page-notice-seen`) — landing on a page that's in ≥2 workspaces shows a thin
  notice above the input strip ("edits here apply to all of them · detach a copy here · ✕"),
  NOT a per-edit nag.
- The **live editing state is the active workspace's resolved pages** — same autosave debounce;
  every persist folds edits back into the library + the workspace's pageIds.
- **Removal is two distinct verbs**: pill ✕ / "Remove from workspace" drops the reference (page
  stays in the library — recoverable, so no confirm); "Delete everywhere" removes it from the
  library and every workspace (confirm; refused while it's some workspace's ONLY page).
- **Active workspace is per-device**: `localStorage["hmt-active-workspace"]`. The server keeps
  only `defaultWorkspaceId` (used by a fresh device / after clearing storage). This is the
  monitor-vs-laptop unlock — each device remembers its own workspace, all content is shared.
- Caps: 8 workspaces × 5 pages each, 24 library pages. Deleting the last workspace is blocked;
  deleting the active one switches to the default (its pages stay in the library). Duplicating
  a workspace deep-copies its pages into NEW library pages (a variant must not edit the
  originals — that's what linking is for).
- **Migrations** (run once, both sides): v1 blob (embedded `pages_state` per workspace, lived
  one night) → hoist pages into the library, ids kept where unique else `pg-N`; legacy pages
  blob → wrap as single "Main" workspace. localStorage `hmt-workspaces-v1` → `hmt-workspaces-v2`
  the same way.

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
- **UI — expanding manager** (founder call: expandable menu, not a floating modal): the
  workspace name in the bottom strip expands upward into a two-pane panel
  (`workspace-switcher.tsx`, 560px × ≤60vh). Left pane = workspaces: click = switch, ✓ active,
  ★ default (hover to set), page count, hover-reveal rename/delete, footer New + Duplicate.
  Right pane = pages of the active workspace (✓ active page, amber `shared` tag when in ≥2
  workspaces, hover ↑↓ reorder / rename / ✕ remove) + **Library** below it: every saved page
  not in this workspace ("in N" usage tag, click = add linked, rename, trash = delete
  everywhere with inline confirm).
- **Page reuse inline** (so the panel is rarely needed): the `+ pg` button becomes a menu when
  the library has loadable pages — "Blank page" / "From library ▸"; the pill context menu gains
  `Duplicate` (copy into this workspace), `Detach a copy here` (shared pages only),
  `Remove from workspace`, and `Delete everywhere`.

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
