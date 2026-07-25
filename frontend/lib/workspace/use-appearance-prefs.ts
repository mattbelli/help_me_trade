"use client";

import { useEffect } from "react";
import { create } from "zustand";

const AUTO_HIDE_CHROME_KEY = "hmt-auto-hide-chrome";

function loadAutoHideChrome(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(AUTO_HIDE_CHROME_KEY) === "true";
  } catch {
    return false;
  }
}

function saveAutoHideChrome(value: boolean): void {
  try {
    localStorage.setItem(AUTO_HIDE_CHROME_KEY, value ? "true" : "false");
  } catch {
    // ignore quota / private mode
  }
}

type AppearanceState = {
  /** Toolbar + terminal strip slide away until the pointer nears their edge. */
  autoHideChrome: boolean;
  hydrated: boolean;
  hydrate: () => void;
  setAutoHideChrome: (value: boolean) => void;
};

// Defaults on first render, then hydrate() in an effect — reading localStorage
// at module scope would desync the prerendered HTML (same as the copilot store).
export const useAppearancePrefs = create<AppearanceState>((set) => ({
  autoHideChrome: false,
  hydrated: false,
  hydrate: () => set({ autoHideChrome: loadAutoHideChrome(), hydrated: true }),
  setAutoHideChrome: (value) => {
    set({ autoHideChrome: value });
    saveAutoHideChrome(value);
  },
}));

/** Reads the pref and hydrates it on mount — for the chrome that obeys it. */
export function useAutoHideChrome(): boolean {
  const autoHideChrome = useAppearancePrefs((s) => s.autoHideChrome);
  const hydrate = useAppearancePrefs((s) => s.hydrate);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  return autoHideChrome;
}
