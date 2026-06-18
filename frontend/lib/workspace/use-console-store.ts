"use client";

import { create } from "zustand";

export type ConsoleMode = "ask" | "cmd";

const COLLAPSED_HEIGHT = 46;
const DEFAULT_OPEN_HEIGHT = 420;
const MIN_OPEN_HEIGHT = 200;
const MAX_OPEN_HEIGHT = 720;

type ConsoleState = {
  open: boolean;
  height: number;
  mode: ConsoleMode;
  paletteOpen: boolean;
  toggle: () => void;
  setOpen: (open: boolean) => void;
  setHeight: (height: number) => void;
  setMode: (mode: ConsoleMode) => void;
  toggleMode: () => void;
  setPaletteOpen: (open: boolean) => void;
  openPalette: () => void;
  collapsedHeight: number;
  minOpenHeight: number;
  maxOpenHeight: number;
};

export const useConsoleStore = create<ConsoleState>((set, get) => ({
  open: false,
  height: DEFAULT_OPEN_HEIGHT,
  mode: "ask",
  paletteOpen: false,
  collapsedHeight: COLLAPSED_HEIGHT,
  minOpenHeight: MIN_OPEN_HEIGHT,
  maxOpenHeight: MAX_OPEN_HEIGHT,
  toggle: () => set((s) => ({ open: !s.open, paletteOpen: false })),
  setOpen: (open) => set({ open, paletteOpen: open ? get().paletteOpen : false }),
  setHeight: (height) =>
    set({
      height: Math.min(MAX_OPEN_HEIGHT, Math.max(MIN_OPEN_HEIGHT, height)),
    }),
  setMode: (mode) => set({ mode, paletteOpen: mode === "cmd" }),
  toggleMode: () =>
    set((s) => ({
      mode: s.mode === "ask" ? "cmd" : "ask",
      paletteOpen: s.mode === "ask",
      open: true,
    })),
  setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
  openPalette: () =>
    set({ open: true, mode: "cmd", paletteOpen: true }),
}));
