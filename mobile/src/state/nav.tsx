/**
 * Tiny cross-tab navigation context. The cockpit provides it so any surface can
 * jump to another tab (e.g. Portfolio's pending-approval card → Inbox). Symbol
 * detail navigation uses expo-router directly, not this.
 */
import { createContext, useContext, type ReactNode } from 'react';

interface NavValue {
  openInbox: () => void;
  /** Toggle horizontal page-swipe (watchlist edit mode disables it for swipe-to-delete). */
  setSwipeEnabled: (enabled: boolean) => void;
}

const NavContext = createContext<NavValue>({ openInbox: () => {}, setSwipeEnabled: () => {} });

export function NavProvider({ value, children }: { value: NavValue; children: ReactNode }) {
  return <NavContext.Provider value={value}>{children}</NavContext.Provider>;
}

export function useNav(): NavValue {
  return useContext(NavContext);
}
