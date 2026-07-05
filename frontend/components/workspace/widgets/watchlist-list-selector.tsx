"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Menu as MenuIcon } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { fetchWatchlists, type WatchlistsState } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  watchlistId?: string;
  onSelect: (watchlistId: string) => void;
};

export function WatchlistListSelector({ watchlistId, onSelect }: Props) {
  const [state, setState] = useState<WatchlistsState | null>(null);

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
  }, []);

  const activeList = useMemo(() => {
    if (!state) return null;
    const id = watchlistId ?? state.active_id;
    return state.lists.find((list) => list.id === id) ?? state.lists[0] ?? null;
  }, [state, watchlistId]);

  if (!state || !activeList) return null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        type="button"
        aria-label="Watchlist options"
        className="workspace-no-drag inline-flex items-center rounded p-1 text-[var(--fg-dim)] outline-none hover:bg-[var(--bg-hover)] hover:text-foreground"
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <MenuIcon className="size-3.5" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="min-w-32 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[length:var(--fs-body)] shadow-[var(--panel-shadow)]"
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
            {list.name}
            {list.id === activeList.id ? <Check className="size-3" /> : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
