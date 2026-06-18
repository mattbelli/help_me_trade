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

type WorkspaceState = {
  layout: WorkspaceLayoutItem[];
  hydrated: boolean;
  pickerOpen: boolean;
  hydrate: () => void;
  updateLayout: (layout: WorkspaceLayoutItem[]) => void;
  addWidget: (type: string) => boolean;
  updateWidgetConfig: (
    instanceId: string,
    patch: Partial<WidgetInstanceConfig>,
  ) => void;
  removeWidget: (id: string) => void;
  setPickerOpen: (open: boolean) => void;
  openPicker: () => void;
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

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  layout: DEFAULT_WORKSPACE_LAYOUT,
  hydrated: false,
  pickerOpen: false,
  hydrate: () => {
    const loaded = localStorageLayoutPersistence.load();
    set({
      layout: loaded ?? DEFAULT_WORKSPACE_LAYOUT,
      hydrated: true,
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
  setPickerOpen: (pickerOpen) => set({ pickerOpen }),
  openPicker: () => set({ pickerOpen: true }),
}));
