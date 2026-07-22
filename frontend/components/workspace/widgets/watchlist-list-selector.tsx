"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, ChevronDown, Plus, Trash2 } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  createWatchlist,
  deleteWatchlist,
  fetchWatchlists,
  type Watchlist,
  type WatchlistsState,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  watchlistId?: string;
  onSelect: (watchlistId: string) => void;
};

// "27/30" for slot-capped lists (pins ride above the cap, so they don't count).
function slotsLabel(list: Watchlist): string | null {
  if (!list.max_slots) return null;
  const pinned = new Set(list.pinned ?? []);
  const used = list.symbols.filter((s) => !pinned.has(s)).length;
  return `${used}/${list.max_slots}`;
}

// The watchlist widget's title IS the switcher: the list's name renders in the
// title slot of the grid header with a chevron, and clicking it drops the
// list menu (founder 07-22 — replaced the separate hamburger menu on the
// right side of the header).
export function WatchlistListSelector({ watchlistId, onSelect }: Props) {
  const [state, setState] = useState<WatchlistsState | null>(null);
  const [open, setOpen] = useState(false);

  // Refetch on every menu open (cheap, local API) so symbol counts and lists
  // added elsewhere are current; the mount fetch covers the trigger label.
  useEffect(() => {
    let cancelled = false;
    fetchWatchlists()
      .then((data) => {
        if (!cancelled) setState(data);
      })
      .catch(() => {
        if (!cancelled) setState(null);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const activeList = useMemo(() => {
    if (!state) return null;
    const id = watchlistId ?? state.active_id;
    return state.lists.find((list) => list.id === id) ?? state.lists[0] ?? null;
  }, [state, watchlistId]);

  if (!state || !activeList) return null;

  const activeSlots = slotsLabel(activeList);

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        type="button"
        aria-label={`Watchlist: ${activeList.name} — switch list`}
        className="workspace-no-drag inline-flex min-w-0 items-center gap-1 rounded px-0.5 outline-none hover:bg-[var(--bg-hover)]"
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <span
          className="min-w-0 truncate pb-[0.3em] -mb-[0.3em] text-[length:var(--fs-widget-title)] font-semibold text-[var(--fg-widget-title)] [text-box:trim-both_cap_alphabetic]"
          style={{ fontFamily: "var(--font-display)" }}
        >
          {activeList.name}
        </span>
        {activeSlots ? (
          <span className="shrink-0 font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)]">
            {activeSlots}
          </span>
        ) : null}
        <ChevronDown className="size-3 shrink-0 text-[var(--fg-faint)]" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="min-w-40 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[length:var(--fs-body)] shadow-[var(--panel-shadow)]"
      >
        {state.lists.map((list) => (
          <DropdownMenuItem
            key={list.id}
            onClick={() => onSelect(list.id)}
            className={cn(
              "flex items-center justify-between gap-3 px-2 py-1.5",
              list.id === activeList.id
                ? "text-foreground"
                : "text-[var(--fg-dim)]",
            )}
          >
            <span className="min-w-0 truncate">{list.name}</span>
            <span className="flex shrink-0 items-center gap-1.5 text-[length:var(--fs-table)] text-[var(--fg-faint)]">
              {slotsLabel(list) ?? list.symbols.length}
              {list.id === activeList.id ? <Check className="size-3" /> : null}
            </span>
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={async () => {
            const name = window.prompt("New watchlist name");
            if (!name?.trim()) return;
            try {
              const prevIds = new Set(state.lists.map((l) => l.id));
              const next = await createWatchlist(name.trim());
              setState(next);
              const created = next.lists.find((l) => !prevIds.has(l.id));
              if (created) onSelect(created.id);
            } catch {
              // fail-soft: list state unchanged
            }
          }}
          className="flex items-center gap-2 px-2 py-1.5 text-[var(--fg-dim)]"
        >
          <Plus className="size-3" />
          New list…
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={state.lists.length <= 1}
          onClick={async () => {
            const count = activeList.symbols.length;
            const ok = window.confirm(
              `Delete watchlist "${activeList.name}" (${count} symbol${count === 1 ? "" : "s"})? This cannot be undone.`,
            );
            if (!ok) return;
            try {
              const next = await deleteWatchlist(activeList.id);
              setState(next);
              onSelect(next.active_id);
            } catch {
              // fail-soft
            }
          }}
          className="flex items-center gap-2 px-2 py-1.5 text-[var(--c-red)] data-[disabled]:opacity-40"
        >
          <Trash2 className="size-3" />
          Delete {"\""}{activeList.name}{"\""}…
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
