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
// Default = only the bottom band: the founder's attention gradient runs
// top-left → bottom-right, so the summoned drawer eats the least-important
// zone and the watchlist/top charts stay visible. Drag to taste (persisted).
const DEFAULT_SVH = 0.36;

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

// Horizontal layout: the drawer is a partial-width panel the founder can dock
// left / center / right (or anywhere) by dragging its header, and widen from
// either side edge. Fractions of the workspace width, persisted.
const WIDTH_STORAGE_KEY = "hmt-terminal-widthfrac";
const LEFT_STORAGE_KEY = "hmt-terminal-leftfrac";
export const MIN_WIDTH_FRAC = 0.35;
const DEFAULT_WIDTH_FRAC = 0.6;
// Right-anchored by default: coverage grows from the bottom-right corner —
// the low end of the founder's top-left attention gradient.
const DEFAULT_LEFT_FRAC = 1 - DEFAULT_WIDTH_FRAC;

function clampLayout(leftFrac: number, widthFrac: number): {
  leftFrac: number;
  widthFrac: number;
} {
  const w = Math.min(1, Math.max(MIN_WIDTH_FRAC, widthFrac));
  const l = Math.min(1 - w, Math.max(0, leftFrac));
  return { leftFrac: l, widthFrac: w };
}

function loadLayout(): { leftFrac: number; widthFrac: number } {
  const fallback = { leftFrac: DEFAULT_LEFT_FRAC, widthFrac: DEFAULT_WIDTH_FRAC };
  if (typeof window === "undefined") return fallback;
  try {
    const w = Number.parseFloat(localStorage.getItem(WIDTH_STORAGE_KEY) ?? "");
    const l = Number.parseFloat(localStorage.getItem(LEFT_STORAGE_KEY) ?? "");
    if (Number.isFinite(w) && Number.isFinite(l)) return clampLayout(l, w);
  } catch {
    // ignore
  }
  return fallback;
}

function saveLayout(leftFrac: number, widthFrac: number): void {
  try {
    localStorage.setItem(WIDTH_STORAGE_KEY, String(widthFrac));
    localStorage.setItem(LEFT_STORAGE_KEY, String(leftFrac));
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
  leftFrac: number;
  widthFrac: number;
  hydrated: boolean;
  scrollback: ScrollbackEntry[];
  history: string[];
  hydrate: () => void;
  toggle: () => void;
  setOpen: (open: boolean) => void;
  setHeight: (px: number) => void;
  setLayout: (leftFrac: number, widthFrac: number) => void;
  appendScrollback: (entry: Omit<ScrollbackEntry, "id">) => void;
  clearScrollback: () => void;
  pushHistory: (entry: string) => void;
};

let scrollbackSeq = 0;

export const useTerminalStore = create<TerminalState>((set, get) => ({
  open: false,
  height: viewportPx(DEFAULT_SVH),
  leftFrac: DEFAULT_LEFT_FRAC,
  widthFrac: DEFAULT_WIDTH_FRAC,
  hydrated: false,
  scrollback: [],
  history: [],
  hydrate: () =>
    set({
      height: loadHeight(),
      ...loadLayout(),
      history: loadHistory(),
      hydrated: true,
    }),
  toggle: () => set((s) => ({ open: !s.open })),
  setOpen: (open) => set({ open }),
  setHeight: (px) => {
    const next = clampDrawerHeight(px);
    set({ height: next });
    saveHeight(next);
  },
  setLayout: (leftFrac, widthFrac) => {
    const next = clampLayout(leftFrac, widthFrac);
    set(next);
    saveLayout(next.leftFrac, next.widthFrac);
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
