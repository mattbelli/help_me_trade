/**
 * Spacing, radii, hairlines, and elevation.
 *
 * Spacing is an 8pt-ish rhythm with a half-step at the bottom for dense rows.
 * Use the named scale, not magic numbers — consistent rhythm is half of "premium".
 */

export const spacing = {
  none: 0,
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  '2xl': 24,
  '3xl': 32,
  '4xl': 40,
  '5xl': 56,
  '6xl': 72,
} as const;

/** Corner radii — matches the web token ramp (10px base), softened for touch. */
export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 22, // cards / sheets
  '2xl': 28, // hero card
  pill: 999,
} as const;

/** One physical pixel hairline — sharper than 1dp on retina. */
import { StyleSheet } from 'react-native';
export const hairline = StyleSheet.hairlineWidth;

/**
 * Elevation presets. Uses the modern cross-platform `boxShadow` (RN deprecated the
 * individual shadow* style props), plus Android `elevation`. Dark UI reads depth
 * from soft, large, low-opacity shadows — not hard edges.
 */
export const elevation = {
  none: { boxShadow: 'none', elevation: 0 },
  card: { boxShadow: '0px 8px 30px rgba(0,0,0,0.35)', elevation: 6 },
  hero: { boxShadow: '0px 16px 44px rgba(0,0,0,0.50)', elevation: 12 },
} as const;

/** Standard touch target floor (HIG: 44pt). */
export const HIT_SLOP = { top: 8, bottom: 8, left: 8, right: 8 } as const;
export const MIN_TOUCH = 44;

export type Spacing = typeof spacing;
