"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ChevronDown, RefreshCw } from "lucide-react";

import { PagesMenu } from "@/components/layout/pages-menu";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  addBoardInboxCard,
  fetchBoard,
  type BoardCard,
  type BoardData,
} from "@/lib/api";
import { relativeTime } from "@/lib/relative-time";
import { cn } from "@/lib/utils";

function typeChipClass(type: BoardCard["type"]): string {
  switch (type) {
    case "idea":
      return "border-[color-mix(in_srgb,var(--c-blue)_35%,transparent)] bg-[color-mix(in_srgb,var(--c-blue)_12%,transparent)] text-[var(--c-blue)]";
    case "safety":
      return "border-[color-mix(in_srgb,var(--c-red)_35%,transparent)] bg-[color-mix(in_srgb,var(--c-red)_12%,transparent)] text-[var(--c-red)]";
    case "bug":
      return "border-[color-mix(in_srgb,var(--c-amber)_35%,transparent)] bg-[color-mix(in_srgb,var(--c-amber)_12%,transparent)] text-[var(--c-amber)]";
    case "feature":
      return "border-[color-mix(in_srgb,var(--c-accent)_35%,transparent)] bg-[color-mix(in_srgb,var(--c-accent)_12%,transparent)] text-[var(--c-accent)]";
    case "research":
      return "border-[color-mix(in_srgb,var(--c-violet)_35%,transparent)] bg-[color-mix(in_srgb,var(--c-violet)_12%,transparent)] text-[var(--c-violet)]";
    case "ops":
    default:
      return "border-border/60 bg-muted/30 text-muted-foreground";
  }
}

function priorityChipClass(priority: BoardCard["priority"]): string {
  switch (priority) {
    case "high":
      return "border-transparent bg-foreground/10 font-semibold text-foreground";
    case "low":
      return "border-border/40 text-muted-foreground/60";
    case "normal":
    default:
      return "border-border text-muted-foreground";
  }
}

function formatTimestamp(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return iso;
  return new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

const DEFAULT_LANES = ["inbox", "founder", "in_progress", "next", "backlog", "done"] as const;

const LANE_HEADINGS: Record<
  string,
  { title: string; founder?: boolean; inbox?: boolean; done?: boolean }
> = {
  inbox: { title: "Founder inbox", inbox: true },
  founder: { title: "⚠ Waiting on you", founder: true },
  in_progress: { title: "In progress" },
  next: { title: "Up next" },
  backlog: { title: "Backlog" },
  done: { title: "Recently done", done: true },
};

const PRIORITY_ORDER: Record<string, number> = {
  high: 0,
  normal: 1,
  low: 2,
};

const DONE_LANE_CAP = 10;
const AUTO_REFRESH_MS = 60_000;

function localTodayIso(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function sortCards(cards: BoardCard[]): BoardCard[] {
  return [...cards].sort((a, b) => {
    const pa = PRIORITY_ORDER[a.priority] ?? 1;
    const pb = PRIORITY_ORDER[b.priority] ?? 1;
    if (pa !== pb) return pa - pb;
    return Date.parse(b.updated_at) - Date.parse(a.updated_at);
  });
}

function laneTitle(laneId: string): string {
  return LANE_HEADINGS[laneId]?.title ?? laneId;
}

function ClampedNotes({ text }: { text: string }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [clamped, setClamped] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setClamped(el.scrollHeight > el.clientHeight + 1);
  }, [text]);

  return (
    <div className="mt-1">
      <p
        ref={ref}
        className="line-clamp-3 text-[11px] leading-snug text-muted-foreground"
      >
        {text}
      </p>
      {clamped ? (
        <span className="mt-0.5 inline-block text-[10px] text-muted-foreground/55">
          more
        </span>
      ) : null}
    </div>
  );
}

function BoardCardView({
  card,
  muted,
  showNew,
  expanded,
  laneLabel,
  onToggle,
}: {
  card: BoardCard;
  muted?: boolean;
  showNew?: boolean;
  expanded: boolean;
  laneLabel: string;
  onToggle: (cardId: string) => void;
}) {
  const isNew = showNew && card.updated_at.slice(0, 10) === localTodayIso();

  return (
    <button
      type="button"
      onClick={() => onToggle(card.id)}
      className={cn(
        "w-full rounded-md border border-border/80 bg-card/60 p-3 text-left shadow-sm transition-colors hover:border-border hover:bg-card/90",
        muted && "opacity-70",
        expanded && "border-border bg-card/90",
      )}
    >
      <div className="flex items-start gap-2">
        <h3 className="min-w-0 flex-1 text-[13px] font-medium leading-snug text-foreground">
          {card.title}
        </h3>
        {isNew ? (
          <span className="shrink-0 rounded-full border border-[color-mix(in_srgb,var(--c-blue)_40%,transparent)] bg-[color-mix(in_srgb,var(--c-blue)_14%,transparent)] px-1.5 py-px font-mono text-[8px] uppercase tracking-wide text-[var(--c-blue)]">
            new
          </span>
        ) : null}
        <ChevronDown
          className={cn(
            "mt-0.5 size-3.5 shrink-0 text-muted-foreground/45 transition-transform duration-200",
            expanded && "rotate-180",
          )}
          aria-hidden
        />
      </div>
      {card.notes ? (
        expanded ? (
          <div className="mt-1 whitespace-pre-wrap text-[11px] leading-snug text-muted-foreground">
            {card.notes}
          </div>
        ) : (
          <ClampedNotes text={card.notes} />
        )
      ) : expanded ? (
        <p className="mt-1 text-[11px] text-muted-foreground/70">No notes.</p>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-1">
        <span
          className={cn(
            "inline-flex rounded-full border px-1.5 py-px font-mono text-[9px] uppercase tracking-wide",
            typeChipClass(card.type),
          )}
        >
          {card.type}
        </span>
        <span
          className={cn(
            "inline-flex rounded-full border px-1.5 py-px font-mono text-[9px] uppercase tracking-wide",
            priorityChipClass(card.priority),
          )}
        >
          {card.priority}
        </span>
        {card.owner ? (
          <span className="inline-flex rounded-full border border-border/50 px-1.5 py-px font-mono text-[9px] text-muted-foreground">
            {card.owner}
          </span>
        ) : null}
      </div>
      {expanded ? (
        <dl className="mt-3 space-y-1 border-t border-border/60 pt-2 text-[10px]">
          <div className="flex justify-between gap-4">
            <dt className="text-[var(--fg-faint)]">Lane</dt>
            <dd className="text-right text-[var(--fg-dim)]">{laneLabel}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-[var(--fg-faint)]">Updated</dt>
            <dd className="text-right text-[var(--fg-dim)]">
              {formatTimestamp(card.updated_at)}
            </dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-[var(--fg-faint)]">ID</dt>
            <dd className="truncate text-right font-mono text-[9px] text-[var(--fg-dim)]">
              {card.id}
            </dd>
          </div>
        </dl>
      ) : null}
    </button>
  );
}

function FounderInboxQuickAdd({
  onAdded,
}: {
  onAdded: (card: BoardCard) => void;
}) {
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [notesOpen, setNotesOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(async () => {
    const trimmedTitle = title.trim();
    if (!trimmedTitle || submitting) return;

    setSubmitting(true);
    setError(null);
    try {
      const { card } = await addBoardInboxCard(trimmedTitle, notes.trim());
      onAdded(card);
      setTitle("");
      setNotes("");
      setNotesOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add card");
    } finally {
      setSubmitting(false);
    }
  }, [notes, onAdded, submitting, title]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submit();
  };

  return (
    <form onSubmit={handleSubmit} className="mb-3 space-y-2">
      <div className="flex w-full flex-col gap-2 sm:flex-row">
        <Input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Drop an idea, bug, anything…"
          disabled={submitting}
          className="h-11 w-full min-w-0 text-base sm:text-sm"
          enterKeyHint="send"
        />
        <Button
          type="submit"
          disabled={submitting || !title.trim()}
          className="h-11 w-full shrink-0 px-5 sm:w-auto"
        >
          {submitting ? "Adding…" : "Add"}
        </Button>
      </div>
      {notesOpen ? (
        <Textarea
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          placeholder="Optional notes…"
          disabled={submitting}
          rows={2}
          className="min-h-[4.5rem] resize-y text-sm"
        />
      ) : (
        <button
          type="button"
          onClick={() => setNotesOpen(true)}
          disabled={submitting}
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
        >
          <ChevronDown className="size-3" />
          Add notes
        </button>
      )}
      {error ? (
        <p className="rounded-md border border-[color-mix(in_srgb,var(--c-red)_35%,transparent)] bg-[color-mix(in_srgb,var(--c-red)_8%,transparent)] px-2 py-1.5 text-[11px] text-[var(--c-red)]">
          {error}
        </p>
      ) : null}
    </form>
  );
}

function BoardLane({
  laneId,
  cards,
  expandedCardId,
  onInboxAdded,
  onToggleCard,
}: {
  laneId: string;
  cards: BoardCard[];
  expandedCardId: string | null;
  onInboxAdded?: (card: BoardCard) => void;
  onToggleCard: (cardId: string) => void;
}) {
  const config = LANE_HEADINGS[laneId] ?? { title: laneId };
  const displayCards = config.done
    ? sortCards(cards).slice(0, DONE_LANE_CAP)
    : sortCards(cards);

  return (
    <section
      className={cn(
        "flex w-full min-w-0 shrink-0 flex-col md:min-w-[240px] md:max-w-[340px] md:flex-1",
        config.inbox &&
          "rounded-lg border border-dashed border-[color-mix(in_srgb,var(--c-blue)_35%,transparent)] bg-[color-mix(in_srgb,var(--c-blue)_4%,transparent)]",
        config.founder &&
          "rounded-lg border border-[color-mix(in_srgb,var(--c-amber)_45%,transparent)] bg-[color-mix(in_srgb,var(--c-amber)_6%,transparent)]",
      )}
    >
      <header
        className={cn(
          "mb-3 px-1",
          config.inbox &&
            "rounded-t-md border-b border-dashed border-[color-mix(in_srgb,var(--c-blue)_30%,transparent)] bg-[color-mix(in_srgb,var(--c-blue)_8%,transparent)] px-3 py-2",
          config.founder &&
            "rounded-t-md border-b border-[color-mix(in_srgb,var(--c-amber)_35%,transparent)] bg-[color-mix(in_srgb,var(--c-amber)_10%,transparent)] px-3 py-2",
          config.done && "opacity-80",
        )}
      >
        <h2
          className={cn(
            "text-[12px] font-medium tracking-tight",
            config.inbox && "text-[var(--c-blue)]",
            config.founder && "text-[var(--c-amber)]",
            !config.inbox && !config.founder && "text-foreground",
            config.done && "text-muted-foreground",
          )}
        >
          {config.title}
        </h2>
        <p className="text-[10px] text-muted-foreground">{displayCards.length} items</p>
      </header>
      <div
        className={cn(
          "flex flex-col gap-2 px-1",
          (config.inbox || config.founder) && "px-3 pb-3",
        )}
      >
        {config.inbox && onInboxAdded ? (
          <FounderInboxQuickAdd onAdded={onInboxAdded} />
        ) : null}
        {displayCards.length === 0 ? (
          <p className="rounded-md border border-dashed border-border/60 px-3 py-6 text-center text-[11px] text-muted-foreground/70">
            nothing here
          </p>
        ) : (
          displayCards.map((card) => (
            <BoardCardView
              key={card.id}
              card={card}
              muted={config.done}
              showNew={config.inbox}
              expanded={expandedCardId === card.id}
              laneLabel={laneTitle(laneId)}
              onToggle={onToggleCard}
            />
          ))
        )}
      </div>
    </section>
  );
}

export function BoardPage() {
  const [board, setBoard] = useState<BoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedCardId, setExpandedCardId] = useState<string | null>(null);

  const handleToggleCard = useCallback((cardId: string) => {
    setExpandedCardId((prev) => (prev === cardId ? null : cardId));
  }, []);

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const data = await fetchBoard();
      setBoard(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load board");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === "visible") {
        void load(true);
      }
    };
    const id = window.setInterval(tick, AUTO_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const handleInboxAdded = useCallback(
    (card: BoardCard) => {
      setBoard((prev) => {
        if (!prev) return prev;
        const hasCard = prev.cards.some((existing) => existing.id === card.id);
        return {
          ...prev,
          cards: hasCard ? prev.cards : [...prev.cards, card],
          lanes: prev.lanes.includes("inbox") ? prev.lanes : ["inbox", ...prev.lanes],
        };
      });
      void load(true);
    },
    [load],
  );

  const lanes = useMemo(() => {
    const fromBoard = board?.lanes?.length ? board.lanes : [...DEFAULT_LANES];
    const rest = fromBoard.filter(
      (lane) => lane !== "inbox" && (LANE_HEADINGS[lane] || lane),
    );
    return ["inbox", ...rest];
  }, [board?.lanes]);

  const cardsByLane = useMemo(() => {
    const grouped = new Map<string, BoardCard[]>();
    for (const lane of lanes) grouped.set(lane, []);
    for (const card of board?.cards ?? []) {
      const bucket = grouped.get(card.lane);
      if (bucket) bucket.push(card);
    }
    return grouped;
  }, [board?.cards, lanes]);

  const updatedLabel =
    board?.updated_at != null ? relativeTime(board.updated_at) : "—";

  return (
    <div className="flex h-svh min-h-0 flex-col bg-background">
      <header className="flex h-[52px] shrink-0 items-center justify-between gap-3 border-b border-[var(--border)] bg-[var(--bg-raised)] px-3.5 sm:px-4">
        <div className="flex min-w-0 items-center gap-2 sm:gap-3">
          <PagesMenu />
          <div className="min-w-0">
            <h1 className="truncate font-mono text-[13px] font-semibold tracking-tight text-foreground">
              Project board
            </h1>
            <p className="truncate text-[10px] text-[var(--fg-dim)]">
              updated {updatedLabel}
              {loading && !board ? " · loading…" : null}
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={refreshing}
          onClick={() => void load(true)}
          className="shrink-0 gap-1.5"
        >
          <RefreshCw className={cn("size-3.5", refreshing && "animate-spin")} />
          <span className="hidden sm:inline">Refresh</span>
        </Button>
      </header>

      <main className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto w-full max-w-[1920px] px-4 py-5 sm:px-6 sm:py-6">
          {error ? (
            <p className="mb-4 rounded-lg border border-[color-mix(in_srgb,var(--c-red)_35%,transparent)] bg-[color-mix(in_srgb,var(--c-red)_8%,transparent)] px-4 py-3 text-sm text-[var(--c-red)]">
              {error}
            </p>
          ) : null}

          <div className="flex flex-col gap-6 md:flex-row md:items-start md:justify-center md:gap-4 md:overflow-x-auto md:pb-2">
            {lanes.map((lane) => (
              <BoardLane
                key={lane}
                laneId={lane}
                cards={cardsByLane.get(lane) ?? []}
                expandedCardId={expandedCardId}
                onInboxAdded={lane === "inbox" ? handleInboxAdded : undefined}
                onToggleCard={handleToggleCard}
              />
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}
