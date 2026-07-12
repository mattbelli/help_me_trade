"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import { fetchFilings, type FilingEvent, type FilingKind } from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;

type TabId = "all" | FilingKind;

const TABS: { id: TabId; label: string }[] = [
  { id: "all", label: "All" },
  { id: "insider_buy", label: "Insiders" },
  { id: "congress_trade", label: "Congress" },
  { id: "stake_13d", label: "13D" },
];

const TH =
  "border-b border-[var(--line-divider)] px-2 py-1.5 text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-faint)] uppercase";
const TD = "border-b border-[var(--line-divider)] px-2 py-1.5";
const ROW = "transition-colors hover:bg-[var(--bg-hover)]";

const MONTHS_SHORT = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

// EDGAR rows carry a full timestamp; congress rows a bare date (their real
// precision — disclosures are day-granular and legally up to 45 days stale).
function formatFiled(value: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (m) return `${MONTHS_SHORT[Number(m[2]) - 1]} ${Number(m[3])}`;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const day = d.toLocaleDateString("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
  });
  const time = d
    .toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    })
    .replace(" AM", "a")
    .replace(" PM", "p");
  return `${day} ${time}`;
}

function kindLabel(ev: FilingEvent): { text: string; className: string } {
  switch (ev.kind) {
    case "insider_buy":
      return { text: "insider buy", className: "text-[var(--c-green)]" };
    case "stake_13d":
      return { text: "13d stake", className: "text-[var(--c-violet)]" };
    case "congress_trade":
      return ev.side === "buy"
        ? { text: "congress buy", className: "text-[var(--c-green)]" }
        : { text: "congress sell", className: "text-[var(--c-red)]" };
    default:
      return { text: ev.kind, className: "text-[var(--fg-dim)]" };
  }
}

function whoText(ev: FilingEvent): string {
  const person = ev.person || "—";
  return ev.role ? `${person} · ${ev.role.toLowerCase()}` : person;
}

export function FilingsWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);

  const [tab, setTab] = useState<TabId>("all");
  const [events, setEvents] = useState<FilingEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const load = useCallback(async () => {
    try {
      const result = await fetchFilings(undefined, 150);
      if (cancelledRef.current) return;
      setEvents(result);
      setError(null);
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to load filings");
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
    };
  }, [load]);

  const visible = useMemo(() => {
    if (!events) return [];
    return tab === "all" ? events : events.filter((e) => e.kind === tab);
  }, [events, tab]);

  if (!events && !error) {
    return <WidgetLoading cols={[1, 1, 2, 1]} />;
  }
  if (error && !events) {
    return <WidgetError message={error} onRetry={() => void load()} />;
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-1 border-b border-[var(--line-divider)] px-3 py-1.5">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={cn(
              "rounded px-2 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
              tab === id
                ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
                : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
            )}
          >
            {label}
          </button>
        ))}
        {error ? (
          <span
            className="ml-auto font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--c-amber)] uppercase"
            title={error}
          >
            stale
          </span>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-auto scroll-shadow-y">
        {visible.length === 0 ? (
          <WidgetEmpty message="No filings yet — EDGAR polls every 10 min, congress every 6 h." />
        ) : (
          <div className="px-1.5 py-1">
            <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
              <thead>
                <tr>
                  <th className={cn(TH, "text-left")}>Filed</th>
                  <th className={cn(TH, "text-left")}>Ticker</th>
                  <th className={cn(TH, "text-left")}>Type</th>
                  <th className={cn(TH, "text-left")}>Who</th>
                  <th className={cn(TH, "text-right")}>Value</th>
                  <th className={cn(TH, "text-right")} aria-label="source link" />
                </tr>
              </thead>
              <tbody>
                {visible.map((ev) => {
                  const kind = kindLabel(ev);
                  return (
                    <tr key={ev.uid} className={ROW}>
                      <td
                        className={cn(TD, "whitespace-nowrap text-[var(--fg-faint)]")}
                        title={
                          ev.traded_at ? `traded ${ev.traded_at}` : undefined
                        }
                      >
                        {formatFiled(ev.filed_at)}
                      </td>
                      <td className={cn(TD, "font-medium text-foreground")}>
                        <ClickableSymbol
                          symbol={ev.symbol}
                          linked={linked}
                          onActivate={() => publish(ev.symbol)}
                        />
                      </td>
                      <td className={cn(TD, "whitespace-nowrap", kind.className)}>
                        {kind.text}
                      </td>
                      <td
                        className={cn(TD, "max-w-[24ch] truncate text-[var(--fg-dim)]")}
                        title={whoText(ev)}
                      >
                        {whoText(ev)}
                      </td>
                      <td
                        className={cn(
                          TD,
                          "text-right whitespace-nowrap tabular-nums text-[var(--fg-dim)]",
                        )}
                      >
                        {ev.value_label || "—"}
                      </td>
                      <td className={cn(TD, "text-right")}>
                        {ev.url ? (
                          <a
                            href={ev.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-[var(--fg-faint)] transition-colors hover:text-[var(--c-accent)]"
                            title="open source filing"
                          >
                            ↗
                          </a>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
