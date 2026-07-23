"use client";

// ACTOR DRILL-IN. Two shapes:
//  · mechanical strategy — real stat boxes (open positions, deployed cap, idea
//    grade counts) + open positions from /api/strategy-lab/paper + idea ledger
//    (origin `strategy:<id>`). NO equity curve: per-strategy equity is not
//    attributable (strategies share an account), stated honestly.
//  · book (llm/human) — equity curve (account-scoped history) + P&L / drawdown
//    / alpha stat strip + the book's ledger lanes (per-seat for the panel) +
//    the latest nightly analyst report rendered inline. Per-account positions
//    still lack an endpoint — that section stays honest-empty.

import { useEffect, useMemo, useState, type ReactNode } from "react";

import {
  fetchBars,
  fetchLabAccounts,
  fetchLabDayPnl,
  fetchLabIdeaLedger,
  fetchLabStrategies,
  fetchPortfolioHistory,
  fetchStrategyLabPaper,
  type LabIdeaRow,
  type LabStrategy,
  type PortfolioHistory,
  type StrategyLabPaperBook,
} from "@/lib/api";
import { fetchReportsByKind, type ReportRow } from "@/lib/reports-api";
import { ResearchMarkdown } from "@/components/research/research-markdown";

import { EquityChart } from "./equity-chart";
import type { ActorFamily } from "./lab-nav";
import {
  EmptyNote,
  ErrorNote,
  Frame,
  Hedge,
  LoadingNote,
  money,
  pct,
  SecHead,
  StatBox,
  StatStrip,
  Tag,
  TD,
  TD_NUM,
  TH,
  TH_NUM,
  Ticker,
  tone,
} from "./lab-ui";

export function ActorPage({
  family,
  actorId,
  onBack,
}: {
  family: ActorFamily;
  actorId: string;
  onBack: () => void;
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onBack}
        className="mb-3 text-[length:var(--fs-table)] text-[var(--fg-faint)] hover:text-[var(--fg-dim)]"
      >
        ‹ roster
      </button>
      {family === "mechanical" ? (
        <MechanicalActor id={actorId} />
      ) : (
        <BookActor id={actorId} family={family} />
      )}
    </div>
  );
}

// ── idea ledger table (shared) ──────────────────────────────────────────────
function IdeaLedger({
  rows,
  emptyLabel,
}: {
  rows: LabIdeaRow[] | null;
  emptyLabel: string;
}) {
  if (rows === null) return <LoadingNote label="loading ideas…" />;
  if (rows.length === 0) return <EmptyNote>{emptyLabel}</EmptyNote>;
  return (
    <Frame>
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={TH}>symbol</th>
            <th className={TH}>dir</th>
            <th className={TH}>status</th>
            <th className={TH_NUM}>ret</th>
            <th className={TH_NUM}>α vs spy</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="hover:bg-[var(--surface-hover)]">
              <td className={TD}>
                <Ticker symbol={r.symbol} />
              </td>
              <td className={TD}>
                <span
                  style={{
                    color:
                      r.direction === "short" ? "var(--neg)" : "var(--pos)",
                  }}
                >
                  {r.direction}
                </span>
              </td>
              <td className={TD}>
                <span className="text-[var(--fg-dim)]">
                  {r.user_action && r.user_action !== "none"
                    ? `${r.status} · ${r.user_action}`
                    : r.status}
                </span>
              </td>
              <td className={TD_NUM} style={{ color: tone(r.ret_pct) }}>
                {r.ret_pct != null ? pct(r.ret_pct, { sign: true }) : "—"}
              </td>
              <td className={TD_NUM} style={{ color: tone(r.alpha_pct) }}>
                {r.alpha_pct != null ? pct(r.alpha_pct, { sign: true }) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Frame>
  );
}

function ledgerCounts(rows: LabIdeaRow[]) {
  const resolved = rows.filter((r) => r.ret_pct != null);
  const wins = resolved.filter((r) => (r.ret_pct ?? 0) > 0).length;
  return { total: rows.length, resolved: resolved.length, wins };
}

// ── mechanical strategy ─────────────────────────────────────────────────────
function MechanicalActor({ id }: { id: string }) {
  const [strategy, setStrategy] = useState<LabStrategy | null | undefined>(
    undefined,
  );
  const [book, setBook] = useState<StrategyLabPaperBook | null>(null);
  const [ideas, setIdeas] = useState<LabIdeaRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [strategies, paper, ledger] = await Promise.all([
          fetchLabStrategies(),
          fetchStrategyLabPaper().catch(
            () => ({ books: [] }) as { books: StrategyLabPaperBook[] },
          ),
          fetchLabIdeaLedger(`strategy:${id}`, 50).catch(() => []),
        ]);
        if (cancelled) return;
        setStrategy(strategies.find((s) => s.id === id) ?? null);
        setBook(paper.books.find((b) => b.strategy_id === id) ?? null);
        setIdeas(ledger);
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "failed to load");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (strategy === undefined) return <LoadingNote label="loading strategy…" />;
  if (strategy === null) return <EmptyNote>unknown strategy: {id}</EmptyNote>;

  const positions = book?.positions ?? [];
  const counts = ideas ? ledgerCounts(ideas) : null;

  return (
    <div className="space-y-5">
      <Header
        title={strategy.name}
        tags={
          <>
            <Tag kind="mech">mech</Tag>
            <Tag kind={strategy.enabled ? "armed" : "disarmed"}>
              {strategy.enabled ? "armed" : "disarmed"}
            </Tag>
            <Tag kind={strategy.entry_mode === "auto" ? "auto" : "propose"}>
              {strategy.entry_mode}
            </Tag>
          </>
        }
        facts={[
          ["account", strategy.account_label ?? "—"],
          ["universe", strategy.universe || "—"],
          [
            "caps",
            `${strategy.max_concurrent_positions ?? "—"} pos · ${strategy.max_deployed_usd != null ? money(strategy.max_deployed_usd) : "—"}`,
          ],
        ]}
      />

      <StatStrip>
        <StatBox k="open positions" v={book?.n_positions ?? positions.length} />
        <StatBox
          k="mean unrealized"
          v={pct(book?.mean_unrealized_pct ?? null, { sign: true })}
          color={tone(book?.mean_unrealized_pct ?? null)}
        />
        <StatBox
          k="max deployed"
          v={
            strategy.max_deployed_usd != null
              ? money(strategy.max_deployed_usd)
              : "—"
          }
          s="cap"
        />
        <StatBox
          k="ideas graded"
          v={counts ? counts.resolved : "—"}
          s={counts ? `of ${counts.total} logged` : undefined}
        />
        <StatBox
          k="ledger wins"
          v={counts ? counts.wins : "—"}
          s={
            counts && counts.resolved > 0 ? (
              <Hedge title="raw count on a small n — not a calibrated win%">
                raw · n={counts.resolved}
              </Hedge>
            ) : (
              "no resolved ideas"
            )
          }
        />
      </StatStrip>

      <div className="rounded-none border-l-2 border-[var(--warn)] bg-[var(--surface-widget)] px-3 py-2 text-[length:var(--fs-table)] text-[var(--fg-dim)]">
        per-strategy equity: <span className="text-[var(--warn)]">not attributable yet</span>{" "}
        — this strategy shares an account with others (caps + P&L are
        account-wide). The boxes above are the parts that are real per-strategy.
      </div>

      <div>
        <SecHead
          label={`open positions · ${positions.length}`}
          meta={book?.planned_exit_note ?? undefined}
        />
        {positions.length === 0 ? (
          <EmptyNote>no open positions for this strategy right now</EmptyNote>
        ) : (
          <Frame>
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  <th className={TH}>symbol</th>
                  <th className={TH_NUM}>qty</th>
                  <th className={TH_NUM}>entry</th>
                  <th className={TH_NUM}>mark</th>
                  <th className={TH_NUM}>unreal %</th>
                  <th className={TH_NUM}>days</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.symbol} className="hover:bg-[var(--surface-hover)]">
                    <td className={TD}>
                      <Ticker symbol={p.symbol} />
                    </td>
                    <td className={TD_NUM}>{p.qty ?? "—"}</td>
                    <td className={TD_NUM}>
                      {p.entry_price != null
                        ? money(Number(p.entry_price), { cents: true })
                        : "—"}
                    </td>
                    <td className={TD_NUM}>
                      {p.mark != null ? money(p.mark, { cents: true }) : "—"}
                    </td>
                    <td
                      className={TD_NUM}
                      style={{ color: tone(p.unrealized_pct) }}
                    >
                      {p.unrealized_pct != null
                        ? pct(p.unrealized_pct, { sign: true })
                        : "—"}
                    </td>
                    <td className={TD_NUM}>{p.days_held ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Frame>
        )}
      </div>

      <div>
        <SecHead
          label="idea ledger"
          note="taken + not-taken, all graded"
          meta={`origin strategy:${id}`}
        />
        <IdeaLedger
          rows={ideas}
          emptyLabel="no graded ideas for this actor yet"
        />
      </div>
    </div>
  );
}

// ── book (llm / human) ──────────────────────────────────────────────────────

// Account → book wiring (mirror of research/books.py BOOKS — account ids are
// the registry's stable keys). Matt + LLM is advisory: report only, no lanes.
const BOOK_WIRING: Record<
  string,
  { reportKind: string; lanes: { origin: string; label: string; note?: string }[] }
> = {
  acct_alpaca_deepseek: {
    reportKind: "book_deepseek",
    lanes: [
      { origin: "book:deepseek", label: "advanced", note: "what it proposed" },
      { origin: "book:deepseek/considered", label: "considered", note: "weighed, not advanced" },
    ],
  },
  acct_alpaca_fable: {
    reportKind: "book_fable",
    lanes: [
      { origin: "book:fable", label: "advanced", note: "what it proposed" },
      { origin: "book:fable/considered", label: "considered", note: "weighed, not advanced" },
    ],
  },
  acct_alpaca_open: {
    reportKind: "book_panel",
    lanes: [
      { origin: "book:panel", label: "advanced", note: "judge's verdicts" },
      { origin: "book:panel/bull", label: "bull seat" },
      { origin: "book:panel/bear", label: "bear seat" },
      { origin: "book:panel/macro", label: "macro seat" },
      { origin: "book:panel/risk", label: "risk seat" },
    ],
  },
  acct_fc78907f4bda: { reportKind: "book_matt", lanes: [] },
};

function LaneLedger({
  origin,
  label,
  note,
}: {
  origin: string;
  label: string;
  note?: string;
}) {
  const [rows, setRows] = useState<LabIdeaRow[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchLabIdeaLedger(origin, 30)
      .then((r) => {
        if (!cancelled) setRows(r);
      })
      .catch(() => {
        if (!cancelled) setRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [origin]);

  const counts = rows ? ledgerCounts(rows) : null;
  return (
    <div>
      <SecHead
        label={label}
        note={note}
        meta={
          counts && counts.resolved > 0
            ? `${counts.wins}/${counts.resolved} resolved green · ${counts.total} tracked`
            : counts
              ? `${counts.total} tracked`
              : undefined
        }
      />
      <IdeaLedger rows={rows} emptyLabel="no ideas in this lane yet" />
    </div>
  );
}

function NightlyReport({ kind }: { kind: string }) {
  const [report, setReport] = useState<ReportRow | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    fetchReportsByKind(kind, 1)
      .then((rows) => {
        if (!cancelled) setReport(rows[0] ?? null);
      })
      .catch(() => {
        if (!cancelled) setReport(null);
      });
    return () => {
      cancelled = true;
    };
  }, [kind]);

  if (report === undefined) return <LoadingNote label="loading report…" />;
  if (report === null)
    return <EmptyNote>no nightly report filed yet</EmptyNote>;
  const proposals = Array.isArray(report.payload?.proposals)
    ? (report.payload.proposals as { symbol?: string; status?: string }[])
    : [];
  return (
    <div className="space-y-2">
      <div className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">
        {report.title} ·{" "}
        {new Date(report.generated_at).toLocaleString([], {
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        })}
        {proposals.length > 0 && (
          <>
            {" "}
            · orders:{" "}
            <span className="text-[var(--fg-dim)]">
              {proposals
                .map((p) => `${p.symbol ?? "?"} ${p.status ?? ""}`.trim())
                .join(", ")}
            </span>
          </>
        )}
      </div>
      <Frame>
        <div className="max-h-[480px] overflow-y-auto p-3">
          {report.format === "markdown" ? (
            <ResearchMarkdown markdown={report.body} />
          ) : (
            <pre className="whitespace-pre-wrap text-[length:var(--fs-table)] text-[var(--fg-dim)]">
              {report.body}
            </pre>
          )}
        </div>
      </Frame>
    </div>
  );
}

function computeStats(hist: PortfolioHistory | null, spyRet: number | null) {
  if (!hist || hist.points.length < 2) return null;
  const pts = hist.points;
  const base = pts[0].equity;
  const last = pts[pts.length - 1].equity;
  const totalPnl = last - base;
  const totalRet = base > 0 ? (last / base - 1) * 100 : 0;
  let peak = -Infinity;
  let maxDd = 0;
  for (const p of pts) {
    peak = Math.max(peak, p.equity);
    if (peak > 0) maxDd = Math.min(maxDd, (p.equity / peak - 1) * 100);
  }
  const alpha = spyRet != null ? totalRet - spyRet : null;
  return { totalPnl, totalRet, maxDd, alpha };
}

function BookActor({ id, family }: { id: string; family: ActorFamily }) {
  const [label, setLabel] = useState<string | null | undefined>(undefined);
  const [dayPnl, setDayPnl] = useState<{ v: number; active: boolean } | null>(
    null,
  );
  const [hist, setHist] = useState<PortfolioHistory | null>(null);
  const [spyRet, setSpyRet] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [accounts, dp, h] = await Promise.all([
          fetchLabAccounts(),
          fetchLabDayPnl(id).catch(() => ({ day_pnl: 0, has_activity: false })),
          fetchPortfolioHistory("ALL", id).catch(() => null),
        ]);
        if (cancelled) return;
        setLabel(accounts.find((a) => a.id === id)?.label ?? null);
        setDayPnl({ v: dp.day_pnl, active: dp.has_activity });
        setHist(h);
        // alpha vs SPY over the same (ALL) window — one extra daily-bars fetch.
        if (h && h.points.length >= 2) {
          const first = h.points[0].timestamp;
          const firstMs = first < 2e10 ? first * 1000 : first;
          const last = h.points[h.points.length - 1].timestamp;
          const lastMs = last < 2e10 ? last * 1000 : last;
          const days = Math.min(
            3650,
            Math.max(2, Math.ceil((lastMs - firstMs) / 86_400_000) + 3),
          );
          fetchBars("SPY", { timeframe: "1Day", days })
            .then((res) => {
              if (cancelled) return;
              const win = res.bars.filter(
                (b) => new Date(b.time).getTime() >= firstMs,
              );
              const b0 = win[0]?.close;
              const bN = win[win.length - 1]?.close;
              setSpyRet(b0 && bN && b0 > 0 ? (bN / b0 - 1) * 100 : null);
            })
            .catch(() => setSpyRet(null));
        }
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "failed to load");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const stats = useMemo(() => computeStats(hist, spyRet), [hist, spyRet]);
  const wiring = BOOK_WIRING[id];

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (label === undefined) return <LoadingNote label="loading book…" />;
  if (label === null) return <EmptyNote>unknown book: {id}</EmptyNote>;

  return (
    <div className="space-y-5">
      <Header
        title={label}
        tags={
          <>
            <Tag kind={family}>{family}</Tag>
            <Tag kind="live">paper</Tag>
          </>
        }
        facts={[
          ["account", id],
          ["day p&l", dayPnl?.active ? money(dayPnl.v, { sign: true }) : "—"],
        ]}
      />

      <StatStrip>
        <StatBox
          k="total p&l"
          v={stats ? money(stats.totalPnl, { sign: true }) : "—"}
          s="this window"
          color={stats ? tone(stats.totalPnl) : undefined}
        />
        <StatBox
          k="return"
          v={stats ? pct(stats.totalRet, { sign: true }) : "—"}
          color={stats ? tone(stats.totalRet) : undefined}
        />
        <StatBox
          k="day p&l"
          v={dayPnl?.active ? money(dayPnl.v, { sign: true }) : "—"}
          color={dayPnl?.active ? tone(dayPnl.v) : undefined}
        />
        <StatBox
          k="max drawdown"
          v={stats ? pct(stats.maxDd) : "—"}
          s="peak-to-trough"
          color={stats && stats.maxDd < 0 ? "var(--neg)" : undefined}
        />
        <StatBox
          k="α vs spy"
          v={
            stats?.alpha != null ? (
              `${pct(stats.alpha, { sign: true })}`
            ) : (
              <span className="text-[var(--fg-faint)]">—</span>
            )
          }
          s="this window"
          color={stats?.alpha != null ? tone(stats.alpha) : undefined}
        />
      </StatStrip>

      <EquityChart
        accountId={id}
        label={label.toLowerCase()}
        color={family === "human" ? "var(--c-violet)" : "var(--c-accent)"}
      />

      <div>
        <SecHead label="open positions" />
        <EmptyNote>
          per-account open positions aren&apos;t exposed via an endpoint yet —
          the equity curve + P&amp;L above are live for this book.
        </EmptyNote>
      </div>

      {wiring && wiring.lanes.length > 0 && (
        <div>
          <SecHead
            label="idea ledger"
            note="every candidate graded, advanced or not"
          />
          <div className="mt-2 space-y-4">
            {wiring.lanes.map((lane) => (
              <LaneLedger key={lane.origin} {...lane} />
            ))}
          </div>
        </div>
      )}

      {wiring && (
        <div>
          <SecHead
            label="nightly report"
            note={
              wiring.lanes.length === 0
                ? "advisory — the founder trades this book"
                : "the analyst's filed cycle"
            }
          />
          <NightlyReport kind={wiring.reportKind} />
        </div>
      )}
    </div>
  );
}

// ── shared detail header ────────────────────────────────────────────────────
function Header({
  title,
  tags,
  facts,
}: {
  title: string;
  tags: ReactNode;
  facts: [string, string][];
}) {
  return (
    <div className="flex flex-wrap items-start gap-4 border-b border-[var(--border-strong)] pb-3">
      <div>
        <h1 className="flex items-center gap-2 text-[length:var(--fs-hero)] font-semibold text-foreground">
          {title}
          <span className="flex items-center gap-2 text-[length:var(--fs-body)]">
            {tags}
          </span>
        </h1>
      </div>
      <div className="ml-auto space-y-0.5 text-right text-[length:var(--fs-table)] text-[var(--fg-faint)]">
        {facts.map(([k, v]) => (
          <div key={k}>
            {k} · <span className="text-[var(--fg-dim)]">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
