"use client";

import { create } from "zustand";

// Dark/light *mode* management layered on top of the named-theme switcher.
// Persists the user's default Dark theme, default Light theme, and whether to
// follow the OS (`system`). Mirrors the localStorage-seeded pattern of
// use-custom-theme-store so the first client render already has the real values
// (SSR falls back to defaults — the moon menu only reads these after mount).

const STORAGE_KEY = "hmt-theme-mode-v1";

export const DEFAULT_DARK_THEME = "carbon";
export const DEFAULT_LIGHT_THEME = "light";

type Persisted = {
  system: boolean;
  defaultDark: string;
  defaultLight: string;
};

const DEFAULTS: Persisted = {
  system: false,
  defaultDark: DEFAULT_DARK_THEME,
  defaultLight: DEFAULT_LIGHT_THEME,
};

function load(): Persisted {
  if (typeof window === "undefined") return DEFAULTS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const p = JSON.parse(raw) as Partial<Persisted>;
    return {
      system: typeof p.system === "boolean" ? p.system : DEFAULTS.system,
      defaultDark:
        typeof p.defaultDark === "string" ? p.defaultDark : DEFAULTS.defaultDark,
      defaultLight:
        typeof p.defaultLight === "string"
          ? p.defaultLight
          : DEFAULTS.defaultLight,
    };
  } catch {
    return DEFAULTS;
  }
}

function save(state: Persisted): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // ignore quota / private mode
  }
}

type ThemeModeState = Persisted & {
  setSystem: (system: boolean) => void;
  setDefaultDark: (id: string) => void;
  setDefaultLight: (id: string) => void;
};

export const useThemeModeStore = create<ThemeModeState>((set, get) => ({
  ...load(),
  setSystem: (system) => {
    set({ system });
    const { defaultDark, defaultLight } = get();
    save({ system, defaultDark, defaultLight });
  },
  setDefaultDark: (defaultDark) => {
    set({ defaultDark });
    const { system, defaultLight } = get();
    save({ system, defaultDark, defaultLight });
  },
  setDefaultLight: (defaultLight) => {
    set({ defaultLight });
    const { system, defaultDark } = get();
    save({ system, defaultDark, defaultLight });
  },
}));

/** OS preference right now (SSR-safe). */
export function prefersDarkNow(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return true;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}
