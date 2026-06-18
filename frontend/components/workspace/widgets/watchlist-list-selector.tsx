"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { fetchWatchlists, type WatchlistsState } from "@/lib/api";

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
        className="workspace-no-drag inline-flex items-center gap-0.5 rounded px-1 py-0.5 font-mono text-[10px] text-[var(--fg-faint)] outline-none hover:bg-[var(--bg-hover)] hover:text-foreground"
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
      >
        {activeList.name}
        <ChevronDown className="size-2.5" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="min-w-32 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[11px] shadow-[var(--panel-shadow)]"
      >
        {state.lists.map((list) => (
          <DropdownMenuItem
            key={list.id}
            onSelect={() => onSelect(list.id)}
            className="px-2 py-1.5"
          >
            {list.name}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
