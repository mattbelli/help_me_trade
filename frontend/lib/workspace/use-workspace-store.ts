"use client";

import { create } from "zustand";

import {
  DEFAULT_WORKSPACE_LAYOUT,
  localStorageLayoutPersistence,
  type WorkspaceLayoutItem,
} from "./layout-persistence";

type WorkspaceState = {
  layout: WorkspaceLayoutItem[];
  hydrated: boolean;
  hydrate: () => void;
  updateLayout: (layout: WorkspaceLayoutItem[]) => void;
};

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  layout: DEFAULT_WORKSPACE_LAYOUT,
  hydrated: false,
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
}));
