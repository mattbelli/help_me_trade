"use client";

import { useAui } from "@assistant-ui/react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useCallback } from "react";

import {
  fetchWatchlists,
  updateWatchlist,
} from "@/lib/api";
import { useTerminalStore } from "@/lib/workspace/use-terminal-store";
import {
  WORKSPACE_THEME_LABELS,
  WORKSPACE_THEMES,
  type WorkspaceTheme,
} from "@/lib/workspace/themes";
import { useCustomThemeStore } from "@/lib/workspace/use-custom-theme-store";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import {
  WIDGET_REGISTRY,
} from "@/lib/workspace/widget-registry";

import { useWorkspaceToast } from "@/components/workspace/workspace-toast";

const WATCHLIST_ADD_RE = /^\/watchlist\s+add\s+([A-Za-z.\-]+)$/i;
const WATCHLIST_REMOVE_RE = /^\/watchlist\s+remove\s+([A-Za-z.\-]+)$/i;
const THEME_NAMED_RE = /^\/theme(?:\s+(\w+))?$/i;

export type CommandResult = {
  handled: boolean;
  output?: string;
  error?: string;
};

export const WORKSPACE_COMMANDS = [
  "/positions",
  "/board",
  "/lab",
  "/add",
  "/theme",
  "/clear",
  "/watchlist add",
  "/watchlist remove",
] as const;

export function useWorkspaceCommands() {
  const router = useRouter();
  const { showToast } = useWorkspaceToast();
  const { theme, setTheme } = useTheme();
  const openWidgetPicker = useWorkspaceStore((s) => s.openPicker);
  const addWidget = useWorkspaceStore((s) => s.addWidget);
  const clearScrollback = useTerminalStore((s) => s.clearScrollback);
  const customThemes = useCustomThemeStore((s) => s.themes);
  const aui = useAui();

  const runCommand = useCallback(
    async (rawInput: string): Promise<CommandResult> => {
      const value = rawInput.trim();
      if (!value.startsWith("/")) {
        return { handled: false };
      }

      const addMatch = WATCHLIST_ADD_RE.exec(value);
      if (addMatch) {
        const symbol = addMatch[1].trim().toUpperCase();
        if (!symbol) {
          return { handled: true, error: "symbol required" };
        }

        try {
          const state = await fetchWatchlists();
          const active = state.lists.find((list) => list.id === state.active_id);
          if (!active) {
            return { handled: true, error: "no active watchlist" };
          }
          if (active.symbols.includes(symbol)) {
            return { handled: true, output: `${symbol} already in watchlist` };
          }
          await updateWatchlist(active.id, {
            symbols: [...active.symbols, symbol],
          });
          showToast(`Added ${symbol} to watchlist`);
          return { handled: true, output: `added ${symbol} to watchlist` };
        } catch (err) {
          const msg =
            err instanceof Error ? err.message : "failed to add symbol";
          return { handled: true, error: msg };
        }
      }

      const removeMatch = WATCHLIST_REMOVE_RE.exec(value);
      if (removeMatch) {
        const symbol = removeMatch[1].trim().toUpperCase();
        if (!symbol) {
          return { handled: true, error: "symbol required" };
        }

        try {
          const state = await fetchWatchlists();
          const active = state.lists.find((list) => list.id === state.active_id);
          if (!active) {
            return { handled: true, error: "no active watchlist" };
          }
          if (!active.symbols.includes(symbol)) {
            return { handled: true, output: `${symbol} not in watchlist` };
          }
          await updateWatchlist(active.id, {
            symbols: active.symbols.filter((s) => s !== symbol),
          });
          showToast(`Removed ${symbol} from watchlist`);
          return { handled: true, output: `removed ${symbol} from watchlist` };
        } catch (err) {
          const msg =
            err instanceof Error ? err.message : "failed to remove symbol";
          return { handled: true, error: msg };
        }
      }

      // Bare "/watchlist" or "/watchlist add" (no symbol) — show usage instead
      // of the misleading "unknown command"; point widget-seekers at /add.
      if (/^\/watchlist(\s+(add|remove))?$/i.test(value)) {
        return {
          handled: true,
          error:
            "usage: /watchlist add <symbol> · /watchlist remove <symbol> — " +
            "for the watchlist widget use /add watchlist",
        };
      }

      const themeMatch = THEME_NAMED_RE.exec(value);
      if (themeMatch) {
        const named = themeMatch[1]?.toLowerCase();
        if (named) {
          if (WORKSPACE_THEMES.includes(named as WorkspaceTheme)) {
            setTheme(named);
            const label = WORKSPACE_THEME_LABELS[named as WorkspaceTheme];
            showToast(`Theme: ${label}`);
            return { handled: true, output: `theme → ${label.toLowerCase()}` };
          }

          const custom = customThemes.find(
            (t) => t.name.toLowerCase() === named,
          );
          if (custom) {
            setTheme(custom.id);
            showToast(`Theme: ${custom.name}`);
            return { handled: true, output: `theme → ${custom.name.toLowerCase()}` };
          }

          const customNames = customThemes.map((t) => t.name.toLowerCase());
          const valid = [
            "carbon",
            "lab",
            "warm",
            "oled",
            "light",
            ...customNames,
          ].join(", ");
          return {
            handled: true,
            error: `unknown theme: ${named} (${valid})`,
          };
        }

        const current = WORKSPACE_THEMES.includes(theme as WorkspaceTheme)
          ? (theme as WorkspaceTheme)
          : "carbon";
        const idx = WORKSPACE_THEMES.indexOf(current);
        const next = WORKSPACE_THEMES[(idx + 1) % WORKSPACE_THEMES.length];
        setTheme(next);
        showToast(`Theme: ${WORKSPACE_THEME_LABELS[next]}`);
        return {
          handled: true,
          output: `theme → ${WORKSPACE_THEME_LABELS[next].toLowerCase()}`,
        };
      }

      switch (value.split(/\s+/)[0]) {
        case "/positions":
          showToast("/positions — coming soon");
          return { handled: true, output: "positions — coming soon" };
        case "/board":
          router.push("/board");
          showToast("Project board");
          return { handled: true, output: "navigating to project board" };
        case "/lab":
          router.push("/strategy-lab");
          showToast("Strategy Lab");
          return { handled: true, output: "navigating to strategy lab" };
        case "/add": {
          const arg = value.split(/\s+/).slice(1).join(" ").trim();
          if (!arg) {
            openWidgetPicker();
            return { handled: true, output: "widget picker opened" };
          }

          const needle = arg.toLowerCase();
          // Exact id → title → substring of either (so "/add chain" and
          // "/add trading calendar" both resolve).
          const widget =
            WIDGET_REGISTRY.find((entry) => entry.id.toLowerCase() === needle) ??
            WIDGET_REGISTRY.find(
              (entry) => entry.title.toLowerCase() === needle,
            ) ??
            WIDGET_REGISTRY.find(
              (entry) =>
                entry.id.toLowerCase().includes(needle) ||
                entry.title.toLowerCase().includes(needle),
            );
          if (!widget) {
            const ids = WIDGET_REGISTRY.map((entry) => entry.id).join(", ");
            return {
              handled: true,
              error: `unknown widget: ${arg} (valid: ${ids})`,
            };
          }

          const added = addWidget(widget.id);
          if (added) {
            return { handled: true, output: `added ${widget.id}` };
          }

          return {
            handled: true,
            error: `${widget.id} is already on this page`,
          };
        }
        case "/clear":
          void aui.threads().switchToNewThread();
          clearScrollback();
          showToast("Chat cleared");
          return { handled: true, output: "chat cleared" };
        default:
          return {
            handled: true,
            error: `unknown command: ${value.split(/\s+/)[0]} — available: ${WORKSPACE_COMMANDS.join(" · ")}`,
          };
      }
    },
    [addWidget, aui, clearScrollback, customThemes, openWidgetPicker, router, setTheme, showToast, theme],
  );

  return { runCommand };
}
