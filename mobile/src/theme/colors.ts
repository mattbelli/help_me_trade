/**
 * The ACTIVE color tokens — what every screen consumes (`import { colors } from
 * '@/theme'`). It's a single live object: `applyColors()` swaps the palette in
 * place (Object.assign) and ThemeProvider remounts the app subtree so render-time
 * reads pick up the new values. Defaults to Carbon.
 *
 * NOTE: read tokens at RENDER time (`colors.bg` inside a component / inline
 * style). A module-scope capture (`const x = colors.bg`) snapshots Carbon and
 * won't follow a theme switch — build such maps inside the component instead.
 */
import { type Colors } from './make-colors';
import { themes } from './themes';

export const colors: Colors = { ...themes.carbon };

/** Swap the active palette in place. ThemeProvider calls this, then remounts. */
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
