/**
 * The ACTIVE color tokens — what every screen consumes. Two read paths:
 *   - `useColors()` in components (reactive; re-renders on theme switch)
 *   - `colors` global (synced fallback for helpers / non-React code)
 *
 * `applyColors()` swaps the global in place; ThemeProvider also supplies the
 * active palette via ColorsContext. Defaults to Carbon.
 */
import { createContext, useContext } from 'react';

import { type Colors } from './make-colors';
import { themes } from './themes';

export const colors: Colors = { ...themes.carbon };

const ColorsContext = createContext<Colors>(colors);
export const ColorsProvider = ColorsContext.Provider;

export function useColors(): Colors {
  return useContext(ColorsContext);
}

/** Swap the active palette in place (keeps the global synced for non-hook readers). */
export function applyColors(next: Colors): void {
  Object.assign(colors, next);
}

export type { Colors };

/** Directional P&L color: green for ≥0, red for <0. The app's most-used helper. */
export function pnlColor(value: number): string {
  return value >= 0 ? colors.positive : colors.negative;
}

export function pnlWash(value: number): string {
  return value >= 0 ? colors.positiveWash : colors.negativeWash;
}
