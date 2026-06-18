"use client";

import { create } from "zustand";

const OPEN_STORAGE_KEY = "hmt-copilot-open";
const PINNED_STORAGE_KEY = "hmt-copilot-pinned";

function loadOpen(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(OPEN_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function loadPinned(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(PINNED_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function saveOpen(open: boolean): void {
  try {
    localStorage.setItem(OPEN_STORAGE_KEY, open ? "true" : "false");
  } catch {
    // ignore quota / private mode
  }
}

function savePinned(pinned: boolean): void {
  try {
    localStorage.setItem(PINNED_STORAGE_KEY, pinned ? "true" : "false");
  } catch {
    // ignore quota / private mode
  }
}

type CopilotState = {
  open: boolean;
  pinned: boolean;
  hydrated: boolean;
  hydrate: () => void;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  setPinned: (pinned: boolean) => void;
  togglePinned: () => void;
};

export const useCopilotStore = create<CopilotState>((set, get) => ({
  open: false,
  pinned: false,
  hydrated: false,
  hydrate: () => {
    set({ open: loadOpen(), pinned: loadPinned(), hydrated: true });
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
  setPinned: (pinned) => {
    set({ pinned });
    savePinned(pinned);
  },
  togglePinned: () => {
    const next = !get().pinned;
    set({ pinned: next });
    savePinned(next);
  },
}));
