"use client";

import { create } from "zustand";

const STORAGE_KEY = "hmt-copilot-open";

function loadOpen(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function saveOpen(open: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, open ? "true" : "false");
  } catch {
    // ignore quota / private mode
  }
}

type CopilotState = {
  open: boolean;
  hydrated: boolean;
  hydrate: () => void;
  setOpen: (open: boolean) => void;
  toggle: () => void;
};

export const useCopilotStore = create<CopilotState>((set, get) => ({
  open: false,
  hydrated: false,
  hydrate: () => {
    set({ open: loadOpen(), hydrated: true });
  },
  setOpen: (open) => {
    set({ open });
    saveOpen(open);
  },
  toggle: () => {
    const next = !get().open;
    set({ open: next });
    saveOpen(next);
  },
}));
