"use client";

import { create } from "zustand";

import { CHART_TIMEFRAMES } from "@/lib/chart-time";
import { fetchWorkspacePages, saveWorkspacePages } from "@/lib/api";

import {
  DEFAULT_PAGES_STATE,
  DEFAULT_WORKSPACE_LAYOUT,
  loadLegacyV2Layout,
  localStoragePagesPersistence,
  wrapLayoutAsPagesState,
  type WidgetInstanceConfig,
  type WorkspaceLayoutItem,
  type WorkspacePage,
  type WorkspacePagesState,
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
  setActivePage: (id: string) => void;
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
let _pendingPagesState: WorkspacePagesState | null = null;
let _hasPendingServerSave = false;
const SERVER_SAVE_DEBOUNCE_MS = 500;

function flushPendingServerSave(): void {
  if (!_pendingPagesState) return;
  const state = _pendingPagesState;
  _pendingPagesState = null;
  void saveWorkspacePages(state)
    .catch((err) => {
      console.warn("[workspace] server save error (swallowed):", err);
    })
    .finally(() => {
      if (!_pendingPagesState) _hasPendingServerSave = false;
    });
}

function scheduleServerSave(pagesState: WorkspacePagesState): void {
  _pendingPagesState = pagesState;
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

function persistPages(pagesState: WorkspacePagesState): void {
  localStoragePagesPersistence.save(pagesState);
  scheduleServerSave(pagesState);
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

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  pages: DEFAULT_PAGES_STATE.pages,
  activePageId: DEFAULT_PAGES_STATE.activePageId,
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

    let serverPages: WorkspacePagesState | null = null;
    try {
      serverPages = await fetchWorkspacePages();
    } catch {
      // server unreachable — silently fall back
    }

    if (serverPages && Object.keys(serverPages.pages).length > 0) {
      const sanitized = sanitizePagesState(serverPages);
      localStoragePagesPersistence.save(sanitized);
      set({
        pages: sanitized.pages,
        activePageId: sanitized.activePageId,
        hydrated: true,
        locked: loadLocked(),
      });
      return;
    }

    const cached = localStoragePagesPersistence.load();
    if (cached && Object.keys(cached.pages).length > 0) {
      const sanitized = sanitizePagesState(cached);
      localStoragePagesPersistence.save(sanitized);
      scheduleServerSave(sanitized);
      set({
        pages: sanitized.pages,
        activePageId: sanitized.activePageId,
        hydrated: true,
        locked: loadLocked(),
      });
      return;
    }

    const legacyLayout = loadLegacyV2Layout();
    if (legacyLayout && legacyLayout.length > 0) {
      const known = filterKnownLayout(legacyLayout);
      const layout = known.length > 0 ? known : DEFAULT_WORKSPACE_LAYOUT;
      const wrapped = wrapLayoutAsPagesState(layout);
      localStoragePagesPersistence.save(wrapped);
      scheduleServerSave(wrapped);
      set({
        pages: wrapped.pages,
        activePageId: wrapped.activePageId,
        hydrated: true,
        locked: loadLocked(),
      });
      return;
    }

    set({
      pages: DEFAULT_PAGES_STATE.pages,
      activePageId: DEFAULT_PAGES_STATE.activePageId,
      hydrated: true,
      locked: loadLocked(),
    });
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
