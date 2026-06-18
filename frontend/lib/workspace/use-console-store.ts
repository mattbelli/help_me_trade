"use client";

import { create } from "zustand";

const COLLAPSED_HEIGHT = 46;
const DEFAULT_OPEN_HEIGHT = 420;
const MIN_OPEN_HEIGHT = 200;
const MAX_OPEN_HEIGHT = 720;

export type ScrollbackEntry = {
  id: string;
  kind: "input" | "output" | "error" | "sent";
  text: string;
};

type ConsoleState = {
  open: boolean;
  height: number;
  scrollback: ScrollbackEntry[];
  toggle: () => void;
  setOpen: (open: boolean) => void;
  setHeight: (height: number) => void;
  appendScrollback: (entry: Omit<ScrollbackEntry, "id">) => void;
  clearScrollback: () => void;
  collapsedHeight: number;
  minOpenHeight: number;
  maxOpenHeight: number;
};

let scrollbackSeq = 0;

export const useConsoleStore = create<ConsoleState>((set) => ({
  open: false,
  height: DEFAULT_OPEN_HEIGHT,
  scrollback: [],
  collapsedHeight: COLLAPSED_HEIGHT,
  minOpenHeight: MIN_OPEN_HEIGHT,
  maxOpenHeight: MAX_OPEN_HEIGHT,
  toggle: () => set((s) => ({ open: !s.open })),
  setOpen: (open) => set({ open }),
  setHeight: (height) =>
    set({
      height: Math.min(MAX_OPEN_HEIGHT, Math.max(MIN_OPEN_HEIGHT, height)),
    }),
  appendScrollback: (entry) =>
    set((s) => ({
      scrollback: [
        ...s.scrollback,
        { ...entry, id: `sb-${++scrollbackSeq}` },
      ],
    })),
  clearScrollback: () => set({ scrollback: [] }),
}));
