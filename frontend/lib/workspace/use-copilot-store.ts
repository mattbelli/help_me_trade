"use client";

import { create } from "zustand";

const OPEN_STORAGE_KEY = "hmt-copilot-open";
const PINNED_STORAGE_KEY = "hmt-copilot-pinned";
const WIDTH_STORAGE_KEY = "hmt-copilot-width";

export const COPILOT_MIN_WIDTH = 320;
export const COPILOT_MAX_WIDTH = 640;
export const COPILOT_DEFAULT_WIDTH = 380;

function clampWidth(width: number): number {
  return Math.min(COPILOT_MAX_WIDTH, Math.max(COPILOT_MIN_WIDTH, width));
}

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

function loadWidth(): number {
  if (typeof window === "undefined") return COPILOT_DEFAULT_WIDTH;
  try {
    const raw = Number.parseInt(
      localStorage.getItem(WIDTH_STORAGE_KEY) ?? "",
      10,
    );
    if (Number.isFinite(raw)) return clampWidth(raw);
  } catch {
    // ignore
  }
  return COPILOT_DEFAULT_WIDTH;
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

function saveWidth(width: number): void {
  try {
    localStorage.setItem(WIDTH_STORAGE_KEY, String(clampWidth(width)));
  } catch {
    // ignore quota / private mode
  }
}

type CopilotState = {
  open: boolean;
  pinned: boolean;
  width: number;
  hydrated: boolean;
  hydrate: () => void;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  setPinned: (pinned: boolean) => void;
  togglePinned: () => void;
  setWidth: (width: number) => void;
};

export const useCopilotStore = create<CopilotState>((set, get) => ({
  open: false,
  pinned: false,
  width: COPILOT_DEFAULT_WIDTH,
  hydrated: false,
  hydrate: () => {
    set({
      open: loadOpen(),
      pinned: loadPinned(),
      width: loadWidth(),
      hydrated: true,
    });
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
  setWidth: (width) => {
    const next = clampWidth(width);
    set({ width: next });
    saveWidth(next);
  },
}));
