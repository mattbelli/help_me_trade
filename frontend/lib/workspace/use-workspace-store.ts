"use client";

import { create } from "zustand";

import {
  DEFAULT_WORKSPACE_LAYOUT,
  localStorageLayoutPersistence,
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
  addWidget: (id: string) => boolean;
  removeWidget: (id: string) => void;
  setPickerOpen: (open: boolean) => void;
  openPicker: () => void;
};

const GRID_COLS = 12;

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
  addWidget: (id) => {
    const def = getWidgetDefinition(id);
    if (!def) return false;

    const { layout } = get();
    if (layout.some((item) => item.i === id)) return false;

    const { x, y } = findPlacement(layout, def.defaultSize);
    const next: WorkspaceLayoutItem = {
      i: id,
      x,
      y,
      w: def.defaultSize.w,
      h: def.defaultSize.h,
      minW: def.defaultSize.minW,
      minH: def.defaultSize.minH,
    };

    const merged = [...layout, next];
    set({ layout: merged, pickerOpen: false });
    localStorageLayoutPersistence.save(merged);
    return true;
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
