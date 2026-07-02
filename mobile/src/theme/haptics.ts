/**
 * Haptic vocabulary — a small, deliberate map so feedback means the same thing
 * everywhere. Wraps expo-haptics; all calls are fire-and-forget (never awaited
 * in a gesture hot path) and safely no-op if the platform can't vibrate.
 *
 * Semantics (RN_APP_BRIEF §4 — "calm-but-alive"):
 *  - selection: light tick for page changes, segment switches, list scrubs
 *  - tap:       a confirmed button press
 *  - success:   a trade approved/filled — the good outcome
 *  - warning:   a reversible caution (e.g. proposal about to expire)
 *  - error:     a rejected/blocked action from the server
 *  - impact:    a deliberate, weighty moment (slide-to-confirm landing)
 */

import * as Haptics from 'expo-haptics';

function safe(run: () => Promise<unknown>): void {
  try {
    void run();
  } catch {
    // Haptics are a nicety — never let a missing capability throw.
  }
}

export const haptics = {
  selection: () => safe(() => Haptics.selectionAsync()),
  tap: () => safe(() => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)),
  impact: () => safe(() => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium)),
  heavy: () => safe(() => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy)),
  success: () => safe(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)),
  warning: () => safe(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning)),
  error: () => safe(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error)),
} as const;
