"use client";

import { create } from "zustand";

import { CHART_TIMEFRAMES } from "@/lib/chart-time";
import { fetchWorkspaceLayout, saveWorkspaceLayout } from "@/lib/api";

import {
  DEFAULT_WORKSPACE_LAYOUT,
  localStorageLayoutPersistence, // role: fast local cache + offline fallback
  type WidgetInstanceConfig,
  type WorkspaceLayoutItem,
} from "./layout-persistence";
import { GRID_DEFAULT_COLS } from "./grid-metrics";
import {
  getWidgetDefinition,
  type WidgetDefaultSize,
} from "./widget-registry";

const LOCKED_STORAGE_KEY = "hmt-workspace-locked";

type WorkspaceState = {
  layout: WorkspaceLayoutItem[];
  hydrated: boolean;
  locked: boolean;
  pickerOpen: boolean;
  gridCols: number;
  hydrate: () => Promise<void>;
  setGridCols: (cols: number) => void;
  updateLayout: (layout: WorkspaceLayoutItem[]) => void;
  addWidget: (type: string) => boolean;
  updateWidgetConfig: (
    instanceId: string,
    patch: Partial<WidgetInstanceConfig>,
  ) => void;
  removeWidget: (id: string) => void;
  toggleLock: () => void;
  setPickerOpen: (open: boolean) => void;
  openPicker: () => void;
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
): { x: number; y: number } {
  const w = Math.min(size.w, cols);
  const maxY = layout.reduce((max, item) => Math.max(max, item.y + item.h), 0);

  for (let y = 0; y <= maxY; y += 1) {
    for (let x = 0; x <= cols - w; x += 1) {
      const candidate = { x, y, w, h: size.h };
      const blocked = layout.some((item) => rectsOverlap(candidate, item));
      if (!blocked) return { x, y };
    }
  }

  return { x: 0, y: maxY };
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
      timeframe: CHART_TIMEFRAMES[4].value,
      style: "candle",
    };
  }
  if (type === "watchlist") {
    return { channel: "none" };
  }
  return undefined;
}

// ── Debounced server save ──────────────────────────────────────────────────────
// Single module-level timer; last-write-wins is correct for a single user.
let _serverSaveTimer: ReturnType<typeof setTimeout> | null = null;
const SERVER_SAVE_DEBOUNCE_MS = 500;

function scheduleServerSave(layout: WorkspaceLayoutItem[]): void {
  if (_serverSaveTimer !== null) clearTimeout(_serverSaveTimer);
  _serverSaveTimer = setTimeout(() => {
    _serverSaveTimer = null;
    saveWorkspaceLayout(layout).catch((err) => {
      console.warn("[workspace] server save error (swallowed):", err);
    });
  }, SERVER_SAVE_DEBOUNCE_MS);
}

// Persist to both local cache (instant) and server (debounced).
function persistLayout(layout: WorkspaceLayoutItem[]): void {
  localStorageLayoutPersistence.save(layout); // immediate cache
  scheduleServerSave(layout); // debounced server write
}

// ── Store ──────────────────────────────────────────────────────────────────────

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  layout: DEFAULT_WORKSPACE_LAYOUT,
  hydrated: false,
  locked: false,
  pickerOpen: false,
  gridCols: GRID_DEFAULT_COLS,
  setGridCols: (gridCols) => {
    if (get().gridCols !== gridCols) set({ gridCols });
  },

  hydrate: async () => {
    // Fail-soft: if the server call throws we fall back to localStorage, then
    // DEFAULT_WORKSPACE_LAYOUT. hydrated=true is set in all branches so the
    // UI never hangs.
    let serverLayout: WorkspaceLayoutItem[] | null = null;
    try {
      serverLayout = await fetchWorkspaceLayout();
    } catch {
      // server unreachable — silently fall back
    }

    // Drop unknown widget types (same logic as before) and verify it's non-empty.
    function filterKnown(
      items: WorkspaceLayoutItem[],
    ): WorkspaceLayoutItem[] {
      return items.filter((item) => getWidgetDefinition(item.type));
    }

    if (serverLayout && serverLayout.length > 0) {
      const known = filterKnown(serverLayout);
      const layout = known.length > 0 ? known : DEFAULT_WORKSPACE_LAYOUT;
      // Sync local cache to match server (so offline opens see latest state).
      localStorageLayoutPersistence.save(layout);
      set({ layout, hydrated: true, locked: loadLocked() });
      return;
    }

    // No server layout — try localStorage cache.
    const cached = localStorageLayoutPersistence.load();
    if (cached && cached.length > 0) {
      const known = filterKnown(cached);
      const layout = known.length > 0 ? known : DEFAULT_WORKSPACE_LAYOUT;
      if (known.length !== cached.length) {
        localStorageLayoutPersistence.save(layout);
      }
      // Seed the (empty) server from this device's existing local layout so it
      // propagates to other devices/tabs on their next open.
      scheduleServerSave(layout);
      set({ layout, hydrated: true, locked: loadLocked() });
      return;
    }

    // Nothing stored anywhere — use the built-in default.
    set({
      layout: DEFAULT_WORKSPACE_LAYOUT,
      hydrated: true,
      locked: loadLocked(),
    });
  },

  updateLayout: (layout) => {
    set({ layout });
    persistLayout(layout);
  },

  addWidget: (type) => {
    const def = getWidgetDefinition(type);
    if (!def) return false;

    const { layout, gridCols } = get();

    if (!def.multiInstance) {
      if (layout.some((item) => item.type === type)) return false;
    }

    const instanceId = def.multiInstance ? nextInstanceId(type, layout) : type;
    const { x, y } = findPlacement(layout, def.defaultSize, gridCols);
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
    set({ layout: merged, pickerOpen: false });
    persistLayout(merged);
    return true;
  },

  updateWidgetConfig: (instanceId, patch) => {
    const { layout } = get();
    const next = layout.map((item) =>
      item.i === instanceId
        ? { ...item, config: { ...item.config, ...patch } }
        : item,
    );
    set({ layout: next });
    persistLayout(next);
  },

  removeWidget: (id) => {
    const { layout } = get();
    const next = layout.filter((item) => item.i !== id);
    if (next.length === layout.length) return;
    set({ layout: next });
    persistLayout(next);
  },

  toggleLock: () => {
    const locked = !get().locked;
    set({ locked });
    saveLocked(locked);
  },
  setPickerOpen: (pickerOpen) => set({ pickerOpen }),
  openPicker: () => set({ pickerOpen: true }),
}));
