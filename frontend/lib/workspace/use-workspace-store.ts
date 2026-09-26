"use client";

import { create } from "zustand";

import { CHART_TIMEFRAMES } from "@/lib/chart-time";
import { fetchWorkspaces, saveWorkspacePages, saveWorkspaces } from "@/lib/api";

import {
  DEFAULT_PAGES_STATE,
  DEFAULT_WORKSPACE_LAYOUT,
  loadActiveWorkspaceId,
  loadLegacyV2Layout,
  localStoragePagesPersistence,
  localStorageWorkspacesPersistence,
  MAX_LIBRARY_PAGES,
  MAX_PAGES_PER_WORKSPACE,
  MAX_WORKSPACES,
  pagesStateForWorkspace,
  saveActiveWorkspaceId,
  wrapLayoutAsPagesState,
  wrapPagesAsWorkspacesState,
  type WidgetInstanceConfig,
  type WorkspaceLayoutItem,
  type WorkspacePage,
  type WorkspacePagesState,
  type WorkspaceSnapshot,
  type WorkspacesState,
} from "./layout-persistence";
import { useChannelStore } from "./use-channel-store";
import { GRID_DEFAULT_COLS } from "./grid-metrics";
import {
  getWidgetDefinition,
  type WidgetDefaultSize,
} from "./widget-registry";

const LOCKED_STORAGE_KEY = "hmt-workspace-locked";

/**
 * Sentinel `activePageId` for the built-in Strategy Lab view. It is NOT a real
 * page: it never appears in `pages`/`pageLibrary`, never counts against
 * MAX_PAGES_PER_WORKSPACE, is exempt from all page CRUD, and is never persisted
 * (activating the Lab is an ephemeral, in-memory switch — see setActivePage).
 * The page switcher renders a hardcoded pill for it before the user pages, and
 * workspace-page.tsx renders the Lab full-bleed when it is the active id.
 */
export const LAB_PAGE_ID = "__lab__";

/** Inclusive grid-row range currently visible in the scroll viewport. */
type VisibleRows = { top: number; bottom: number };

type WorkspaceState = {
  pages: Record<string, WorkspacePage>;
  activePageId: string;
  // The page LIBRARY: every saved page, independent of workspaces. Workspaces
  // reference library pages by id (linked — the same page in two workspaces is
  // one object). `pages`/`activePageId` above are the LIVE editing view of the
  // active workspace; every persist folds edits back into the library and the
  // active workspace's pageIds.
  pageLibrary: Record<string, WorkspacePage>;
  workspaces: Record<string, WorkspaceSnapshot>;
  activeWorkspaceId: string;
  defaultWorkspaceId: string;
  hydrated: boolean;
  locked: boolean;
  pickerOpen: boolean;
  gridCols: number;
  visibleRows: VisibleRows | null;
  focusRequest: { instanceId: string; nonce: number } | null;
  hydrate: () => Promise<void>;
  getActiveLayout: () => WorkspaceLayoutItem[];
  setGridCols: (cols: number) => void;
  setVisibleRows: (rows: VisibleRows | null) => void;
  updateLayout: (layout: WorkspaceLayoutItem[], pageId?: string) => void;
  addWidget: (type: string) => boolean;
  /** Alt-drag copy: clone `instanceId` at the dropped cell. Refuses for
      single-instance widgets. */
  duplicateWidget: (
    instanceId: string,
    at: { x: number; y: number },
    pageId?: string,
  ) => boolean;
  updateWidgetConfig: (
    instanceId: string,
    patch: Partial<WidgetInstanceConfig>,
    pageId?: string,
  ) => void;
  removeWidget: (id: string, pageId?: string) => void;
  addPage: (name?: string) => string | null;
  addPageFromLibrary: (id: string) => boolean;
  renamePage: (id: string, name: string) => void;
  /** Drop the page from the active workspace — it stays in the library. */
  removePageFromWorkspace: (id: string) => boolean;
  /** Delete from the library and every workspace. Refuses if it's the only
      page of some workspace. */
  deletePageEverywhere: (id: string) => boolean;
  duplicatePage: (id: string) => string | null;
  /** Detach a private copy for the active workspace: clones the page and swaps
      the reference here; other workspaces keep the original. */
  forkPage: (id: string) => string | null;
  movePage: (id: string, dir: -1 | 1) => void;
  setActivePage: (id: string) => void;
  switchWorkspace: (id: string) => void;
  addWorkspace: (name: string, duplicateCurrent?: boolean) => string | null;
  renameWorkspace: (id: string, name: string) => void;
  deleteWorkspace: (id: string) => boolean;
  setDefaultWorkspace: (id: string) => void;
  toggleLock: () => void;
  setPickerOpen: (open: boolean) => void;
  openPicker: () => void;
  focusWidget: (type: string) => boolean;
  focusSymbol: (symbol: string) => boolean;
  clearFocusRequest: () => void;
};

function loadLocked(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(LOCKED_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function saveLocked(locked: boolean): void {
  try {
    localStorage.setItem(LOCKED_STORAGE_KEY, locked ? "true" : "false");
  } catch {
    // ignore quota / private mode
  }
}

function rectsOverlap(
  a: { x: number; y: number; w: number; h: number },
  b: { x: number; y: number; w: number; h: number },
): boolean {
  return !(
    a.x + a.w <= b.x ||
    b.x + b.w <= a.x ||
    a.y + a.h <= b.y ||
    b.y + b.h <= a.y
  );
}

function findPlacement(
  layout: WorkspaceLayoutItem[],
  size: WidgetDefaultSize,
  cols: number,
  visibleRows?: VisibleRows | null,
): { x: number; y: number } {
  const w = Math.min(size.w, cols);
  const maxY = layout.reduce((max, item) => Math.max(max, item.y + item.h), 0);

  const scan = (fromY: number, toY: number): { x: number; y: number } | null => {
    for (let y = fromY; y <= toY; y += 1) {
      for (let x = 0; x <= cols - w; x += 1) {
        const candidate = { x, y, w, h: size.h };
        const blocked = layout.some((item) => rectsOverlap(candidate, item));
        if (!blocked) return { x, y };
      }
    }
    return null;
  };

  // Prefer a gap that lands fully inside the currently visible scroll band so
  // the fresh widget appears on-screen; fall back to the top-left-first scan.
  if (visibleRows) {
    const from = Math.max(0, visibleRows.top);
    const to = Math.min(maxY, visibleRows.bottom - size.h + 1);
    const hit = to >= from ? scan(from, to) : null;
    if (hit) return hit;
  }

  return scan(0, maxY) ?? { x: 0, y: maxY };
}

function nextInstanceId(type: string, layout: WorkspaceLayoutItem[]): string {
  const prefix = `${type}-`;
  const nums = layout
    .filter((item) => item.type === type)
    .map((item) => {
      if (item.i === type) return 1;
      if (!item.i.startsWith(prefix)) return 0;
      const n = Number.parseInt(item.i.slice(prefix.length), 10);
      return Number.isFinite(n) ? n : 0;
    });
  const next = nums.length > 0 ? Math.max(...nums) + 1 : 1;
  return `${type}-${next}`;
}

function defaultConfigForType(type: string): WidgetInstanceConfig | undefined {
  if (type === "chart") {
    return {
      channel: "none",
      timeframe: CHART_TIMEFRAMES[5].value,
      style: "candle",
    };
  }
  if (type === "watchlist") {
    return { channel: "none" };
  }
  return undefined;
}

function filterKnownLayout(
  items: WorkspaceLayoutItem[],
): WorkspaceLayoutItem[] {
  return items.filter((item) => getWidgetDefinition(item.type));
}

function resolveLayoutOverlaps(
  layout: WorkspaceLayoutItem[],
): WorkspaceLayoutItem[] {
  if (layout.length <= 1) return layout;

  const sorted = [...layout].sort((a, b) => {
    if (a.y !== b.y) return a.y - b.y;
    if (a.x !== b.x) return a.x - b.x;
    return a.i.localeCompare(b.i);
  });

  const placed: WorkspaceLayoutItem[] = [];
  for (const item of sorted) {
    let candidate = { ...item };
    while (placed.some((p) => rectsOverlap(candidate, p))) {
      const overlapping = placed.filter((p) => rectsOverlap(candidate, p));
      const nextY = Math.max(...overlapping.map((p) => p.y + p.h));
      candidate = { ...candidate, y: nextY };
    }
    placed.push(candidate);
  }

  const byId = new Map(placed.map((p) => [p.i, p]));
  return layout.map((item) => byId.get(item.i) ?? item);
}

function sanitizePagesState(state: WorkspacePagesState): WorkspacePagesState {
  const pages: Record<string, WorkspacePage> = {};
  for (const [key, page] of Object.entries(state.pages)) {
    const known = filterKnownLayout(page.layout);
    const filtered =
      known.length > 0 ? known : page.layout.length === 0 ? [] : known;
    const layout = resolveLayoutOverlaps(filtered);
    pages[key] = { ...page, id: page.id || key, layout };
  }

  const activePageId =
    state.activePageId in pages
      ? state.activePageId
      : Object.keys(pages)[0];

  const activeLayout = pages[activePageId]?.layout ?? [];
  if (activeLayout.length === 0 && Object.keys(pages).length === 1) {
    const canvasId = activePageId;
    pages[canvasId] = {
      ...pages[canvasId],
      layout: DEFAULT_WORKSPACE_LAYOUT,
    };
  }

  return { pages, activePageId };
}

// Page ids must be unique across the whole LIBRARY, not just the active
// workspace — a colliding id would silently alias two pages together.
function nextPageId(library: Record<string, WorkspacePage>): string {
  let n = Object.keys(library).length + 1;
  let id = `page-${n}`;
  while (library[id]) {
    n += 1;
    id = `page-${n}`;
  }
  return id;
}


// ── Debounced server save ──────────────────────────────────────────────────────
let _serverSaveTimer: ReturnType<typeof setTimeout> | null = null;
let _pendingWorkspacesState: WorkspacesState | null = null;
let _hasPendingServerSave = false;
const SERVER_SAVE_DEBOUNCE_MS = 500;

function flushPendingServerSave(): void {
  if (!_pendingWorkspacesState) return;
  const state = _pendingWorkspacesState;
  _pendingWorkspacesState = null;
  void saveWorkspaces(state)
    .catch((err) => {
      console.warn("[workspace] server save error (swallowed):", err);
    })
    .finally(() => {
      if (!_pendingWorkspacesState) _hasPendingServerSave = false;
    });
}

function scheduleServerSave(state: WorkspacesState): void {
  _pendingWorkspacesState = state;
  _hasPendingServerSave = true;
  if (_serverSaveTimer !== null) clearTimeout(_serverSaveTimer);
  _serverSaveTimer = setTimeout(() => {
    _serverSaveTimer = null;
    flushPendingServerSave();
  }, SERVER_SAVE_DEBOUNCE_MS);
}

if (typeof window !== "undefined") {
  const flushOnHide = () => {
    if (document.visibilityState !== "hidden") return;
    if (_serverSaveTimer !== null) {
      clearTimeout(_serverSaveTimer);
      _serverSaveTimer = null;
    }
    flushPendingServerSave();
  };
  document.addEventListener("visibilitychange", flushOnHide);
  window.addEventListener("pagehide", flushOnHide);
}

function fullStateOf(s: {
  pageLibrary: Record<string, WorkspacePage>;
  workspaces: Record<string, WorkspaceSnapshot>;
  defaultWorkspaceId: string;
}): WorkspacesState {
  return {
    version: 2,
    pages: s.pageLibrary,
    workspaces: s.workspaces,
    defaultWorkspaceId: s.defaultWorkspaceId,
  };
}

// Fold the live pages view back into the library + the active workspace's
// pageIds and persist everything (localStorage + debounced server PUT). Also
// keeps the legacy per-pages cache warm so a rolled-back bundle finds a layout.
function persistPages(pagesState: WorkspacePagesState): void {
  const { pageLibrary, workspaces, activeWorkspaceId, defaultWorkspaceId } =
    useWorkspaceStore.getState();
  const nextLibrary = { ...pageLibrary, ...pagesState.pages };
  const active = workspaces[activeWorkspaceId];
  const nextWorkspaces = active
    ? {
        ...workspaces,
        [activeWorkspaceId]: {
          ...active,
          pageIds: Object.keys(pagesState.pages),
          activePageId: pagesState.activePageId,
        },
      }
    : workspaces;
  useWorkspaceStore.setState({
    pageLibrary: nextLibrary,
    workspaces: nextWorkspaces,
  });
  const full = fullStateOf({
    pageLibrary: nextLibrary,
    workspaces: nextWorkspaces,
    defaultWorkspaceId,
  });
  localStorageWorkspacesPersistence.save(full);
  localStoragePagesPersistence.save(pagesState);
  scheduleServerSave(full);
  // TRANSITIONAL: also PUT the legacy pages endpoint while the backend may
  // predate /api/workspace/workspaces — its server-side migration wraps the
  // pages blob, so keeping that blob fresh means no edits are lost across the
  // switchover. Drop once the workspaces endpoint is confirmed live.
  if (activeWorkspaceId === defaultWorkspaceId) {
    void saveWorkspacePages(pagesState);
  }
}

// Persist a workspaces/library-level change that doesn't touch the live view.
function persistWorkspaces(state: WorkspacesState): void {
  localStorageWorkspacesPersistence.save(state);
  scheduleServerSave(state);
}

/** Flush any debounced layout save (e.g. on workspace unmount). */
export function flushWorkspaceLayoutSave(): void {
  if (_serverSaveTimer !== null) {
    clearTimeout(_serverSaveTimer);
    _serverSaveTimer = null;
  }
  flushPendingServerSave();
}
// ── Store ──────────────────────────────────────────────────────────────────────

const INITIAL_WORKSPACES = wrapPagesAsWorkspacesState(DEFAULT_PAGES_STATE);

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  pages: DEFAULT_PAGES_STATE.pages,
  activePageId: DEFAULT_PAGES_STATE.activePageId,
  pageLibrary: INITIAL_WORKSPACES.pages,
  workspaces: INITIAL_WORKSPACES.workspaces,
  activeWorkspaceId: INITIAL_WORKSPACES.defaultWorkspaceId,
  defaultWorkspaceId: INITIAL_WORKSPACES.defaultWorkspaceId,
  hydrated: false,
  locked: false,
  pickerOpen: false,
  gridCols: GRID_DEFAULT_COLS,
  visibleRows: null,
  focusRequest: null,

  getActiveLayout: () => {
    const { pages, activePageId } = get();
    return pages[activePageId]?.layout ?? [];
  },

  setGridCols: (gridCols) => {
    if (get().gridCols !== gridCols) set({ gridCols });
  },

  setVisibleRows: (rows) => {
    const cur = get().visibleRows;
    if (cur?.top === rows?.top && cur?.bottom === rows?.bottom) return;
    set({ visibleRows: rows });
  },

  hydrate: async () => {
    if (get().hydrated && _hasPendingServerSave) return;

    // Resolve the full workspaces set: server (which auto-migrates from the
    // old pages blob) → localStorage cache → legacy pages/v2 caches wrapped as
    // a single "Main" workspace → factory default.
    let workspacesState: WorkspacesState | null = null;
    try {
      workspacesState = await fetchWorkspaces();
    } catch {
      // server unreachable — silently fall back
    }
    if (!workspacesState) {
      workspacesState = localStorageWorkspacesPersistence.load();
    }
    if (!workspacesState) {
      const cachedPages = localStoragePagesPersistence.load();
      if (cachedPages && Object.keys(cachedPages.pages).length > 0) {
        workspacesState = wrapPagesAsWorkspacesState(cachedPages);
      }
    }
    if (!workspacesState) {
      const legacyLayout = loadLegacyV2Layout();
      if (legacyLayout && legacyLayout.length > 0) {
        const known = filterKnownLayout(legacyLayout);
        const layout = known.length > 0 ? known : DEFAULT_WORKSPACE_LAYOUT;
        workspacesState = wrapPagesAsWorkspacesState(
          wrapLayoutAsPagesState(layout),
        );
      }
    }
    if (!workspacesState) {
      workspacesState = wrapPagesAsWorkspacesState(DEFAULT_PAGES_STATE);
    }

    // This DEVICE's workspace choice; fall back to the shared default.
    const storedActive = loadActiveWorkspaceId();
    const activeWorkspaceId =
      storedActive && workspacesState.workspaces[storedActive]
        ? storedActive
        : workspacesState.defaultWorkspaceId;

    const resolved =
      pagesStateForWorkspace(workspacesState, activeWorkspaceId) ??
      DEFAULT_PAGES_STATE;
    const sanitized = sanitizePagesState(resolved);
    const activeWs = workspacesState.workspaces[activeWorkspaceId];
    const pageLibrary = { ...workspacesState.pages, ...sanitized.pages };
    const workspaces = {
      ...workspacesState.workspaces,
      [activeWorkspaceId]: {
        ...activeWs,
        pageIds: Object.keys(sanitized.pages),
        activePageId: sanitized.activePageId,
      },
    };
    localStorageWorkspacesPersistence.save(
      fullStateOf({
        pageLibrary,
        workspaces,
        defaultWorkspaceId: workspacesState.defaultWorkspaceId,
      }),
    );
    localStoragePagesPersistence.save(sanitized);
    set({
      pages: sanitized.pages,
      activePageId: sanitized.activePageId,
      pageLibrary,
      workspaces,
      activeWorkspaceId,
      defaultWorkspaceId: workspacesState.defaultWorkspaceId,
      hydrated: true,
      locked: loadLocked(),
    });
  },

  switchWorkspace: (id) => {
    const { pageLibrary, workspaces, activeWorkspaceId, defaultWorkspaceId } =
      get();
    const target = workspaces[id];
    if (!target || id === activeWorkspaceId) return;
    // Any pending debounced save still carries the OLD active workspace's
    // pages inside the full workspaces state — flush it before swapping.
    flushWorkspaceLayoutSave();
    const resolved = pagesStateForWorkspace(
      fullStateOf({ pageLibrary, workspaces, defaultWorkspaceId }),
      id,
    );
    if (!resolved) return;
    const sanitized = sanitizePagesState(resolved);
    saveActiveWorkspaceId(id);
    set({
      activeWorkspaceId: id,
      pages: sanitized.pages,
      activePageId: sanitized.activePageId,
      pageLibrary: { ...pageLibrary, ...sanitized.pages },
    });
  },

  addWorkspace: (name, duplicateCurrent) => {
    const { pageLibrary, workspaces, defaultWorkspaceId, pages } = get();
    if (Object.keys(workspaces).length >= MAX_WORKSPACES) return null;
    let n = Object.keys(workspaces).length + 1;
    let id = `ws-${n}`;
    while (workspaces[id]) {
      n += 1;
      id = `ws-${n}`;
    }
    const trimmed = name.trim() || `Workspace ${n}`;

    // Both paths mint NEW library pages: duplicating deep-copies the live
    // pages (a variant workspace must not edit the originals — that's what
    // linking is for), a fresh workspace starts with one blank page.
    const srcPages = duplicateCurrent
      ? Object.values(pages)
      : [{ id: "", name: "Page 1", layout: [] as WorkspaceLayoutItem[] }];
    if (Object.keys(pageLibrary).length + srcPages.length > MAX_LIBRARY_PAGES) {
      return null;
    }
    const nextLibrary = { ...pageLibrary };
    const pageIds: string[] = [];
    for (const src of srcPages) {
      const pageId = nextPageId(nextLibrary);
      const copy = JSON.parse(JSON.stringify(src)) as WorkspacePage;
      nextLibrary[pageId] = { ...copy, id: pageId };
      pageIds.push(pageId);
    }
    const nextWorkspaces = {
      ...workspaces,
      [id]: { id, name: trimmed, pageIds, activePageId: pageIds[0] },
    };
    set({ pageLibrary: nextLibrary, workspaces: nextWorkspaces });
    persistWorkspaces(
      fullStateOf({
        pageLibrary: nextLibrary,
        workspaces: nextWorkspaces,
        defaultWorkspaceId,
      }),
    );
    get().switchWorkspace(id);
    return id;
  },

  renameWorkspace: (id, name) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const { pageLibrary, workspaces, defaultWorkspaceId } = get();
    const ws = workspaces[id];
    if (!ws) return;
    const nextWorkspaces = { ...workspaces, [id]: { ...ws, name: trimmed } };
    set({ workspaces: nextWorkspaces });
    persistWorkspaces(
      fullStateOf({
        pageLibrary,
        workspaces: nextWorkspaces,
        defaultWorkspaceId,
      }),
    );
  },

  deleteWorkspace: (id) => {
    const { pageLibrary, workspaces, activeWorkspaceId, defaultWorkspaceId } =
      get();
    if (!workspaces[id] || Object.keys(workspaces).length <= 1) return false;
    // Its pages stay in the library (they may be shared; orphans are still
    // loadable from the manager).
    const nextWorkspaces = { ...workspaces };
    delete nextWorkspaces[id];
    const nextDefault =
      defaultWorkspaceId === id
        ? Object.keys(nextWorkspaces)[0]
        : defaultWorkspaceId;
    set({ workspaces: nextWorkspaces, defaultWorkspaceId: nextDefault });
    if (activeWorkspaceId === id) {
      const resolved = pagesStateForWorkspace(
        fullStateOf({
          pageLibrary,
          workspaces: nextWorkspaces,
          defaultWorkspaceId: nextDefault,
        }),
        nextDefault,
      );
      const sanitized = sanitizePagesState(resolved ?? DEFAULT_PAGES_STATE);
      saveActiveWorkspaceId(nextDefault);
      set({
        activeWorkspaceId: nextDefault,
        pages: sanitized.pages,
        activePageId: sanitized.activePageId,
        pageLibrary: { ...pageLibrary, ...sanitized.pages },
      });
    }
    persistWorkspaces(
      fullStateOf({
        pageLibrary: get().pageLibrary,
        workspaces: nextWorkspaces,
        defaultWorkspaceId: nextDefault,
      }),
    );
    return true;
  },

  setDefaultWorkspace: (id) => {
    const { pageLibrary, workspaces, defaultWorkspaceId } = get();
    if (!workspaces[id] || defaultWorkspaceId === id) return;
    set({ defaultWorkspaceId: id });
    persistWorkspaces(
      fullStateOf({ pageLibrary, workspaces, defaultWorkspaceId: id }),
    );
  },

  updateLayout: (layout, pageId) => {
    const { pages, activePageId } = get();
    const targetId = pageId ?? activePageId;
    const page = pages[targetId];
    if (!page) return;
    const resolved = resolveLayoutOverlaps(layout);
    const nextPages = {
      ...pages,
      [targetId]: { ...page, layout: resolved },
    };
    set({ pages: nextPages });
    persistPages({ pages: nextPages, activePageId });
  },

  addWidget: (type) => {
    const def = getWidgetDefinition(type);
    if (!def) return false;

    const { pages, activePageId, gridCols, visibleRows } = get();
    const page = pages[activePageId];
    if (!page) return false;
    const layout = page.layout;

    if (!def.multiInstance) {
      if (layout.some((item) => item.type === type)) return false;
    }

    const instanceId = def.multiInstance ? nextInstanceId(type, layout) : type;
    const { x, y } = findPlacement(layout, def.defaultSize, gridCols, visibleRows);
    const config = defaultConfigForType(type);
    const w = Math.min(def.defaultSize.w, gridCols);

    const next: WorkspaceLayoutItem = {
      i: instanceId,
      type,
      x,
      y,
      w,
      h: def.defaultSize.h,
      minW: def.defaultSize.minW,
      minH: def.defaultSize.minH,
      ...(config ? { config } : {}),
    };

    const merged = [...layout, next];
    const nextPages = {
      ...pages,
      [activePageId]: { ...page, layout: merged },
    };
    // Reuse the focus channel to scroll the fresh widget into view + flash it.
    set({
      pages: nextPages,
      pickerOpen: false,
      focusRequest: { instanceId, nonce: Date.now() },
    });
    persistPages({ pages: nextPages, activePageId });
    return true;
  },

  duplicateWidget: (instanceId, at, pageId) => {
    const { pages, activePageId } = get();
    const targetId = pageId ?? activePageId;
    const page = pages[targetId];
    if (!page) return false;
    const source = page.layout.find((item) => item.i === instanceId);
    if (!source) return false;
    if (!getWidgetDefinition(source.type)?.multiInstance) return false;

    const copy: WorkspaceLayoutItem = {
      ...source,
      i: nextInstanceId(source.type, page.layout),
      x: Math.max(0, at.x),
      y: Math.max(0, at.y),
      ...(source.config ? { config: { ...source.config } } : {}),
    };
    const merged = resolveLayoutOverlaps([...page.layout, copy]);
    const nextPages = {
      ...pages,
      [targetId]: { ...page, layout: merged },
    };
    set({ pages: nextPages });
    persistPages({ pages: nextPages, activePageId });
    return true;
  },

  updateWidgetConfig: (instanceId, patch, pageId) => {
    const { pages, activePageId } = get();
    const targetId = pageId ?? activePageId;
    const page = pages[targetId];
    if (!page) return;
    const nextLayout = page.layout.map((item) =>
      item.i === instanceId
        ? { ...item, config: { ...item.config, ...patch } }
        : item,
    );
    const nextPages = {
      ...pages,
      [targetId]: { ...page, layout: nextLayout },
    };
    set({ pages: nextPages });
    persistPages({ pages: nextPages, activePageId });
  },

  removeWidget: (id, pageId) => {
    const { pages, activePageId } = get();
    const targetId = pageId ?? activePageId;
    const page = pages[targetId];
    if (!page) return;
    const nextLayout = page.layout.filter((item) => item.i !== id);
    if (nextLayout.length === page.layout.length) return;
    const nextPages = {
      ...pages,
      [targetId]: { ...page, layout: nextLayout },
    };
    set({ pages: nextPages });
    persistPages({ pages: nextPages, activePageId });
  },

  addPage: (name) => {
    const { pages, pageLibrary } = get();
    if (Object.keys(pages).length >= MAX_PAGES_PER_WORKSPACE) return null;
    if (Object.keys(pageLibrary).length >= MAX_LIBRARY_PAGES) return null;

    const id = nextPageId(pageLibrary);
    const pageNumber = Object.keys(pages).length + 1;
    const pageName = name?.trim() || `Page ${pageNumber}`;
    const newPage: WorkspacePage = { id, name: pageName, layout: [] };
    const nextPages = { ...pages, [id]: newPage };
    set({ pages: nextPages, activePageId: id });
    persistPages({ pages: nextPages, activePageId: id });
    return id;
  },

  addPageFromLibrary: (id) => {
    const { pages, pageLibrary } = get();
    if (Object.keys(pages).length >= MAX_PAGES_PER_WORKSPACE) return false;
    const page = pageLibrary[id];
    if (!page || pages[id]) return false;
    const nextPages = { ...pages, [id]: page };
    set({ pages: nextPages, activePageId: id });
    persistPages({ pages: nextPages, activePageId: id });
    return true;
  },

  renamePage: (id, name) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const { pages, activePageId, pageLibrary, workspaces, defaultWorkspaceId } =
      get();
    const page = pageLibrary[id] ?? pages[id];
    if (!page) return;
    const renamed = { ...page, name: trimmed };
    if (pages[id]) {
      const nextPages = { ...pages, [id]: renamed };
      set({ pages: nextPages });
      persistPages({ pages: nextPages, activePageId });
      return;
    }
    // Library-only page (not in the active workspace) — rename in place.
    const nextLibrary = { ...pageLibrary, [id]: renamed };
    set({ pageLibrary: nextLibrary });
    persistWorkspaces(
      fullStateOf({ pageLibrary: nextLibrary, workspaces, defaultWorkspaceId }),
    );
  },

  removePageFromWorkspace: (id) => {
    const { pages, activePageId } = get();
    if (Object.keys(pages).length <= 1) return false;
    if (!pages[id]) return false;

    const nextPages = { ...pages };
    delete nextPages[id];

    let nextActivePageId = activePageId;
    if (activePageId === id) {
      nextActivePageId = Object.keys(nextPages)[0];
    }

    // persistPages rebuilds the workspace's pageIds from the live keys; the
    // page itself stays in the library.
    set({ pages: nextPages, activePageId: nextActivePageId });
    persistPages({ pages: nextPages, activePageId: nextActivePageId });
    return true;
  },

  deletePageEverywhere: (id) => {
    const { pages, activePageId, pageLibrary, workspaces, defaultWorkspaceId } =
      get();
    if (!pageLibrary[id]) return false;
    // A workspace must keep ≥1 page — refuse rather than invent a blank one.
    for (const ws of Object.values(workspaces)) {
      if (ws.pageIds.length === 1 && ws.pageIds[0] === id) return false;
    }
    const nextLibrary = { ...pageLibrary };
    delete nextLibrary[id];
    const nextWorkspaces: Record<string, WorkspaceSnapshot> = {};
    for (const [wsId, ws] of Object.entries(workspaces)) {
      const pageIds = ws.pageIds.filter((p) => p !== id);
      nextWorkspaces[wsId] = {
        ...ws,
        pageIds,
        activePageId: pageIds.includes(ws.activePageId)
          ? ws.activePageId
          : pageIds[0],
      };
    }
    let nextPages = pages;
    let nextActivePageId = activePageId;
    if (pages[id]) {
      nextPages = { ...pages };
      delete nextPages[id];
      if (activePageId === id) nextActivePageId = Object.keys(nextPages)[0];
    }
    set({
      pages: nextPages,
      activePageId: nextActivePageId,
      pageLibrary: nextLibrary,
      workspaces: nextWorkspaces,
    });
    persistWorkspaces(
      fullStateOf({
        pageLibrary: nextLibrary,
        workspaces: nextWorkspaces,
        defaultWorkspaceId,
      }),
    );
    return true;
  },

  duplicatePage: (id) => {
    const { pages, pageLibrary } = get();
    if (Object.keys(pages).length >= MAX_PAGES_PER_WORKSPACE) return null;
    if (Object.keys(pageLibrary).length >= MAX_LIBRARY_PAGES) return null;
    const src = pages[id] ?? pageLibrary[id];
    if (!src) return null;
    const newId = nextPageId(pageLibrary);
    const copy = JSON.parse(JSON.stringify(src)) as WorkspacePage;
    const newPage: WorkspacePage = {
      ...copy,
      id: newId,
      name: `${src.name} 2`,
    };
    const nextPages = { ...pages, [newId]: newPage };
    set({ pages: nextPages, activePageId: newId });
    persistPages({ pages: nextPages, activePageId: newId });
    return newId;
  },

  forkPage: (id) => {
    const { pages, activePageId, pageLibrary } = get();
    const src = pages[id];
    if (!src) return null;
    if (Object.keys(pageLibrary).length >= MAX_LIBRARY_PAGES) return null;
    const newId = nextPageId(pageLibrary);
    const copy = JSON.parse(JSON.stringify(src)) as WorkspacePage;
    const newPage: WorkspacePage = { ...copy, id: newId };
    // Swap the reference IN PLACE (same pill position); other workspaces keep
    // the original page.
    const nextPages: Record<string, WorkspacePage> = {};
    for (const key of Object.keys(pages)) {
      if (key === id) nextPages[newId] = newPage;
      else nextPages[key] = pages[key];
    }
    const nextActivePageId = activePageId === id ? newId : activePageId;
    set({ pages: nextPages, activePageId: nextActivePageId });
    persistPages({ pages: nextPages, activePageId: nextActivePageId });
    return newId;
  },

  movePage: (id, dir) => {
    const { pages, activePageId } = get();
    const keys = Object.keys(pages);
    const idx = keys.indexOf(id);
    const target = idx + dir;
    if (idx === -1 || target < 0 || target >= keys.length) return;
    [keys[idx], keys[target]] = [keys[target], keys[idx]];
    const nextPages: Record<string, WorkspacePage> = {};
    for (const key of keys) nextPages[key] = pages[key];
    set({ pages: nextPages });
    persistPages({ pages: nextPages, activePageId });
  },

  setActivePage: (id) => {
    // Lab sentinel: an ephemeral, non-persisted active id. Skip the real-page
    // guard and the persist — pages[LAB_PAGE_ID] intentionally doesn't exist.
    if (id === LAB_PAGE_ID) {
      if (get().activePageId === LAB_PAGE_ID) return;
      set({ activePageId: LAB_PAGE_ID });
      return;
    }
    const { pages, activePageId } = get();
    if (!pages[id] || activePageId === id) return;
    set({ activePageId: id });
    persistPages({ pages, activePageId: id });
  },

  toggleLock: () => {
    const locked = !get().locked;
    set({ locked });
    saveLocked(locked);
  },
  setPickerOpen: (pickerOpen) => set({ pickerOpen }),
  openPicker: () => set({ pickerOpen: true }),

  focusWidget: (type) => {
    const { pages, activePageId } = get();
    let found = pages[activePageId]?.layout.find((it) => it.type === type);
    let pageId = activePageId;
    if (!found) {
      for (const [pid, page] of Object.entries(pages)) {
        const hit = page.layout.find((it) => it.type === type);
        if (hit) {
          found = hit;
          pageId = pid;
          break;
        }
      }
    }
    if (!found) return false;
    if (pageId !== activePageId) get().setActivePage(pageId);
    set({ focusRequest: { instanceId: found.i, nonce: Date.now() } });
    return true;
  },

  focusSymbol: (symbol) => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return false;
    const { pages, activePageId } = get();
    let chart = pages[activePageId]?.layout.find((it) => it.type === "chart");
    let pageId = activePageId;
    if (!chart) {
      for (const [pid, page] of Object.entries(pages)) {
        const hit = page.layout.find((it) => it.type === "chart");
        if (hit) {
          chart = hit;
          pageId = pid;
          break;
        }
      }
    }
    if (!chart) return false;
    if (pageId !== activePageId) get().setActivePage(pageId);
    const channel = chart.config?.channel ?? "none";
    if (channel !== "none") {
      useChannelStore.getState().setChannelSymbol(channel, sym);
    } else {
      get().updateWidgetConfig(chart.i, { symbol: sym });
    }
    set({ focusRequest: { instanceId: chart.i, nonce: Date.now() } });
    return true;
  },

  // Consume the one-shot focus request once the grid has scrolled + flashed, so
  // returning to the page later doesn't re-fire the stale request.
  clearFocusRequest: () => set({ focusRequest: null }),
}));
