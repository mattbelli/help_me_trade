"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import { Skeleton } from "@/components/ui/skeleton";
import { TerminalButton } from "@/components/ui/terminal/terminal-button";
import {
  fetchDecisions,
  fetchMyTrades,
  fetchMyTradesSummary,
  type DecisionStats,
  type JournalDecision,
  type MyTrade,
  type MyTradesSummary,
} from "@/lib/journal-api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

type View = "ai" | "me";

function money(s: string | null): string {
  if (s == null) return "—";
  const n = Number(s);
  if (Number.isNaN(n)) return "—";
  return `${n >= 0 ? "+" : "−"}$${Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function pct(v: number | null): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function rMultiple(v: number | null): string {
  return v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}R`;
}

function pnlColor(s: string | null): string {
  if (s == null) return "var(--fg-dim)";
  const n = Number(s);
  if (Number.isNaN(n) || n === 0) return "var(--fg-dim)";
  return n > 0 ? "var(--c-green)" : "var(--c-red)";
}

function outcomeColor(outcome: string | null): string {
  if (outcome === "win") return "var(--c-green)";
  if (outcome === "loss") return "var(--c-red)";
  return "var(--fg-dim)";
}

function ToggleButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "px-2 py-0.5 text-[11px] font-medium uppercase tracking-[0.05em] transition-colors",
        active
          ? "bg-[var(--c-accent)] text-[var(--bg)]"
          : "text-[var(--fg-dim)] hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

export function JournalWidget({ config }: WidgetProps) {
  const [view, setView] = useState<View>("ai");
  const [decisions, setDecisions] = useState<JournalDecision[] | null>(null);
  const [stats, setStats] = useState<DecisionStats | null>(null);
  const [trades, setTrades] = useState<MyTrade[] | null>(null);
  const [summary, setSummary] = useState<MyTradesSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  const load = useCallback(async (which: View) => {
    setLoading(true);
    setError(null);
    try {
      if (which === "ai") {
        const res = await fetchDecisions(undefined, 30);
        if (cancelledRef.current) return;
        setDecisions(res.decisions);
        setStats(res.stats);
      } else {
        const [res, sum] = await Promise.all([
          fetchMyTrades(undefined, 30),
          fetchMyTradesSummary(),
        ]);
        if (cancelledRef.current) return;
        setTrades(res.trades);
        setSummary(sum);
      }
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to load journal");
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(view);
  }, [view, load]);

  const loaded =
    view === "ai" ? decisions !== null : trades !== null && summary !== null;

  return (
    <div className="flex h-full flex-col overflow-hidden font-mono text-[12px]">
      {/* Header: AI ⇄ Me toggle + refresh */}
      <div className="flex items-center justify-between border-b border-[var(--border)] px-2 py-1.5">
        <div className="flex overflow-hidden rounded border border-[var(--border)]">
          <ToggleButton active={view === "ai"} onClick={() => setView("ai")}>
            AI
          </ToggleButton>
          <span className="self-center text-[10px] text-[var(--fg-faint)]">⇄</span>
          <ToggleButton active={view === "me"} onClick={() => setView("me")}>
            Me
          </ToggleButton>
        </div>
        <TerminalButton onClick={() => void load(view)} disabled={loading}>
          {loading ? "…" : "↻"}
        </TerminalButton>
      </div>

      {error ? (
        <p className="px-2 py-1 text-[10px] text-[var(--c-red)]">{error}</p>
      ) : null}

      {!loaded && loading ? (
        <Skeleton className="m-3 h-full min-h-24 rounded-md" />
      ) : view === "ai" ? (
        <AiView
          decisions={decisions ?? []}
          stats={stats}
          linked={linked}
          publish={publish}
        />
      ) : (
        <MeView
          trades={trades ?? []}
          summary={summary}
          linked={linked}
          publish={publish}
        />
      )}
    </div>
  );
}

// --- AI view: the agent's own paper calls -----------------------------------

function AiView({
  decisions,
  stats,
  linked,
  publish,
}: {
  decisions: JournalDecision[];
  stats: DecisionStats | null;
  linked: boolean;
  publish: (s: string) => void;
}) {
  if (decisions.length === 0) {
    return (
      <p className="p-3 text-[11px] text-[var(--fg-dim)]">
        No agent decisions yet — arm a strategy or run a paper proposal.
      </p>
    );
  }
  const strat = (stats?.by_strategy ?? []).filter((s) => s.n > 0);
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      {/* Per-strategy stats strip */}
      {strat.length > 0 ? (
        <div className="flex flex-wrap gap-1.5 border-b border-[var(--border)] px-2 py-1.5">
          {strat.map((s) => (
            <span
              key={s.strategy_id}
              className="rounded border border-[var(--border)] px-1.5 py-0.5 text-[10px] text-[var(--fg-dim)]"
              title={`${s.wins}W / ${s.losses}L`}
            >
              <span className="text-foreground">{s.strategy_id}</span> ·{" "}
              {s.win_rate == null ? "n/a" : pct(s.win_rate)} ·{" "}
              {rMultiple(s.avg_r_multiple)} · {money(s.net_realized_pnl)} · n{s.n}
            </span>
          ))}
        </div>
      ) : null}

      {/* Decisions list */}
      {decisions.map((d) => (
        <div
          key={d.proposal_id ?? `${d.symbol}-${d.created_at}`}
          className="border-b border-[var(--border)] px-2 py-1.5 transition-colors hover:bg-[var(--bg-hover)]"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium">
              {d.symbol ? (
                <ClickableSymbol
                  symbol={d.symbol}
                  linked={linked}
                  onActivate={() => publish(d.symbol as string)}
                />
              ) : (
                "—"
              )}
            </span>
            <span className="flex items-center gap-2 tabular-nums text-[11px]">
              <span className="text-[var(--fg-faint)]">win% {pct(d.win_prob)}</span>
              <span style={{ color: outcomeColor(d.outcome) }}>
                {d.outcome ?? d.decision ?? "pending"}
              </span>
              {d.r_multiple != null ? (
                <span style={{ color: pnlColor(d.realized_pnl) }}>
                  {rMultiple(d.r_multiple)}
                </span>
              ) : null}
              <span style={{ color: pnlColor(d.realized_pnl) }}>
                {money(d.realized_pnl)}
              </span>
            </span>
          </div>
          {d.thesis ? (
            <p className="truncate text-[10px] text-[var(--fg-faint)]">{d.thesis}</p>
          ) : null}
        </div>
      ))}
    </div>
  );
}

// --- Me view: the user's real broker trades ---------------------------------

function MeView({
  trades,
  summary,
  linked,
  publish,
}: {
  trades: MyTrade[];
  summary: MyTradesSummary | null;
  linked: boolean;
  publish: (s: string) => void;
}) {
  const behavioral = summary?.behavioral;
  const netCash = summary?.net_cash_realized;
  const topSyms = (behavioral?.top_symbols ?? [])
    .map((t) => t.symbol)
    .slice(0, 2)
    .join("/");

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      {/* Net-cash truth + behavioral summary line */}
      {summary ? (
        <div className="border-b border-[var(--border)] px-2 py-1.5 text-[10px] text-[var(--fg-dim)]">
          <div>
            <span className="text-[var(--fg-faint)]">net-cash realized </span>
            <span
              className="tabular-nums"
              style={{ color: pnlColor(netCash?.realized_closed ?? null) }}
            >
              {money(netCash?.realized_closed ?? null)}
            </span>
            <span className="text-[var(--fg-faint)]">
              {" "}
              · win {netCash?.closed_win_rate == null ? "n/a" : pct(netCash.closed_win_rate)}
              {" "}
              (per-symbol) · {behavioral?.trade_count ?? 0} trades
              {topSyms ? ` · top ${topSyms}` : ""}
            </span>
          </div>
          <div className="text-[var(--fg-faint)]">
            Net-cash = source of truth; per-trade P&amp;L below is FIFO-approximate.
          </div>
        </div>
      ) : null}

      {trades.length === 0 ? (
        <p className="p-3 text-[11px] text-[var(--fg-dim)]">
          No closed round-trips in the imported history.
        </p>
      ) : (
        trades.map((t, idx) => (
          <div
            key={`${t.symbol}-${t.close_date}-${idx}`}
            className="grid grid-cols-[minmax(0,1fr)_3rem_5rem_2.5rem_2.5rem] items-center gap-x-1 border-b border-[var(--border)] px-2 py-1.5 transition-colors hover:bg-[var(--bg-hover)]"
          >
            <span className="truncate font-medium">
              {t.symbol ? (
                <ClickableSymbol
                  symbol={t.symbol}
                  linked={linked}
                  onActivate={() => publish(t.symbol as string)}
                />
              ) : (
                "—"
              )}
            </span>
            <span className="text-right text-[10px] text-[var(--fg-faint)]">
              {t.side}
            </span>
            <span
              className="text-right tabular-nums"
              style={{ color: pnlColor(t.pnl) }}
              title="FIFO-approximate"
            >
              {money(t.pnl)}
            </span>
            <span className="text-right text-[10px] tabular-nums text-[var(--fg-dim)]">
              {t.hold_days == null ? "—" : `${t.hold_days}d`}
            </span>
            <span
              className="text-right text-[10px] uppercase"
              style={{ color: outcomeColor(t.outcome) }}
            >
              {t.outcome === "win" ? "W" : t.outcome === "loss" ? "L" : "—"}
            </span>
          </div>
        ))
      )}
    </div>
  );
}
