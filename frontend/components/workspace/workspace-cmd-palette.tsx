"use client";

import { useTheme } from "next-themes";
import { useAui } from "@assistant-ui/react";
import { useCallback, type KeyboardEvent } from "react";

import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  fetchWatchlists,
  updateWatchlist,
} from "@/lib/api";
import { useConsoleStore } from "@/lib/workspace/use-console-store";
import {
  WORKSPACE_THEME_LABELS,
  WORKSPACE_THEMES,
  type WorkspaceTheme,
} from "@/lib/workspace/themes";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";

import { useWorkspaceToast } from "./workspace-toast";

const WATCHLIST_ADD_RE = /^\/watchlist\s+add\s+([A-Za-z.\-]+)$/i;
const WATCHLIST_REMOVE_RE = /^\/watchlist\s+remove\s+([A-Za-z.\-]+)$/i;

export function WorkspaceCmdPalette() {
  const { showToast } = useWorkspaceToast();
  const { theme, setTheme } = useTheme();
  const { setPaletteOpen, setOpen } = useConsoleStore();
  const openWidgetPicker = useWorkspaceStore((s) => s.openPicker);
  const aui = useAui();

  const closePalette = useCallback(() => {
    setPaletteOpen(false);
    setOpen(true);
  }, [setOpen, setPaletteOpen]);

  const watchlistAdd = useCallback(
    async (rawSymbol: string) => {
      const symbol = rawSymbol.trim().toUpperCase();
      if (!symbol) {
        showToast("Symbol required");
        return;
      }

      try {
        const state = await fetchWatchlists();
        const active = state.lists.find((list) => list.id === state.active_id);
        if (!active) {
          showToast("No active watchlist");
          return;
        }
        if (active.symbols.includes(symbol)) {
          showToast(`${symbol} already in watchlist`);
          return;
        }
        await updateWatchlist(active.id, {
          symbols: [...active.symbols, symbol],
        });
        showToast(`Added ${symbol} to watchlist`);
      } catch (err) {
        showToast(
          err instanceof Error ? err.message : "Failed to add symbol",
        );
      }
    },
    [showToast],
  );

  const watchlistRemove = useCallback(
    async (rawSymbol: string) => {
      const symbol = rawSymbol.trim().toUpperCase();
      if (!symbol) {
        showToast("Symbol required");
        return;
      }

      try {
        const state = await fetchWatchlists();
        const active = state.lists.find((list) => list.id === state.active_id);
        if (!active) {
          showToast("No active watchlist");
          return;
        }
        if (!active.symbols.includes(symbol)) {
          showToast(`${symbol} not in watchlist`);
          return;
        }
        await updateWatchlist(active.id, {
          symbols: active.symbols.filter((s) => s !== symbol),
        });
        showToast(`Removed ${symbol} from watchlist`);
      } catch (err) {
        showToast(
          err instanceof Error ? err.message : "Failed to remove symbol",
        );
      }
    },
    [showToast],
  );

  const run = useCallback(
    (id: string) => {
      switch (id) {
        case "positions":
          showToast("/positions — coming soon");
          break;
        case "add":
          openWidgetPicker();
          break;
        case "theme": {
          const current = WORKSPACE_THEMES.includes(theme as WorkspaceTheme)
            ? (theme as WorkspaceTheme)
            : "carbon";
          const idx = WORKSPACE_THEMES.indexOf(current);
          const next = WORKSPACE_THEMES[(idx + 1) % WORKSPACE_THEMES.length];
          setTheme(next);
          showToast(`Theme: ${WORKSPACE_THEME_LABELS[next]}`);
          break;
        }
        case "clear":
          void aui.threads().switchToNewThread();
          showToast("Chat cleared");
          break;
        default:
          break;
      }
      closePalette();
    },
    [aui, closePalette, openWidgetPicker, setTheme, showToast, theme],
  );

  const onInputKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key !== "Enter") return;

      const value = event.currentTarget.value.trim();
      const addMatch = WATCHLIST_ADD_RE.exec(value);
      if (addMatch) {
        event.preventDefault();
        void watchlistAdd(addMatch[1]).then(closePalette);
        return;
      }

      const removeMatch = WATCHLIST_REMOVE_RE.exec(value);
      if (removeMatch) {
        event.preventDefault();
        void watchlistRemove(removeMatch[1]).then(closePalette);
      }
    },
    [closePalette, watchlistAdd, watchlistRemove],
  );

  return (
    <Command className="h-full rounded-none border-0 bg-transparent">
      <CommandInput
        placeholder="Type a command…"
        autoFocus
        onKeyDown={onInputKeyDown}
      />
      <CommandList>
        <CommandEmpty>No commands found.</CommandEmpty>
        <CommandGroup heading="Commands">
          <CommandItem onSelect={() => run("positions")}>
            <span className="font-mono text-[var(--c-command)]">/positions</span>
            <span className="text-muted-foreground text-xs">
              Jump to positions widget
            </span>
          </CommandItem>
          <CommandItem onSelect={() => run("add")}>
            <span className="font-mono text-[var(--c-command)]">/add</span>
            <span className="text-muted-foreground text-xs">
              Add a widget
            </span>
          </CommandItem>
          <CommandItem onSelect={() => run("theme")}>
            <span className="font-mono text-[var(--c-command)]">/theme</span>
            <span className="text-muted-foreground text-xs">
              Cycle workspace theme
            </span>
          </CommandItem>
          <CommandItem onSelect={() => run("clear")}>
            <span className="font-mono text-[var(--c-command)]">/clear</span>
            <span className="text-muted-foreground text-xs">
              Start a new chat thread
            </span>
          </CommandItem>
          <CommandItem
            value="watchlist add symbol"
            onSelect={() => showToast("Type /watchlist add SYM")}
          >
            <span className="font-mono text-[var(--c-command)]">
              /watchlist add
            </span>
            <span className="text-muted-foreground text-xs">
              Add symbol to active watchlist
            </span>
          </CommandItem>
          <CommandItem
            value="watchlist remove symbol"
            onSelect={() => showToast("Type /watchlist remove SYM")}
          >
            <span className="font-mono text-[var(--c-command)]">
              /watchlist remove
            </span>
            <span className="text-muted-foreground text-xs">
              Remove symbol from active watchlist
            </span>
          </CommandItem>
        </CommandGroup>
        <CommandGroup heading="Themes">
          {WORKSPACE_THEMES.map((t) => (
            <CommandItem
              key={t}
              onSelect={() => {
                setTheme(t);
                showToast(`Theme: ${WORKSPACE_THEME_LABELS[t]}`);
                setPaletteOpen(false);
              }}
            >
              <span className="font-mono">{WORKSPACE_THEME_LABELS[t]}</span>
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </Command>
  );
}
