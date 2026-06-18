"use client";

import { create } from "zustand";

import { CHART_TIMEFRAMES } from "@/lib/chart-time";

import {
  DEFAULT_WORKSPACE_LAYOUT,
  localStorageLayoutPersistence,
  type WidgetInstanceConfig,
  type WorkspaceLayoutItem,
} from "./layout-persistence";
import {
  getWidgetDefinition,
  type WidgetDefaultSize,
} from "./widget-registry";

type EnsureChatResult = {
  instanceId: string;
  added: boolean;
};

const LOCKED_STORAGE_KEY = "hmt-workspace-locked";

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

type WorkspaceState = {
  layout: WorkspaceLayoutItem[];
  hydrated: boolean;
  locked: boolean;
  pickerOpen: boolean;
  highlightInstanceId: string | null;
  hydrate: () => void;
  updateLayout: (layout: WorkspaceLayoutItem[]) => void;
  addWidget: (type: string) => boolean;
  ensureChatWidget: () => EnsureChatResult | null;
  updateWidgetConfig: (
    instanceId: string,
    patch: Partial<WidgetInstanceConfig>,
  ) => void;
  removeWidget: (id: string) => void;
  toggleLock: () => void;
  setPickerOpen: (open: boolean) => void;
  openPicker: () => void;
  setHighlight: (instanceId: string | null) => void;
};

const GRID_COLS = 24;

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
): { x: number; y: number } {
  const maxY = layout.reduce((max, item) => Math.max(max, item.y + item.h), 0);

  for (let y = 0; y <= maxY; y += 1) {
    for (let x = 0; x <= GRID_COLS - size.w; x += 1) {
      const candidate = { x, y, w: size.w, h: size.h };
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

let highlightTimer: ReturnType<typeof setTimeout> | null = null;

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  layout: DEFAULT_WORKSPACE_LAYOUT,
  hydrated: false,
  locked: false,
  pickerOpen: false,
  highlightInstanceId: null,
  hydrate: () => {
    const loaded = localStorageLayoutPersistence.load();
    set({
      layout: loaded ?? DEFAULT_WORKSPACE_LAYOUT,
      hydrated: true,
      locked: loadLocked(),
    });
  },
  updateLayout: (layout) => {
    set({ layout });
    localStorageLayoutPersistence.save(layout);
  },
  addWidget: (type) => {
    const def = getWidgetDefinition(type);
    if (!def) return false;

    const { layout } = get();

    if (!def.multiInstance) {
      if (layout.some((item) => item.type === type)) return false;
    }

    const instanceId = def.multiInstance ? nextInstanceId(type, layout) : type;
    const { x, y } = findPlacement(layout, def.defaultSize);
    const config = defaultConfigForType(type);

    const next: WorkspaceLayoutItem = {
      i: instanceId,
      type,
      x,
      y,
      w: def.defaultSize.w,
      h: def.defaultSize.h,
      minW: def.defaultSize.minW,
      minH: def.defaultSize.minH,
      ...(config ? { config } : {}),
    };

    const merged = [...layout, next];
    set({ layout: merged, pickerOpen: false });
    localStorageLayoutPersistence.save(merged);
    return true;
  },
  ensureChatWidget: () => {
    const def = getWidgetDefinition("chat");
    if (!def) return null;

    const { layout } = get();
    const existing = layout.find((item) => item.type === "chat");
    if (existing) {
      return { instanceId: existing.i, added: false };
    }

    const instanceId = "chat";
    const { w, h, minW, minH } = def.defaultSize;
    const shifted = layout.map((item) => ({ ...item, y: item.y + h }));
    const next: WorkspaceLayoutItem = {
      i: instanceId,
      type: "chat",
      x: 0,
      y: 0,
      w,
      h,
      minW,
      minH,
    };

    const merged = [next, ...shifted];
    set({ layout: merged });
    localStorageLayoutPersistence.save(merged);
    get().setHighlight(instanceId);
    return { instanceId, added: true };
  },
  setHighlight: (instanceId) => {
    if (highlightTimer) {
      clearTimeout(highlightTimer);
      highlightTimer = null;
    }
    set({ highlightInstanceId: instanceId });
    if (instanceId) {
      highlightTimer = setTimeout(() => {
        if (get().highlightInstanceId === instanceId) {
          set({ highlightInstanceId: null });
        }
        highlightTimer = null;
      }, 2500);
    }
  },
  updateWidgetConfig: (instanceId, patch) => {
    const { layout } = get();
    const next = layout.map((item) =>
      item.i === instanceId
        ? { ...item, config: { ...item.config, ...patch } }
        : item,
    );
    set({ layout: next });
    localStorageLayoutPersistence.save(next);
  },
  removeWidget: (id) => {
    const { layout } = get();
    const next = layout.filter((item) => item.i !== id);
    if (next.length === layout.length) return;
    set({ layout: next });
    localStorageLayoutPersistence.save(next);
  },
  toggleLock: () => {
    const locked = !get().locked;
    set({ locked });
    saveLocked(locked);
  },
  setPickerOpen: (pickerOpen) => set({ pickerOpen }),
  openPicker: () => set({ pickerOpen: true }),
}));
