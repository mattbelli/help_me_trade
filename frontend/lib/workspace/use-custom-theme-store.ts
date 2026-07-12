"use client";

import { create } from "zustand";

import { fetchCustomThemes, saveCustomThemes } from "@/lib/api";

import {
  captureThemeVars,
  colorSchemeFromBackground,
  injectCustomThemeStyles,
  loadLocalCustomThemes,
  newCustomThemeId,
  saveLocalCustomThemes,
  type CustomTheme,
} from "./custom-themes";
import { isBuiltinTheme, WORKSPACE_THEMES, type WorkspaceTheme } from "./themes";

type CustomThemeState = {
  themes: CustomTheme[];
  hydrated: boolean;
  hydrate: () => Promise<void>;
  createFromCurrent: (name: string, base: string) => string;
  updateVars: (id: string, vars: Record<string, string>) => void;
  rename: (id: string, name: string) => void;
  remove: (id: string) => void;
};

// ── Debounced server save ────────────────────────────────────────────────────
let _serverSaveTimer: ReturnType<typeof setTimeout> | null = null;
const SERVER_SAVE_DEBOUNCE_MS = 500;

function scheduleServerSave(themes: CustomTheme[]): void {
  if (_serverSaveTimer !== null) clearTimeout(_serverSaveTimer);
  _serverSaveTimer = setTimeout(() => {
    _serverSaveTimer = null;
    saveCustomThemes(themes).catch((err) => {
      console.warn("[custom-themes] server save error (swallowed):", err);
    });
  }, SERVER_SAVE_DEBOUNCE_MS);
}

function persist(themes: CustomTheme[]): void {
  saveLocalCustomThemes(themes);
  scheduleServerSave(themes);
  injectCustomThemeStyles(themes);
}

function resolvedColorScheme(vars: Record<string, string>): "dark" | "light" {
  return colorSchemeFromBackground(vars["--surface-canvas"] ?? "#000000");
}

export const useCustomThemeStore = create<CustomThemeState>((set, get) => ({
  // Seed synchronously from localStorage so the next-themes `themes` prop knows
  // custom ids on first client render.
  themes: loadLocalCustomThemes(),
  hydrated: false,

  hydrate: async () => {
    let serverThemes: CustomTheme[] | null = null;
    try {
      serverThemes = await fetchCustomThemes();
    } catch {
      // server unreachable — fall back to local
    }

    if (serverThemes && serverThemes.length > 0) {
      saveLocalCustomThemes(serverThemes);
      set({ themes: serverThemes, hydrated: true });
      injectCustomThemeStyles(serverThemes);
      return;
    }

    const local = loadLocalCustomThemes();
    if (local.length > 0) {
      // Push local up ONLY when the server CONFIRMED it has no themes
      // (serverThemes === []). When the fetch FAILED (null — e.g. backend
      // mid-restart), pushing would let any device with stale localStorage
      // silently clobber the canonical server copy — this deleted a theme
      // and reverted another on 2026-07-04 (restored from backup).
      if (serverThemes !== null) scheduleServerSave(local);
      set({ themes: local, hydrated: true });
      injectCustomThemeStyles(local);
      return;
    }

    set({ themes: serverThemes ?? [], hydrated: true });
    injectCustomThemeStyles(get().themes);
  },

  createFromCurrent: (name, base) => {
    // Captures the CURRENT resolved vars off <html>. When the editor is open it
    // has applied an inline preview, so live edits are captured automatically.
    const vars = captureThemeVars();
    const id = newCustomThemeId();
    const now = new Date().toISOString();
    const theme: CustomTheme = {
      id,
      name: name.trim() || "Custom",
      base: isBuiltinTheme(base) ? (base as WorkspaceTheme) : base,
      colorScheme: resolvedColorScheme(vars),
      vars,
      createdAt: now,
      updatedAt: now,
    };
    const next = [...get().themes, theme];
    set({ themes: next });
    persist(next);
    return id;
  },

  updateVars: (id, vars) => {
    // Replace the full resolved knob var map (the editor's live edits).
    const next = get().themes.map((t) => {
      if (t.id !== id) return t;
      return {
        ...t,
        vars,
        colorScheme: resolvedColorScheme(vars),
        updatedAt: new Date().toISOString(),
      };
    });
    set({ themes: next });
    persist(next);
  },

  rename: (id, name) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const next = get().themes.map((t) =>
      t.id === id ? { ...t, name: trimmed, updatedAt: new Date().toISOString() } : t,
    );
    set({ themes: next });
    persist(next);
  },

  remove: (id) => {
    const next = get().themes.filter((t) => t.id !== id);
    if (next.length === get().themes.length) return;
    set({ themes: next });
    persist(next);
  },
}));

export function customThemeIds(themes: CustomTheme[]): string[] {
  return themes.map((t) => t.id);
}

export { WORKSPACE_THEMES };
