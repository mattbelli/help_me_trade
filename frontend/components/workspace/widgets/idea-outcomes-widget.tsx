"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  getIdeaOutcomes,
  gradeIdeaOutcome,
  type IdeaGradeInput,
  type IdeaOutcomeRow,
  type IdeaOutcomesResponse,
} from "@/lib/api";
import { usePagePoll } from "@/lib/workspace/use-page-poll";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;

const STATUS_CHIP_ORDER = [
  "pending",
  "triggered",
  "won",
  "lost",
  "expired",
  "invalidated",
  "data_gap",
] as const;

// The founder grades JUDGMENT only on fully-resolved ideas (mirrors the backend
// GRADEABLE_STATUSES). invalidated/data_gap are resolved but not gradeable.
const GRADEABLE_STATUSES = new Set(["won", "lost", "expired"]);

// The 5b rubric — four 1–5 scores. Keys match the founder_verdict JSON shape.
const RUBRIC_FIELDS: { key: keyof IdeaGradeInput; short: string; label: string }[] = [
  { key: "take_it", short: "T", label: "Would you have taken it?" },
  { key: "entry_honest", short: "E", label: "Entry zone honest?" },
  { key: "stop_survivable", short: "S", label: "Stop survivable?" },
  { key: "thesis_quality", short: "Q", label: "Thesis played out?" },
];

const SCORE_KEYS = ["take_it", "entry_honest", "stop_survivable", "thesis_quality"] as const;

type TabId = "open" | "resolved";

const TABS: { id: TabId; label: string }[] = [
  { id: "open", label: "Open" },
  { id: "resolved", label: "Resolved" },
];

const TH =
  "border-b border-[var(--line-divider)] px-2 py-1.5 text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-faint)] uppercase";
const TD = "border-b border-[var(--line-divider)] px-2 py-1.5";
const ROW = "transition-colors hover:bg-[var(--bg-hover)]";

function statusClass(status: string): string {
  switch (status) {
    case "won":
      return "text-[var(--c-green)]";
    case "lost":
      return "text-[var(--c-red)]";
    case "triggered":
      return "text-[var(--c-accent)]";
    case "pending":
      return "text-[var(--fg-dim)]";
    case "expired":
    case "invalidated":
    case "data_gap":
      return "text-[var(--fg-faint)]";
    default:
      return "text-[var(--fg-dim)]";
  }
}

function formatDateOnly(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatWinRate(winRate: number | null): string {
  if (winRate == null) return "—";
  return `${(winRate * 100).toFixed(1)}%`;
}

function formatOriginLabel(origin: string): string {
  if (origin === "llm_conviction") return "llm";
  if (origin === "structure") return "struct";
  return origin.slice(0, 6);
}

function formatOriginWinRateSummary(
  byOrigin: NonNullable<IdeaOutcomesResponse["summary"]["by_origin"]>,
): string {
  const entries = Object.entries(byOrigin);
  if (entries.length < 2) return "";
  return entries
    .map(([origin, stats]) => {
      const label = formatOriginLabel(origin);
      const winLoss = (stats.counts.won ?? 0) + (stats.counts.lost ?? 0);
      const total = Object.values(stats.counts).reduce((sum, n) => sum + n, 0);
      const count = winLoss > 0 ? winLoss : total;
      return `${label} ${formatWinRate(stats.win_rate)} (${count})`;
    })
    .join(" · ");
}

function OriginChip({ origin }: { origin: string }) {
  if (origin !== "llm_conviction") return null;
  return (
    <span
      className="ml-1 rounded px-1 py-px font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--c-violet)] uppercase"
      title="LLM-originated conviction idea (A/B tracked)"
    >
      LLM
    </span>
  );
}

function formatMfeMae(mfe: number | null, mae: number | null): string {
  const parts: string[] = [];
  if (mfe != null) {
    parts.push(`${mfe >= 0 ? "+" : "−"}${Math.abs(mfe).toFixed(1)}%`);
  }
  if (mae != null) {
    parts.push(`${mae >= 0 ? "+" : "−"}${Math.abs(mae).toFixed(1)}%`);
  }
  return parts.length > 0 ? parts.join(" / ") : "—";
}

function formatEntryZone(row: IdeaOutcomeRow): string {
  return `${row.entry_low}–${row.entry_high}`;
}

const MONTHS_SHORT = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

// Date-only strings (YYYY-MM-DD) must not go through new Date() — UTC-midnight
// parsing shifts them a day back in ET.
function formatDayString(value: string | null | undefined): string {
  if (!value) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!m) return value;
  return `${MONTHS_SHORT[Number(m[2]) - 1]} ${Number(m[3])}`;
}

function LastVsZoneCell({ row }: { row: IdeaOutcomeRow }) {
  if (!row.last_price) {
    return <span className="text-[var(--fg-faint)]">—</span>;
  }
  const d = row.zone_distance_pct;
  const title = `latest daily close${row.last_price_at ? ` (${row.last_price_at})` : ""} · % from entry zone (+ above / − below)`;
  if (d == null) {
    return <span title={title}>{row.last_price}</span>;
  }
  if (d === 0) {
    return (
      <span title={title}>
        {row.last_price} <span className="text-[var(--c-accent)]">at zone</span>
      </span>
    );
  }
  return (
    <span title={title}>
      {row.last_price}{" "}
      <span className="text-[var(--fg-faint)]">
        {d > 0 ? "+" : "−"}
        {Math.abs(d).toFixed(1)}%
      </span>
    </span>
  );
}

function StatusCell({ row }: { row: IdeaOutcomeRow }) {
  const label = row.status.replace(/_/g, " ");
  return (
    <td
      className={cn(TD, "text-[length:var(--fs-body)] uppercase", statusClass(row.status))}
      title={row.notes?.trim() ? row.notes : undefined}
    >
      {label}
    </td>
  );
}

function OpenTable({
  rows,
  linked,
  publish,
}: {
  rows: IdeaOutcomeRow[];
  linked: boolean;
  publish: (symbol: string) => void;
}) {
  if (rows.length === 0) {
    return <WidgetEmpty message="No open ideas" />;
  }

  return (
    <div className="px-1.5 py-1">
      <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
        <thead>
          <tr>
            <th className={cn(TH, "text-left")}>Symbol</th>
            <th className={cn(TH, "text-left")}>Status</th>
            <th className={cn(TH, "text-left")}>Entry</th>
            <th className={cn(TH, "text-right")}>Stop</th>
            <th className={cn(TH, "text-right")}>Target</th>
            <th className={cn(TH, "text-right")}>Last</th>
            <th className={cn(TH, "text-right")}>MFE / MAE</th>
            <th className={cn(TH, "text-left")}>As of</th>
            <th className={cn(TH, "text-left")}>Expires</th>
          </tr>
        </thead>
        <tbody className="leading-[20px]">
          {rows.map((row) => (
            <tr key={row.id} className={ROW}>
              <td className={cn(TD, "font-medium text-foreground")}>
                <ClickableSymbol
                  symbol={row.symbol}
                  linked={linked}
                  onActivate={() => publish(row.symbol)}
                />
                <OriginChip origin={row.origin} />
              </td>
              <StatusCell row={row} />
              <td className={cn(TD, "tabular-nums text-[var(--fg-dim)]")}>
                {formatEntryZone(row)}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {row.stop}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {row.targets[0] ?? "—"}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                <LastVsZoneCell row={row} />
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {formatMfeMae(row.mfe_pct, row.mae_pct)}
              </td>
              <td className={cn(TD, "text-[var(--fg-faint)]")}>
                {formatDateOnly(row.as_of)}
              </td>
              <td
                className={cn(TD, "text-[var(--fg-faint)]")}
                title="idea TTL: 10 trading days to trigger, 20 after trigger to resolve"
              >
                {formatDayString(row.expires_on)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GradeDisplay({ row }: { row: IdeaOutcomeRow }) {
  const v = row.verdict;
  if (!v) return null;
  const numbersWrong = v.entry_honest === 1 || v.stop_survivable === 1;
  return (
    <span
      className={cn(
        "tabular-nums",
        numbersWrong ? "text-[var(--c-red)]" : "text-[var(--fg-dim)]",
      )}
      title={
        (v.note ? `${v.note} · ` : "") +
        "take-it / entry-honest / stop-survivable / thesis"
      }
    >
      {RUBRIC_FIELDS.map((f) => `${f.short}${v[f.key as keyof typeof v] ?? "—"}`).join(" ")}
    </span>
  );
}

function ScoreRow({
  field,
  value,
  onPick,
}: {
  field: (typeof RUBRIC_FIELDS)[number];
  value: number | null;
  onPick: (n: number) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-[13ch] shrink-0 text-[var(--fg-dim)]">{field.label}</span>
      <div className="flex gap-0.5">
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            type="button"
            onClick={() => onPick(n)}
            className={cn(
              "h-5 w-5 rounded border text-[length:var(--fs-table)] transition-colors",
              value === n
                ? "border-[var(--c-accent)] bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
                : "border-[var(--border)] text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
            )}
          >
            {n}
          </button>
        ))}
      </div>
    </div>
  );
}

function GradeForm({
  row,
  onSubmit,
  onCancel,
}: {
  row: IdeaOutcomeRow;
  onSubmit: (grade: IdeaGradeInput) => Promise<void>;
  onCancel: () => void;
}) {
  const [scores, setScores] = useState<Record<string, number | null>>(() => {
    const v = row.verdict;
    return {
      take_it: v?.take_it ?? null,
      entry_honest: v?.entry_honest ?? null,
      stop_survivable: v?.stop_survivable ?? null,
      thesis_quality: v?.thesis_quality ?? null,
    };
  });
  const [note, setNote] = useState(row.verdict?.note ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const complete = SCORE_KEYS.every((k) => scores[k] != null);

  const save = async () => {
    if (!complete || saving) return;
    setSaving(true);
    setErr(null);
    try {
      await onSubmit({
        take_it: scores.take_it!,
        entry_honest: scores.entry_honest!,
        stop_survivable: scores.stop_survivable!,
        thesis_quality: scores.thesis_quality!,
        note: note.trim() || undefined,
      });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to save grade");
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-1.5 px-2 py-2">
      {RUBRIC_FIELDS.map((f) => (
        <ScoreRow
          key={f.key}
          field={f}
          value={scores[f.key] ?? null}
          onPick={(n) => setScores((s) => ({ ...s, [f.key]: n }))}
        />
      ))}
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="note (optional)"
          className="w-[26ch] rounded border border-[var(--border)] bg-transparent px-1.5 py-0.5 text-[length:var(--fs-table)] text-[var(--fg-dim)] placeholder:text-[var(--fg-faint)] focus:border-[var(--c-accent)] focus:outline-none"
        />
        <button
          type="button"
          onClick={() => void save()}
          disabled={!complete || saving}
          className={cn(
            "rounded px-2 py-0.5 text-[length:var(--fs-table)] transition-colors",
            complete && !saving
              ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)] hover:brightness-110"
              : "text-[var(--fg-faint)]",
          )}
        >
          {saving ? "saving…" : row.verdict ? "revise" : "save"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded px-2 py-0.5 text-[length:var(--fs-table)] text-[var(--fg-faint)] transition-colors hover:text-[var(--fg-dim)]"
        >
          cancel
        </button>
        {err ? (
          <span className="text-[length:var(--fs-table)] text-[var(--c-red)]">{err}</span>
        ) : null}
      </div>
    </div>
  );
}

function ResolvedTable({
  rows,
  linked,
  publish,
  gradingId,
  setGradingId,
  onGrade,
}: {
  rows: IdeaOutcomeRow[];
  linked: boolean;
  publish: (symbol: string) => void;
  gradingId: number | null;
  setGradingId: (id: number | null) => void;
  onGrade: (id: number, grade: IdeaGradeInput) => Promise<void>;
}) {
  if (rows.length === 0) {
    return <WidgetEmpty message="No resolved ideas" />;
  }

  return (
    <div className="px-1.5 py-1">
      <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
        <thead>
          <tr>
            <th className={cn(TH, "text-left")}>Symbol</th>
            <th className={cn(TH, "text-left")}>Status</th>
            <th className={cn(TH, "text-left")}>Resolved</th>
            <th className={cn(TH, "text-right")}>Price</th>
            <th className={cn(TH, "text-right")}>MFE / MAE</th>
            <th className={cn(TH, "text-left")}>Grade</th>
          </tr>
        </thead>
        <tbody className="leading-[20px]">
          {rows.map((row) => {
            const gradeable = GRADEABLE_STATUSES.has(row.status);
            const open = gradingId === row.id;
            return (
              <Fragment key={row.id}>
                <tr className={ROW}>
                  <td className={cn(TD, "font-medium text-foreground")}>
                    <ClickableSymbol
                      symbol={row.symbol}
                      linked={linked}
                      onActivate={() => publish(row.symbol)}
                    />
                    <OriginChip origin={row.origin} />
                  </td>
                  <StatusCell row={row} />
                  <td className={cn(TD, "text-[var(--fg-dim)]")}>
                    {formatDateOnly(row.resolved_at)}
                  </td>
                  <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                    {row.resolution_price ?? "—"}
                  </td>
                  <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                    {formatMfeMae(row.mfe_pct, row.mae_pct)}
                  </td>
                  <td className={cn(TD, "text-[length:var(--fs-body)]")}>
                    {!gradeable ? (
                      <span className="text-[var(--fg-faint)]">—</span>
                    ) : row.verdict && !open ? (
                      <button
                        type="button"
                        onClick={() => setGradingId(row.id)}
                        className="transition-colors hover:brightness-110"
                        title="Click to revise grade"
                      >
                        <GradeDisplay row={row} />
                      </button>
                    ) : open ? (
                      <span className="text-[var(--c-accent)]">grading…</span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setGradingId(row.id)}
                        className="rounded border border-[var(--border)] px-1.5 py-0.5 text-[length:var(--fs-table)] text-[var(--fg-faint)] transition-colors hover:border-[var(--c-accent)] hover:text-[var(--c-accent)]"
                      >
                        grade
                      </button>
                    )}
                  </td>
                </tr>
                {open ? (
                  <tr>
                    <td
                      colSpan={6}
                      className="border-b border-[var(--line-divider)] bg-[var(--bg-hover)]"
                    >
                      <GradeForm
                        row={row}
                        onSubmit={(grade) => onGrade(row.id, grade)}
                        onCancel={() => setGradingId(null)}
                      />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function IdeaOutcomesWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);

  const [tab, setTab] = useState<TabId>("open");
  const [data, setData] = useState<IdeaOutcomesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [gradingId, setGradingId] = useState<number | null>(null);
  const cancelledRef = useRef(false);

  const load = useCallback(async (isInitial: boolean) => {
    if (isInitial) setLoading(true);
    try {
      const result = await getIdeaOutcomes();
      if (cancelledRef.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to load outcomes");
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, [load]);

  usePagePoll(load, POLL_MS);

  const handleGrade = useCallback(
    async (id: number, grade: IdeaGradeInput) => {
      await gradeIdeaOutcome(id, grade);
      setGradingId(null);
      await load(false);
    },
    [load],
  );

  const stale = error !== null && data !== null;

  const statusChips = useMemo(() => {
    if (!data) return [];
    const counts = data.summary.counts;
    return STATUS_CHIP_ORDER.filter((status) => (counts[status] ?? 0) > 0).map(
      (status) => `${status} ${counts[status]}`,
    );
  }, [data]);

  if (loading && !data) {
    return <WidgetLoading cols={[2, 1, 1, 1]} />;
  }

  if (error && !data) {
    return <WidgetError message={error} onRetry={() => void load(false)} />;
  }

  if (
    data &&
    data.open.length === 0 &&
    data.resolved.length === 0 &&
    data.summary.resolved === 0 &&
    Object.keys(data.summary.counts).length === 0
  ) {
    return <WidgetEmpty message="No tracked ideas yet — run a setup scan." />;
  }

  const summary = data!.summary;
  const originSummary = summary.by_origin
    ? formatOriginWinRateSummary(summary.by_origin)
    : "";
  const grading = summary.grading;

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-[var(--line-divider)] px-3 py-1.5">
        <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
          resolved {summary.resolved} · win rate {formatWinRate(summary.win_rate)}
          {originSummary ? ` · ${originSummary}` : ""}
        </span>
        {statusChips.length > 0 ? (
          <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)]">
            {statusChips.join(" · ")}
          </span>
        ) : null}
        {grading ? (
          <span
            className="font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)]"
            title="5b judging gate: ≥10 graded, mean take-it ≥3, zero numbers-wrong"
          >
            graded {grading.graded}/{grading.gate.min_graded}
            {grading.mean_take_it != null
              ? ` · mean ${grading.mean_take_it.toFixed(1)}`
              : ""}
            {grading.numbers_wrong > 0 ? ` · ${grading.numbers_wrong} wrong` : ""}
            {" · gate: "}
            <span
              className={
                grading.gate_pass
                  ? "text-[var(--c-green)]"
                  : "text-[var(--fg-faint)]"
              }
            >
              {grading.gate_pass ? "PASS" : "not yet"}
            </span>
          </span>
        ) : null}
        {stale ? (
          <span
            className="font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--c-amber)] uppercase"
            title={error ?? undefined}
          >
            stale
          </span>
        ) : null}
      </div>

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
      </div>

      <div className="min-h-0 flex-1 overflow-auto scroll-shadow-y">
        {tab === "open" ? (
          <OpenTable rows={data!.open} linked={linked} publish={publish} />
        ) : (
          <ResolvedTable
            rows={data!.resolved}
            linked={linked}
            publish={publish}
            gradingId={gradingId}
            setGradingId={setGradingId}
            onGrade={handleGrade}
          />
        )}
      </div>
    </div>
  );
}
