"use client";

import { create } from "zustand";

const COLLAPSED_HEIGHT = 46;

export type ScrollbackEntry = {
  id: string;
  kind: "input" | "output" | "error" | "sent";
  text: string;
};

type ConsoleState = {
  open: boolean;
  scrollback: ScrollbackEntry[];
  toggle: () => void;
  setOpen: (open: boolean) => void;
  appendScrollback: (entry: Omit<ScrollbackEntry, "id">) => void;
  clearScrollback: () => void;
  collapsedHeight: number;
};

let scrollbackSeq = 0;

export const useConsoleStore = create<ConsoleState>((set) => ({
  open: false,
  scrollback: [],
  collapsedHeight: COLLAPSED_HEIGHT,
  toggle: () => set((s) => ({ open: !s.open })),
  setOpen: (open) => set({ open }),
  appendScrollback: (entry) =>
    set((s) => ({
      scrollback: [
        ...s.scrollback,
        { ...entry, id: `sb-${++scrollbackSeq}` },
      ],
    })),
  clearScrollback: () => set({ scrollback: [] }),
}));
