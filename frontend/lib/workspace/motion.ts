import { useReducedMotion, type Transition } from "motion/react";

// The 4 roles. Never free-hand a spring/duration outside these.
export const motionStd: Transition = { type: "spring", stiffness: 420, damping: 36 }; // panels, popovers, docking
export const motionSnappy: Transition = { type: "spring", stiffness: 700, damping: 34 }; // press, toggles, small transforms
export const motionFast: Transition = { duration: 0.13, ease: [0.2, 0, 0, 1] }; // color/opacity hovers
export const motionExit: Transition = { duration: 0.1, ease: [0.4, 0, 1, 1] }; // leave-fast
export const flashDecay: Transition = { duration: 0.5, ease: [0.4, 0, 0.2, 1] }; // tick-flash fade-back

/**
 * Collapses any of the roles above to an instant transition when the user
 * prefers reduced motion. Wrap every animated transition through this.
 */
export function useMotionSafe(transition: Transition): Transition {
  const reduce = useReducedMotion();
  return reduce ? { duration: 0 } : transition;
}
