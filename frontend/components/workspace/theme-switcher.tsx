"use client";

import { useTheme } from "next-themes";
import { useState, useSyncExternalStore } from "react";
import { CheckIcon, PaletteIcon, PencilIcon, PlusIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  isBuiltinTheme,
  WORKSPACE_THEME_LABELS,
  WORKSPACE_THEMES,
} from "@/lib/workspace/themes";
import { useCustomThemeStore } from "@/lib/workspace/use-custom-theme-store";
import { cn } from "@/lib/utils";

import { ThemeEditorDialog } from "./theme-editor-dialog";

const subscribe = () => () => {};
const getSnapshot = () => true;
const getServerSnapshot = () => false;

export function ThemeSwitcher({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const customThemes = useCustomThemeStore((s) => s.themes);
  const remove = useCustomThemeStore((s) => s.remove);

  const [menuOpen, setMenuOpen] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | undefined>(undefined);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);

  const active = mounted ? (theme ?? "carbon") : "carbon";

  function openClone() {
    setEditingId(undefined);
    setMenuOpen(false);
    setEditorOpen(true);
  }

  function openEdit(id: string) {
    setEditingId(id);
    setMenuOpen(false);
    setEditorOpen(true);
  }

  function deleteTheme(id: string) {
    const target = customThemes.find((t) => t.id === id);
    remove(id);
    setConfirmingDelete(null);
    if (theme === id) {
      const fallback =
        target && isBuiltinTheme(target.base) ? target.base : "carbon";
      setTheme(fallback);
    }
  }

  return (
    <>
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
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
        <DropdownMenuContent align="end" className="min-w-52 font-mono text-xs">
          {WORKSPACE_THEMES.map((id) => (
            <DropdownMenuItem key={id} onClick={() => setTheme(id)} className="gap-2">
              <span
                className="size-3 rounded-sm border bg-background border-border"
                data-theme={id}
              />
              {WORKSPACE_THEME_LABELS[id]}
              {active === id ? (
                <CheckIcon className="ml-auto size-3.5 text-accent" />
              ) : null}
            </DropdownMenuItem>
          ))}

          {customThemes.length > 0 ? <DropdownMenuSeparator /> : null}

          {customThemes.map((ct) => (
            <div
              key={ct.id}
              className="group/custom relative flex cursor-default items-center gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-accent hover:text-accent-foreground"
            >
              <button
                type="button"
                onClick={() => {
                  setTheme(ct.id);
                  setMenuOpen(false);
                }}
                className="flex flex-1 items-center gap-2 truncate text-left"
              >
                <span
                  className="size-3 shrink-0 rounded-sm border bg-background border-border"
                  data-theme={ct.id}
                />
                <span className="truncate">{ct.name}</span>
                {active === ct.id ? (
                  <CheckIcon className="size-3.5 text-accent" />
                ) : null}
              </button>

              {confirmingDelete === ct.id ? (
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    aria-label="Confirm delete"
                    onClick={() => deleteTheme(ct.id)}
                    className="rounded p-0.5 text-[var(--c-red)] hover:bg-[var(--c-red)]/15"
                  >
                    <CheckIcon className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    aria-label="Cancel delete"
                    onClick={() => setConfirmingDelete(null)}
                    className="rounded p-0.5 text-[var(--fg-dim)] hover:bg-[var(--bg-hover)]"
                  >
                    <XIcon className="size-3.5" />
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover/custom:opacity-100">
                  <button
                    type="button"
                    aria-label="Edit theme"
                    onClick={() => openEdit(ct.id)}
                    className="rounded p-0.5 text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground"
                  >
                    <PencilIcon className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    aria-label="Delete theme"
                    onClick={() => setConfirmingDelete(ct.id)}
                    className="rounded p-0.5 text-[var(--fg-dim)] hover:bg-[var(--c-red)]/15 hover:text-[var(--c-red)]"
                  >
                    <XIcon className="size-3.5" />
                  </button>
                </div>
              )}
            </div>
          ))}

          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={openClone} className="gap-2">
            <PlusIcon className="size-3.5" />
            Custom theme
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <ThemeEditorDialog
        open={editorOpen}
        onOpenChange={setEditorOpen}
        editingId={editingId}
      />
    </>
  );
}
