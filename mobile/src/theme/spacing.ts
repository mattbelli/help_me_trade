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
 * Elevation as layered shadow presets (iOS shadow* props).
 * Dark UI reads depth from soft, large, low-opacity shadows — not hard edges.
 */
export const elevation = {
  none: {
    shadowColor: 'transparent',
    shadowOpacity: 0,
    shadowRadius: 0,
    shadowOffset: { width: 0, height: 0 },
    elevation: 0,
  },
  card: {
    shadowColor: '#000000',
    shadowOpacity: 0.35,
    shadowRadius: 30,
    shadowOffset: { width: 0, height: 8 },
    elevation: 6,
  },
  hero: {
    shadowColor: '#000000',
    shadowOpacity: 0.5,
    shadowRadius: 44,
    shadowOffset: { width: 0, height: 16 },
    elevation: 12,
  },
} as const;

/** Standard touch target floor (HIG: 44pt). */
export const HIT_SLOP = { top: 8, bottom: 8, left: 8, right: 8 } as const;
export const MIN_TOUCH = 44;

export type Spacing = typeof spacing;
