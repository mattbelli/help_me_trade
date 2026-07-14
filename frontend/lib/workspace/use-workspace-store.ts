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
  MAX_WORKSPACES,
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
const MAX_PAGES = 5;

/** Inclusive grid-row range currently visible in the scroll viewport. */
type VisibleRows = { top: number; bottom: number };

type WorkspaceState = {
  pages: Record<string, WorkspacePage>;
  activePageId: string;
  // Named groups of pages. `pages`/`activePageId` above are the LIVE editing
  // state of the active workspace; every persist writes them back into
  // `workspaces[activeWorkspaceId].pages_state`.
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
  updateWidgetConfig: (
    instanceId: string,
    patch: Partial<WidgetInstanceConfig>,
    pageId?: string,
  ) => void;
  removeWidget: (id: string, pageId?: string) => void;
  addPage: (name?: string) => string | null;
  renamePage: (id: string, name: string) => void;
  deletePage: (id: string) => boolean;
  duplicatePage: (id: string) => string | null;
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

function nextPageId(pages: Record<string, WorkspacePage>): string {
  let n = Object.keys(pages).length + 1;
  let id = `page-${n}`;
  while (pages[id]) {
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

// Fold the live pages state back into the active workspace and persist the
// whole workspaces set (localStorage + debounced server PUT). Also keeps the
// legacy per-pages cache warm so a rolled-back bundle still finds a layout.
function persistPages(pagesState: WorkspacePagesState): void {
  const { workspaces, activeWorkspaceId, defaultWorkspaceId } =
    useWorkspaceStore.getState();
  const active = workspaces[activeWorkspaceId];
  const nextWorkspaces = active
    ? {
        ...workspaces,
        [activeWorkspaceId]: { ...active, pages_state: pagesState },
      }
    : workspaces;
  useWorkspaceStore.setState({ workspaces: nextWorkspaces });
  const full: WorkspacesState = {
    workspaces: nextWorkspaces,
    defaultWorkspaceId,
  };
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

// Persist a workspaces-level change (add/rename/delete/set-default) that
// doesn't touch the live pages state.
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

    const activeWs = workspacesState.workspaces[activeWorkspaceId];
    const sanitized = sanitizePagesState(activeWs.pages_state);
    const workspaces = {
      ...workspacesState.workspaces,
      [activeWorkspaceId]: { ...activeWs, pages_state: sanitized },
    };
    localStorageWorkspacesPersistence.save({
      workspaces,
      defaultWorkspaceId: workspacesState.defaultWorkspaceId,
    });
    localStoragePagesPersistence.save(sanitized);
    set({
      pages: sanitized.pages,
      activePageId: sanitized.activePageId,
      workspaces,
      activeWorkspaceId,
      defaultWorkspaceId: workspacesState.defaultWorkspaceId,
      hydrated: true,
      locked: loadLocked(),
    });
  },

  switchWorkspace: (id) => {
    const { workspaces, activeWorkspaceId } = get();
    const target = workspaces[id];
    if (!target || id === activeWorkspaceId) return;
    // Any pending debounced save still carries the OLD active workspace's
    // pages inside the full workspaces state — flush it before swapping.
    flushWorkspaceLayoutSave();
    const sanitized = sanitizePagesState(target.pages_state);
    saveActiveWorkspaceId(id);
    set({
      activeWorkspaceId: id,
      pages: sanitized.pages,
      activePageId: sanitized.activePageId,
    });
  },

  addWorkspace: (name, duplicateCurrent) => {
    const { workspaces, defaultWorkspaceId, pages, activePageId } = get();
    if (Object.keys(workspaces).length >= MAX_WORKSPACES) return null;
    let n = Object.keys(workspaces).length + 1;
    let id = `ws-${n}`;
    while (workspaces[id]) {
      n += 1;
      id = `ws-${n}`;
    }
    const trimmed = name.trim() || `Workspace ${n}`;
    const pagesState: WorkspacePagesState = duplicateCurrent
      ? (JSON.parse(
          JSON.stringify({ pages, activePageId }),
        ) as WorkspacePagesState)
      : {
          pages: { "page-1": { id: "page-1", name: "Page 1", layout: [] } },
          activePageId: "page-1",
        };
    const nextWorkspaces = {
      ...workspaces,
      [id]: { id, name: trimmed, pages_state: pagesState },
    };
    set({ workspaces: nextWorkspaces });
    persistWorkspaces({ workspaces: nextWorkspaces, defaultWorkspaceId });
    get().switchWorkspace(id);
    return id;
  },

  renameWorkspace: (id, name) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const { workspaces, defaultWorkspaceId } = get();
    const ws = workspaces[id];
    if (!ws) return;
    const nextWorkspaces = { ...workspaces, [id]: { ...ws, name: trimmed } };
    set({ workspaces: nextWorkspaces });
    persistWorkspaces({ workspaces: nextWorkspaces, defaultWorkspaceId });
  },

  deleteWorkspace: (id) => {
    const { workspaces, activeWorkspaceId, defaultWorkspaceId } = get();
    if (!workspaces[id] || Object.keys(workspaces).length <= 1) return false;
    const nextWorkspaces = { ...workspaces };
    delete nextWorkspaces[id];
    const nextDefault =
      defaultWorkspaceId === id
        ? Object.keys(nextWorkspaces)[0]
        : defaultWorkspaceId;
    set({ workspaces: nextWorkspaces, defaultWorkspaceId: nextDefault });
    if (activeWorkspaceId === id) {
      const fallback = nextWorkspaces[nextDefault];
      const sanitized = sanitizePagesState(fallback.pages_state);
      saveActiveWorkspaceId(nextDefault);
      set({
        activeWorkspaceId: nextDefault,
        pages: sanitized.pages,
        activePageId: sanitized.activePageId,
      });
    }
    persistWorkspaces({
      workspaces: nextWorkspaces,
      defaultWorkspaceId: nextDefault,
    });
    return true;
  },

  setDefaultWorkspace: (id) => {
    const { workspaces, defaultWorkspaceId } = get();
    if (!workspaces[id] || defaultWorkspaceId === id) return;
    set({ defaultWorkspaceId: id });
    persistWorkspaces({ workspaces, defaultWorkspaceId: id });
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
    const { pages } = get();
    if (Object.keys(pages).length >= MAX_PAGES) return null;

    const id = nextPageId(pages);
    const pageNumber = Object.keys(pages).length + 1;
    const pageName = name?.trim() || `Page ${pageNumber}`;
    const newPage: WorkspacePage = { id, name: pageName, layout: [] };
    const nextPages = { ...pages, [id]: newPage };
    set({ pages: nextPages, activePageId: id });
    persistPages({ pages: nextPages, activePageId: id });
    return id;
  },

  renamePage: (id, name) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const { pages, activePageId } = get();
    const page = pages[id];
    if (!page) return;
    const nextPages = {
      ...pages,
      [id]: { ...page, name: trimmed },
    };
    set({ pages: nextPages });
    persistPages({ pages: nextPages, activePageId });
  },

  deletePage: (id) => {
    const { pages, activePageId } = get();
    if (Object.keys(pages).length <= 1) return false;
    if (!pages[id]) return false;

    const nextPages = { ...pages };
    delete nextPages[id];

    let nextActivePageId = activePageId;
    if (activePageId === id) {
      nextActivePageId = Object.keys(nextPages)[0];
    }

    set({ pages: nextPages, activePageId: nextActivePageId });
    persistPages({ pages: nextPages, activePageId: nextActivePageId });
    return true;
  },

  duplicatePage: (id) => {
    const { pages } = get();
    if (Object.keys(pages).length >= MAX_PAGES) return null;
    const src = pages[id];
    if (!src) return null;
    const newId = nextPageId(pages);
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

  setActivePage: (id) => {
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
