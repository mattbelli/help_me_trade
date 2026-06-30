/**
 * ThemeProvider — owns the active theme. The user picks a ThemeMode in Settings
 * (a fixed palette or `system`); we resolve it to a concrete palette, apply it to
 * the live `colors` object, and remount the subtree (keyed by the resolved
 * palette) so every render-time `colors.*` read refreshes. The choice persists to
 * AsyncStorage. `system` follows the OS light/dark scheme (light → light, else
 * carbon).
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { useColorScheme, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';

import { applyColors, colors } from './colors';
import { isLightTheme, themes, type ThemeKey, type ThemeMode } from './themes';

const STORAGE_KEY = 'hmt_theme_mode';

interface ThemeValue {
  mode: ThemeMode;
  resolved: ThemeKey;
  setMode: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeValue>({
  mode: 'carbon',
  resolved: 'carbon',
  setMode: () => {},
});

function resolve(mode: ThemeMode, scheme: string | null | undefined): ThemeKey {
  if (mode === 'system') return scheme === 'light' ? 'light' : 'carbon';
  return mode;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const scheme = useColorScheme();
  const [mode, setModeState] = useState<ThemeMode>('carbon');

  // Restore the saved choice once on mount (default carbon until then).
  useEffect(() => {
    let cancelled = false;
    void AsyncStorage.getItem(STORAGE_KEY).then((saved) => {
      if (!cancelled && saved && (saved in themes || saved === 'system')) {
        setModeState(saved as ThemeMode);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const setMode = (next: ThemeMode) => {
    setModeState(next);
    void AsyncStorage.setItem(STORAGE_KEY, next);
  };

  const resolved = resolve(mode, scheme);
  // Apply synchronously so the keyed subtree below renders with the new palette.
  applyColors(themes[resolved]);

  return (
    <ThemeContext.Provider value={{ mode, resolved, setMode }}>
      <StatusBar style={isLightTheme(resolved) ? 'dark' : 'light'} />
      <View key={resolved} style={{ flex: 1, backgroundColor: colors.bg }}>
        {children}
      </View>
    </ThemeContext.Provider>
  );
}

export function useThemeMode(): ThemeValue {
  return useContext(ThemeContext);
}
