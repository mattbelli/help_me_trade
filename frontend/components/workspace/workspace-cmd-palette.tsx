"use client";

import { useTheme } from "next-themes";
import { useAui } from "@assistant-ui/react";
import { useCallback } from "react";

import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { useConsoleStore } from "@/lib/workspace/use-console-store";
import {
  WORKSPACE_THEME_LABELS,
  WORKSPACE_THEMES,
  type WorkspaceTheme,
} from "@/lib/workspace/themes";

import { useWorkspaceToast } from "./workspace-toast";

export function WorkspaceCmdPalette() {
  const { showToast } = useWorkspaceToast();
  const { theme, setTheme } = useTheme();
  const { setPaletteOpen, setOpen } = useConsoleStore();
  const aui = useAui();

  const run = useCallback(
    (id: string) => {
      switch (id) {
        case "positions":
          showToast("/positions — coming soon");
          break;
        case "add":
          showToast("/add — widget picker coming soon");
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
      setPaletteOpen(false);
      setOpen(true);
    },
    [aui, setOpen, setPaletteOpen, setTheme, showToast, theme],
  );

  return (
    <Command className="h-full rounded-none border-0 bg-transparent">
      <CommandInput placeholder="Type a command…" autoFocus />
      <CommandList>
        <CommandEmpty>No commands found.</CommandEmpty>
        <CommandGroup heading="Commands">
          <CommandItem onSelect={() => run("positions")}>
            <span className="font-mono text-accent">/positions</span>
            <span className="text-muted-foreground text-xs">
              Jump to positions widget
            </span>
          </CommandItem>
          <CommandItem onSelect={() => run("add")}>
            <span className="font-mono text-accent">/add</span>
            <span className="text-muted-foreground text-xs">
              Add a widget
            </span>
          </CommandItem>
          <CommandItem onSelect={() => run("theme")}>
            <span className="font-mono text-accent">/theme</span>
            <span className="text-muted-foreground text-xs">
              Cycle workspace theme
            </span>
          </CommandItem>
          <CommandItem onSelect={() => run("clear")}>
            <span className="font-mono text-accent">/clear</span>
            <span className="text-muted-foreground text-xs">
              Start a new chat thread
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
