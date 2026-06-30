/**
 * useCountdown — ticks once a second toward an ISO expiry. Proposals carry a
 * TTL (≤1h); the card shows the remaining time and disables Approve at zero.
 */
import { useEffect, useState } from 'react';

export interface Countdown {
  secondsLeft: number;
  expired: boolean;
  /** "M:SS" (or "0:00" once expired). */
  label: string;
  /** True in the final stretch — drives the warning color. */
  urgent: boolean;
}

function compute(expiresAt: string | null): { secondsLeft: number } {
  if (!expiresAt) return { secondsLeft: Number.POSITIVE_INFINITY };
  const ms = new Date(expiresAt).getTime() - Date.now();
  return { secondsLeft: Math.max(0, Math.floor(ms / 1000)) };
}

export function useCountdown(expiresAt: string | null): Countdown {
  const [secondsLeft, setSecondsLeft] = useState(() => compute(expiresAt).secondsLeft);

  useEffect(() => {
    setSecondsLeft(compute(expiresAt).secondsLeft);
    if (!expiresAt) return;
    const id = setInterval(() => setSecondsLeft(compute(expiresAt).secondsLeft), 1000);
    return () => clearInterval(id);
  }, [expiresAt]);

  const finite = Number.isFinite(secondsLeft);
  const expired = finite && secondsLeft <= 0;
  const m = finite ? Math.floor(secondsLeft / 60) : 0;
  const s = finite ? secondsLeft % 60 : 0;

  return {
    secondsLeft,
    expired,
    label: finite ? `${m}:${String(s).padStart(2, '0')}` : '—',
    urgent: finite && secondsLeft > 0 && secondsLeft <= 60,
  };
}
