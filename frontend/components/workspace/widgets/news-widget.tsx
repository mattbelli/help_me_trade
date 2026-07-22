"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { relativeTime } from "@/components/workspace/company-info-sections";
import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  fetchWatchlistNews,
  type WatchlistNewsItem,
} from "@/lib/ticker-info-api";
import type { ChannelId } from "@/lib/workspace/channels";
import { motionStd, useMotionSafe } from "@/lib/workspace/motion";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

const POLL_MS = 30_000;

// ---------------------------------------------------------------------------
// Bloop — a soft descending pitch-drop when a new headline lands (deliberately
// gentler than the notifications ding). Browsers gate audio on a prior user
// gesture; until then this silently no-ops.
// ---------------------------------------------------------------------------
let bloopCtx: AudioContext | null = null;

function playBloop(): void {
  try {
    bloopCtx ??= new AudioContext();
    if (bloopCtx.state === "suspended") void bloopCtx.resume();
    if (bloopCtx.state !== "running") return;
    const t0 = bloopCtx.currentTime;
    const osc = bloopCtx.createOscillator();
    const gain = bloopCtx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(660, t0);
    osc.frequency.exponentialRampToValueAtTime(330, t0 + 0.16);
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(0.04, t0 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.24);
    osc.connect(gain).connect(bloopCtx.destination);
    osc.start(t0);
    osc.stop(t0 + 0.26);
  } catch {
    // Audio unavailable — stay silent.
  }
}

function itemKey(item: WatchlistNewsItem): string {
  return item.url || `${item.headline}|${item.datetime ?? ""}`;
}

export function NewsWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const channelSymbol = useChannelSymbol(channel);
  // Scope comes from the header dropdown; unset falls back to the original
  // behavior — follow the channel's ticker when on one, else the watchlist.
  const scope =
    config.newsScope ?? (channel !== "none" ? "ticker" : "watchlist");
  const symbol =
    scope === "ticker" && channel !== "none" ? channelSymbol : undefined;
  const { publish, linked } = usePublishSymbol(channel);
  const bloopOn = config.bloop !== false;

  const [items, setItems] = useState<WatchlistNewsItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Keys already shown for the CURRENT scope — arrivals beyond this set are
  // "new" (animate + bloop). Null = first load of the scope seeds silently.
  const seenRef = useRef<Set<string> | null>(null);
  const [freshKeys, setFreshKeys] = useState<Set<string>>(new Set());
  const bloopRef = useRef(bloopOn);
  bloopRef.current = bloopOn;

  const scopeKey = scope === "ticker" ? `ticker:${symbol ?? ""}` : scope;

  const load = useCallback(async () => {
    if (scope === "ticker" && !symbol) {
      setItems([]);
      setLoading(false);
      return;
    }
    try {
      const res = await fetchWatchlistNews(
        50,
        symbol,
        scope === "all" || scope === "sp500" ? scope : undefined,
      );
      setError(null);
      const fresh = new Set<string>();
      if (seenRef.current === null) {
        seenRef.current = new Set(res.map(itemKey));
      } else {
        for (const item of res) {
          const key = itemKey(item);
          if (!seenRef.current.has(key)) {
            seenRef.current.add(key);
            fresh.add(key);
          }
        }
      }
      setItems(res);
      if (fresh.size > 0) {
        setFreshKeys(fresh);
        if (bloopRef.current) playBloop();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load news");
    } finally {
      setLoading(false);
    }
  }, [scope, symbol]);

  useEffect(() => {
    seenRef.current = null; // new scope: seed silently, never a bloop storm
    setFreshKeys(new Set());
    setItems([]);
    setLoading(true);
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
    // load's deps are exactly the scope; scopeKey keeps the reset honest.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey]);

  const entrance = useMotionSafe(motionStd);

  if (scope === "ticker" && !symbol) {
    return (
      <WidgetEmpty
        message={
          channel !== "none"
            ? "Click a ticker to follow its news"
            : "Ticker scope follows a channel — set one, or pick another scope"
        }
      />
    );
  }
  if (loading && items.length === 0) {
    return <WidgetLoading rows={4} cols={[1]} />;
  }
  if (error && items.length === 0) {
    return <WidgetError message={error} />;
  }
  if (items.length === 0) {
    const emptyByScope = {
      ticker: `No recent news for ${symbol}`,
      watchlist: "No watchlist news yet",
      sp500: "No S&P 500 news yet",
      all: "No news yet",
    } as const;
    return <WidgetEmpty message={emptyByScope[scope]} />;
  }

  return (
    <div className="flex h-full flex-col overflow-hidden px-1.5 py-1">
      <ul className="min-h-0 flex-1 space-y-1.5 overflow-auto scroll-shadow-y px-0.5 pb-1">
        <AnimatePresence initial={false}>
          {items.map((item) => {
            const key = itemKey(item);
            const isFresh = freshKeys.has(key);
            return (
              <motion.li
                key={key}
                layout="position"
                initial={isFresh ? { opacity: 0, y: -18 } : false}
                animate={{ opacity: 1, y: 0 }}
                transition={entrance}
                className="notif-card group rounded-md border border-[var(--border)] transition-[border-color] hover:border-[var(--border-strong,var(--border))]"
              >
                <div className="flex flex-col gap-1 px-2.5 py-2">
                  <div className="flex items-center gap-2">
                    {item.symbol ? (
                      <ClickableSymbol
                        symbol={item.symbol}
                        linked={linked}
                        onActivate={() => publish(item.symbol as string)}
                        className="font-mono text-[length:var(--fs-micro)] tracking-[0.08em] uppercase"
                      />
                    ) : null}
                    <span className="font-mono text-[length:var(--fs-micro)] lowercase text-[var(--fg-faint)]">
                      {item.source}
                    </span>
                    <span className="ml-auto shrink-0 font-mono text-[length:var(--fs-micro)] tabular-nums text-[var(--fg-faint)]">
                      {relativeTime(item.datetime)}
                    </span>
                  </div>
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[length:var(--fs-body)] leading-snug text-foreground transition-colors hover:text-[var(--c-accent)]"
                  >
                    {item.headline}
                    <ExternalLink className="mb-0.5 ml-1 inline size-2.5 text-[var(--fg-faint)]" />
                  </a>
                  {item.summary ? (
                    <span className="line-clamp-2 text-[length:var(--fs-table)] text-[var(--fg-dim)]">
                      {item.summary}
                    </span>
                  ) : null}
                </div>
              </motion.li>
            );
          })}
        </AnimatePresence>
      </ul>
    </div>
  );
}
