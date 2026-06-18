"use client";

import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";
import { CheckIcon, PaletteIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  WORKSPACE_THEME_LABELS,
  WORKSPACE_THEMES,
  type WorkspaceTheme,
} from "@/lib/workspace/themes";
import { cn } from "@/lib/utils";

const THEME_SWATCH: Record<WorkspaceTheme, string> = {
  carbon: "bg-background border-border",
  ember: "bg-background border-border",
  paper: "bg-background border-border",
};

const subscribe = () => () => {};
const getSnapshot = () => true;
const getServerSnapshot = () => false;

export function ThemeSwitcher({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const active =
    mounted && WORKSPACE_THEMES.includes(theme as WorkspaceTheme)
      ? (theme as WorkspaceTheme)
      : "carbon";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="sm"
            className={cn(
              "h-7 gap-1.5 px-2.5 font-mono text-[11px] text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground",
              className,
            )}
          />
        }
      >
        <PaletteIcon className="size-3.5" />
        theme
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-44 font-mono text-xs">
        {WORKSPACE_THEMES.map((id) => (
          <DropdownMenuItem
            key={id}
            onClick={() => setTheme(id)}
            className="gap-2"
          >
            <span
              className={cn(
                "size-3 rounded-sm border",
                THEME_SWATCH[id],
              )}
              data-theme={id}
            />
            {WORKSPACE_THEME_LABELS[id]}
            {active === id ? (
              <CheckIcon className="ml-auto size-3.5 text-accent" />
            ) : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
