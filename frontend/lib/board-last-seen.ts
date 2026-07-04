"use client";

import { useCallback, useEffect, useState } from "react";

export const BOARD_LAST_SEEN_KEY = "board:lastSeen";

const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Date-only values parse as local midnight; full ISO strings use Date.parse. */
export function parseUpdatedAt(value: string): number {
  if (DATE_ONLY_RE.test(value)) {
    const [y, m, d] = value.split("-").map(Number);
    return new Date(y, m - 1, d).getTime();
  }
  const ts = Date.parse(value);
  return Number.isNaN(ts) ? 0 : ts;
}

export function readBoardLastSeen(): number | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(BOARD_LAST_SEEN_KEY);
  if (!raw) return null;
  const ts = Number(raw);
  return Number.isFinite(ts) ? ts : null;
}

export function writeBoardLastSeen(timestamp = Date.now()): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(BOARD_LAST_SEEN_KEY, String(timestamp));
}

const LAST_SEEN_WRITE_DELAY_MS = 5_000;

export const BOARD_SEEN_CARDS_KEY = "board:seenCards";

export function readSeenCards(): Record<string, number> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(BOARD_SEEN_CARDS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeSeenCards(map: Record<string, number>): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(BOARD_SEEN_CARDS_KEY, JSON.stringify(map));
}

export function isCardNew(
  cardId: string,
  updatedAt: string,
  lastSeen: number | null,
  seenCards: Record<string, number>,
): boolean {
  if (lastSeen === null) return false;
  const ts = parseUpdatedAt(updatedAt);
  return ts > lastSeen && ts > (seenCards[cardId] ?? 0);
}

export function useBoardLastSeen() {
  const [lastSeen, setLastSeen] = useState<number | null>(() =>
    typeof window !== "undefined" ? readBoardLastSeen() : null,
  );
  const [seenCards, setSeenCards] = useState<Record<string, number>>(() =>
    typeof window !== "undefined" ? readSeenCards() : {},
  );

  useEffect(() => {
    const id = window.setTimeout(() => writeBoardLastSeen(), LAST_SEEN_WRITE_DELAY_MS);
    return () => window.clearTimeout(id);
  }, []);

  const markAllSeen = useCallback((maxCardTimestamp?: number) => {
    // "Seen" must clear EVERYTHING currently on the board — even a card whose
    // updated_at is (wrongly) in the future from clock skew or a hand-typed
    // stamp. Take whichever is later.
    const now = Math.max(Date.now(), maxCardTimestamp ?? 0);
    writeBoardLastSeen(now);
    setLastSeen(now);
    // Global seen supersedes every per-card entry.
    writeSeenCards({});
    setSeenCards({});
  }, []);

  const markCardSeen = useCallback((cardId: string, cardTimestamp: number) => {
    // Opening a card reads it — clamp to the card's own (possibly future)
    // timestamp so the marker clears no matter what.
    setSeenCards((prev) => {
      const next = { ...prev, [cardId]: Math.max(Date.now(), cardTimestamp) };
      writeSeenCards(next);
      return next;
    });
  }, []);

  return { lastSeen, seenCards, markAllSeen, markCardSeen };
}
