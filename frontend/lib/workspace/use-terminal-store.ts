"use client";

import { create } from "zustand";

/**
 * Terminal drawer state — succeeds `use-console-store` (scrollback) and the
 * open/height halves of `use-copilot-store`. The Quake drawer grows out of the
 * always-mounted 40px strip; collapsed just clips it (overflow hidden), so
 * summoning is a pure height transition with no remount.
 */

export const TERMINAL_COLLAPSED_HEIGHT = 40;

const HEIGHT_STORAGE_KEY = "hmt-terminal-height";
const HISTORY_STORAGE_KEY = "hmt-terminal-history";
const HISTORY_MAX = 200;

const MIN_SVH = 0.25;
const MAX_SVH = 0.85;
const DEFAULT_SVH = 0.46;

function viewportPx(fraction: number): number {
  const h = typeof window === "undefined" ? 900 : window.innerHeight;
  return Math.round(h * fraction);
}

export function clampDrawerHeight(px: number): number {
  return Math.min(viewportPx(MAX_SVH), Math.max(viewportPx(MIN_SVH), px));
}

function loadHeight(): number {
  if (typeof window === "undefined") return viewportPx(DEFAULT_SVH);
  try {
    const raw = Number.parseInt(
      localStorage.getItem(HEIGHT_STORAGE_KEY) ?? "",
      10,
    );
    if (Number.isFinite(raw)) return clampDrawerHeight(raw);
  } catch {
    // ignore
  }
  return viewportPx(DEFAULT_SVH);
}

function saveHeight(px: number): void {
  try {
    localStorage.setItem(HEIGHT_STORAGE_KEY, String(px));
  } catch {
    // ignore
  }
}

function loadHistory(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(HISTORY_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed.filter((x) => typeof x === "string");
  } catch {
    // ignore
  }
  return [];
}

function saveHistory(history: string[]): void {
  try {
    localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(history));
  } catch {
    // ignore
  }
}

export type ScrollbackEntry = {
  id: string;
  kind: "input" | "output" | "error" | "sent";
  text: string;
};

type TerminalState = {
  open: boolean;
  height: number;
  hydrated: boolean;
  scrollback: ScrollbackEntry[];
  history: string[];
  hydrate: () => void;
  toggle: () => void;
  setOpen: (open: boolean) => void;
  setHeight: (px: number) => void;
  appendScrollback: (entry: Omit<ScrollbackEntry, "id">) => void;
  clearScrollback: () => void;
  pushHistory: (entry: string) => void;
};

let scrollbackSeq = 0;

export const useTerminalStore = create<TerminalState>((set, get) => ({
  open: false,
  height: viewportPx(DEFAULT_SVH),
  hydrated: false,
  scrollback: [],
  history: [],
  hydrate: () =>
    set({ height: loadHeight(), history: loadHistory(), hydrated: true }),
  toggle: () => set((s) => ({ open: !s.open })),
  setOpen: (open) => set({ open }),
  setHeight: (px) => {
    const next = clampDrawerHeight(px);
    set({ height: next });
    saveHeight(next);
  },
  appendScrollback: (entry) =>
    set((s) => ({
      scrollback: [...s.scrollback, { ...entry, id: `sb-${++scrollbackSeq}` }],
    })),
  clearScrollback: () => set({ scrollback: [] }),
  pushHistory: (entry) => {
    const trimmed = entry.trim();
    if (!trimmed) return;
    const prev = get().history;
    // Dedupe consecutive repeats.
    if (prev[prev.length - 1] === trimmed) return;
    const next = [...prev, trimmed].slice(-HISTORY_MAX);
    set({ history: next });
    saveHistory(next);
  },
}));
