/**
 * Theme barrel. Import tokens from here:
 *   import { colors, spacing, type, radius, haptics } from '@/theme';
 *
 * This app is single-theme (Carbon dark), so tokens are plain modules — no
 * provider/context needed. If a light mode is ever added, swap these for a hook.
 */

export { colors, applyColors, pnlColor, pnlWash, type Colors } from './colors';
export { themes, isLightTheme, THEME_OPTIONS, type ThemeKey, type ThemeMode } from './themes';
export { ThemeProvider, useThemeMode } from './theme-context';
export { spacing, radius, elevation, hairline, HIT_SLOP, MIN_TOUCH, type Spacing } from './spacing';
export { type, fonts, tabularNums, type TypeVariant } from './typography';
export { duration, timing, spring } from './motion';
export { haptics } from './haptics';

import { colors } from './colors';
import { spacing, radius, elevation } from './spacing';
import { type as typeScale } from './typography';

/** Convenience aggregate for the rare consumer that wants one object. */
export const theme = {
  colors,
  spacing,
  radius,
  elevation,
  type: typeScale,
} as const;

export type Theme = typeof theme;
