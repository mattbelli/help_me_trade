/**
 * ThemeProvider — owns the active theme. The user picks a ThemeMode in Settings
 * (a fixed palette or `system`); we resolve it to a concrete palette, apply it to
 * the live `colors` object, and remount the subtree (keyed by the resolved
 * palette) so every render-time `colors.*` read refreshes. The choice persists to
 * AsyncStorage. `system` follows the OS light/dark scheme (light → light, else
 * carbon).
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useColorScheme, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';

import { getCustomThemes } from '@/api/endpoints';
import type { WebCustomTheme } from '@/api/types';

import { applyColors, colors } from './colors';
import { webThemeIsLight, webThemeToPalette } from './custom-themes';
import { makeColors } from './make-colors';
import { isLightTheme, THEME_OPTIONS, themes, type ThemeKey, type ThemeMode } from './themes';

const STORAGE_KEY = 'hmt_theme_mode';
const CUSTOM_KEY = 'hmt_custom_themes_v1';

interface ThemeValue {
  mode: ThemeMode | string;
  resolved: string;
  isLight: boolean;
  setMode: (mode: ThemeMode | string) => void;
  options: { mode: string; label: string }[];
}

const ThemeContext = createContext<ThemeValue>({
  mode: 'carbon',
  resolved: 'carbon',
  isLight: false,
  setMode: () => {},
  options: THEME_OPTIONS,
});

function isValidSavedMode(saved: string): boolean {
  return saved in themes || saved === 'system' || saved.startsWith('custom-');
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const scheme = useColorScheme();
  const [mode, setModeState] = useState<ThemeMode | string>('carbon');
  const [custom, setCustom] = useState<WebCustomTheme[]>([]);

  // Restore the saved choice once on mount (default carbon until then).
  useEffect(() => {
    let cancelled = false;
    void AsyncStorage.getItem(STORAGE_KEY).then((saved) => {
      if (!cancelled && saved && isValidSavedMode(saved)) {
        setModeState(saved);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Hydrate custom themes from cache, then refresh from serv.
  useEffect(() => {
    let cancelled = false;
    void AsyncStorage.getItem(CUSTOM_KEY).then((cached) => {
      if (!cancelled && cached) {
        try {
          setCustom(JSON.parse(cached) as WebCustomTheme[]);
        } catch {
          // ignore corrupt cache
        }
      }
    });
    void getCustomThemes()
      .then((r) => {
        if (cancelled) return;
        const themes = r.custom_themes ?? [];
        setCustom(themes);
        void AsyncStorage.setItem(CUSTOM_KEY, JSON.stringify(themes));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const customColors = useMemo(
    () => Object.fromEntries(custom.map((t) => [t.id, makeColors(webThemeToPalette(t))])),
    [custom],
  );

  const customMeta = useMemo(
    () =>
      Object.fromEntries(
        custom.map((t) => [t.id, { name: t.name, isLight: webThemeIsLight(t) }]),
      ),
    [custom],
  );

  const setMode = (next: ThemeMode | string) => {
    setModeState(next);
    void AsyncStorage.setItem(STORAGE_KEY, next);
  };

  const resolved =
    mode === 'system' ? (scheme === 'light' ? 'light' : 'carbon') : mode;
  const isBuiltin = resolved in themes;
  const activeColors = isBuiltin
    ? themes[resolved as ThemeKey]
    : (customColors[resolved] ?? themes.carbon);
  const activeIsLight = isBuiltin
    ? isLightTheme(resolved as ThemeKey)
    : (customMeta[resolved]?.isLight ?? false);

  // Apply synchronously so the keyed subtree below renders with the new palette.
  applyColors(activeColors);

  const options = useMemo(
    () => [...THEME_OPTIONS, ...custom.map((t) => ({ mode: t.id, label: t.name }))],
    [custom],
  );

  return (
    <ThemeContext.Provider value={{ mode, resolved, isLight: activeIsLight, setMode, options }}>
      <StatusBar style={activeIsLight ? 'dark' : 'light'} />
      <View key={resolved} style={{ flex: 1, backgroundColor: colors.bg }}>
        {children}
      </View>
    </ThemeContext.Provider>
  );
}

export function useThemeMode(): ThemeValue {
  return useContext(ThemeContext);
}
