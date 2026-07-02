/**
 * Motion tokens — durations + spring configs for Reanimated.
 *
 * Principle (RN_UI_PLAYBOOK §2/§4): all motion runs on the UI thread via worklets,
 * animating transform/opacity only. These presets keep timing consistent so the
 * whole app feels like one object, not a pile of components. Tuned by feel; the
 * playbook says to source exact curves from HIG + teardown in the polish phase.
 */

import { Easing, type WithSpringConfig, type WithTimingConfig } from 'react-native-reanimated';

/** Durations (ms). Keep transitions short; this is a glance-and-go cockpit. */
export const duration = {
  instant: 90,
  fast: 160,
  base: 240,
  slow: 360,
} as const;

/** Standard ease for timing-based fades/translations (iOS-like ease-out). */
export const timing = {
  fast: { duration: duration.fast, easing: Easing.out(Easing.cubic) },
  base: { duration: duration.base, easing: Easing.out(Easing.cubic) },
  slow: { duration: duration.slow, easing: Easing.out(Easing.cubic) },
} satisfies Record<string, WithTimingConfig>;

/** Springs — the default for anything gesture-driven or "alive". */
export const spring = {
  /** Snappy, minimal overshoot — buttons, toggles, small UI. */
  snappy: { damping: 26, stiffness: 320, mass: 0.9 },
  /** Gentle settle — cards, sheets, page elements entering. */
  gentle: { damping: 22, stiffness: 180, mass: 1 },
  /** Bouncy accent — celebratory micro-moments (use sparingly). */
  bouncy: { damping: 14, stiffness: 220, mass: 1 },
} satisfies Record<string, WithSpringConfig>;
