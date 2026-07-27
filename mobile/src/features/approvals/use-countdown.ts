/**
 * useCountdown — ticks toward an ISO expiry. Intraday proposals carry a short
 * TTL (≤1h, "M:SS"); overnight book proposals live ~20h (expires at next
 * session close) and read "19h 42m". The card disables Approve at zero.
 */
import { useEffect, useState } from 'react';

export interface Countdown {
  secondsLeft: number;
  expired: boolean;
  /** "19h 42m" above an hour, "M:SS" below (or "0:00" once expired). */
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
  const [state, setState] = useState(() => ({
    expiresAt,
    secondsLeft: compute(expiresAt).secondsLeft,
  }));
  // Prop changed since last render → re-sync during render (React's "adjusting
  // state when a prop changes" pattern; avoids a sync setState in the effect).
  if (state.expiresAt !== expiresAt) {
    setState({ expiresAt, secondsLeft: compute(expiresAt).secondsLeft });
  }

  useEffect(() => {
    if (!expiresAt) return;
    const id = setInterval(
      () => setState({ expiresAt, secondsLeft: compute(expiresAt).secondsLeft }),
      1000,
    );
    return () => clearInterval(id);
  }, [expiresAt]);

  const { secondsLeft } = state;
  const finite = Number.isFinite(secondsLeft);
  const expired = finite && secondsLeft <= 0;

  let label = '—';
  if (finite) {
    if (secondsLeft > 3600) {
      const h = Math.floor(secondsLeft / 3600);
      const m = Math.floor((secondsLeft % 3600) / 60);
      label = `${h}h ${m}m`;
    } else {
      const m = Math.floor(secondsLeft / 60);
      const s = secondsLeft % 60;
      label = `${m}:${String(s).padStart(2, '0')}`;
    }
  }

  return {
    secondsLeft,
    expired,
    label,
    urgent: finite && secondsLeft > 0 && secondsLeft <= 60,
  };
}
