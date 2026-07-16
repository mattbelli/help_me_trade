/**
 * ThemeProvider — owns the active theme. The user picks a ThemeMode in Settings
 * (a fixed palette or `system`); we resolve it to a concrete palette, sync the
 * global `colors` object, and supply the palette via ColorsProvider so
 * `useColors()` consumers re-render without remounting the navigator. The choice
 * persists to AsyncStorage. `system` follows the OS light/dark scheme (light →
 * light, else carbon).
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useColorScheme, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';

import { getCustomThemes } from '@/api/endpoints';
import type { WebCustomTheme } from '@/api/types';

import { applyColors, ColorsProvider } from './colors';
import { webThemeIsLight, webThemeToPalette } from './custom-themes';
import { makeColors } from './make-colors';
import { isLightTheme, THEME_OPTIONS, themes, type ThemeKey, type ThemeMode } from './themes';

const STORAGE_KEY = 'hmt_theme_mode';
const CUSTOM_KEY = 'hmt_custom_themes_v1';

type ThemeOption = {
  mode: string;
  label: string;
  swatch: string;
  kind: 'builtin' | 'system' | 'custom';
};

function themeOptionsFromBuiltins(): ThemeOption[] {
  return THEME_OPTIONS.map((opt) => {
    if (opt.mode === 'system') {
      return { ...opt, swatch: themes.carbon.accent, kind: 'system' as const };
    }
    return {
      ...opt,
      swatch: themes[opt.mode as ThemeKey].accent,
      kind: 'builtin' as const,
    };
  });
}

interface ThemeValue {
  mode: ThemeMode | string;
  resolved: string;
  isLight: boolean;
  setMode: (mode: ThemeMode | string) => void;
  options: ThemeOption[];
}

const ThemeContext = createContext<ThemeValue>({
  mode: 'carbon',
  resolved: 'carbon',
  isLight: false,
  setMode: () => {},
  options: themeOptionsFromBuiltins(),
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

  applyColors(activeColors);

  const options = useMemo(
    () => [
      ...themeOptionsFromBuiltins(),
      ...custom.map((t) => ({
        mode: t.id,
        label: t.name,
        swatch: customColors[t.id]?.accent ?? themes.carbon.accent,
        kind: 'custom' as const,
      })),
    ],
    [custom, customColors],
  );

  return (
    <ThemeContext.Provider value={{ mode, resolved, isLight: activeIsLight, setMode, options }}>
      <StatusBar style={activeIsLight ? 'dark' : 'light'} />
      <ColorsProvider value={activeColors}>
        <View style={{ flex: 1, backgroundColor: activeColors.bg }}>{children}</View>
      </ColorsProvider>
    </ThemeContext.Provider>
  );
}

export function useThemeMode(): ThemeValue {
  return useContext(ThemeContext);
}
