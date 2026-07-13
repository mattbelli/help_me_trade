"use client";

import { useTheme } from "next-themes";
import { useState, useSyncExternalStore } from "react";
import {
  CheckIcon,
  Monitor,
  Moon,
  PencilIcon,
  PlusIcon,
  Star,
  Sun,
  XIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { resolveThemeIsDark } from "@/lib/workspace/custom-themes";
import {
  isBuiltinTheme,
  WORKSPACE_THEME_LABELS,
  WORKSPACE_THEMES,
} from "@/lib/workspace/themes";
import { useCustomThemeStore } from "@/lib/workspace/use-custom-theme-store";
import {
  DEFAULT_DARK_THEME,
  DEFAULT_LIGHT_THEME,
  prefersDarkNow,
  useThemeModeStore,
} from "@/lib/workspace/use-theme-mode-store";
import { cn } from "@/lib/utils";

import { ThemeEditorDialog } from "./theme-editor-dialog";

const subscribe = () => () => {};
const getSnapshot = () => true;
const getServerSnapshot = () => false;

type ThemeEntry = { id: string; label: string; custom: boolean; dark: boolean };

const MODES: {
  key: "dark" | "light" | "system";
  label: string;
  Icon: React.ComponentType<{ className?: string }>;
}[] = [
  { key: "dark", label: "Dark", Icon: Moon },
  { key: "light", label: "Light", Icon: Sun },
  { key: "system", label: "System", Icon: Monitor },
];

export function ThemeSwitcher({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const customThemes = useCustomThemeStore((s) => s.themes);
  const remove = useCustomThemeStore((s) => s.remove);

  const system = useThemeModeStore((s) => s.system);
  const defaultDark = useThemeModeStore((s) => s.defaultDark);
  const defaultLight = useThemeModeStore((s) => s.defaultLight);
  const setSystem = useThemeModeStore((s) => s.setSystem);
  const setDefaultDark = useThemeModeStore((s) => s.setDefaultDark);
  const setDefaultLight = useThemeModeStore((s) => s.setDefaultLight);

  const [menuOpen, setMenuOpen] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | undefined>(undefined);
  const [cloneBaseId, setCloneBaseId] = useState<string | undefined>(undefined);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);

  const active = mounted ? (theme ?? "carbon") : "carbon";

  const knownIds = new Set<string>([
    ...WORKSPACE_THEMES,
    ...customThemes.map((t) => t.id),
  ]);
  const resolvedDefaultDark = knownIds.has(defaultDark)
    ? defaultDark
    : DEFAULT_DARK_THEME;
  const resolvedDefaultLight = knownIds.has(defaultLight)
    ? defaultLight
    : DEFAULT_LIGHT_THEME;

  const entries: ThemeEntry[] = [
    ...WORKSPACE_THEMES.map((id) => ({
      id,
      label: WORKSPACE_THEME_LABELS[id],
      custom: false,
    })),
    ...customThemes.map((ct) => ({ id: ct.id, label: ct.name, custom: true })),
  ].map((e) => ({ ...e, dark: resolveThemeIsDark(e.id, customThemes) }));
  const darkEntries = entries.filter((e) => e.dark);
  const lightEntries = entries.filter((e) => !e.dark);

  const activeSegment: "dark" | "light" | "system" = system
    ? "system"
    : resolveThemeIsDark(active, customThemes)
      ? "dark"
      : "light";

  function selectMode(mode: "dark" | "light" | "system") {
    if (mode === "system") {
      setSystem(true);
      setTheme(prefersDarkNow() ? resolvedDefaultDark : resolvedDefaultLight);
    } else {
      setSystem(false);
      setTheme(mode === "dark" ? resolvedDefaultDark : resolvedDefaultLight);
    }
  }

  // Picking a theme applies it, drops System follow, and adopts it as that
  // mode's default (the last dark/light theme you chose becomes its default).
  function applyTheme(id: string, dark: boolean) {
    setSystem(false);
    if (dark) setDefaultDark(id);
    else setDefaultLight(id);
    setTheme(id);
    setMenuOpen(false);
  }

  // Pin a theme as its group's default without switching to it now.
  function setGroupDefault(id: string, dark: boolean) {
    if (dark) setDefaultDark(id);
    else setDefaultLight(id);
  }

  function openClone() {
    setEditingId(undefined);
    setCloneBaseId(undefined);
    setMenuOpen(false);
    setEditorOpen(true);
  }

  function openEdit(id: string) {
    setEditingId(id);
    setCloneBaseId(undefined);
    setMenuOpen(false);
    setEditorOpen(true);
  }

  // "Edit" on a BUILT-IN: the factory themes stay intact — the editor opens
  // seeded from that theme's palette and Save creates a custom copy.
  function openEditBuiltin(id: string) {
    setEditingId(undefined);
    setCloneBaseId(id);
    setMenuOpen(false);
    setEditorOpen(true);
  }

  function deleteTheme(id: string) {
    const target = customThemes.find((t) => t.id === id);
    remove(id);
    setConfirmingDelete(null);
    if (defaultDark === id) setDefaultDark(DEFAULT_DARK_THEME);
    if (defaultLight === id) setDefaultLight(DEFAULT_LIGHT_THEME);
    if (theme === id) {
      const fallback =
        target && isBuiltinTheme(target.base) ? target.base : "carbon";
      setTheme(fallback);
    }
  }

  function renderThemeRow(entry: ThemeEntry) {
    const isActive = active === entry.id;
    const isDefault = entry.dark
      ? resolvedDefaultDark === entry.id
      : resolvedDefaultLight === entry.id;

    return (
      <div
        key={entry.id}
        className="group/theme relative flex cursor-default items-center gap-2 rounded-md px-1.5 py-1 hover:bg-accent hover:text-accent-foreground"
      >
        <button
          type="button"
          onClick={() => applyTheme(entry.id, entry.dark)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <span
            className="size-3 shrink-0 rounded-sm border bg-background border-border"
            data-theme={entry.id}
          />
          <span className="truncate">{entry.label}</span>
          {isActive ? (
            <CheckIcon className="size-3.5 shrink-0 text-accent" />
          ) : null}
        </button>

        <div className="flex shrink-0 items-center gap-0.5">
          {isDefault ? (
            <span
              title={`Default ${entry.dark ? "dark" : "light"} theme`}
              className="p-0.5 text-[var(--c-amber)]"
            >
              <Star className="size-3.5 fill-current" />
            </span>
          ) : (
            <button
              type="button"
              aria-label={`Set as default ${entry.dark ? "dark" : "light"} theme`}
              title={`Set as default ${entry.dark ? "dark" : "light"}`}
              onClick={() => setGroupDefault(entry.id, entry.dark)}
              className="rounded p-0.5 text-[var(--fg-faint)] opacity-0 transition-opacity group-hover/theme:opacity-100 hover:text-[var(--c-amber)]"
            >
              <Star className="size-3.5" />
            </button>
          )}

          {!entry.custom ? (
            <button
              type="button"
              aria-label={`Edit ${entry.label} (as a copy)`}
              title="Edit (saves as your copy)"
              onClick={() => openEditBuiltin(entry.id)}
              className="rounded p-0.5 text-[var(--fg-dim)] opacity-0 transition-opacity group-hover/theme:opacity-100 hover:bg-[var(--bg-hover)] hover:text-foreground"
            >
              <PencilIcon className="size-3.5" />
            </button>
          ) : null}
          {entry.custom ? (
            confirmingDelete === entry.id ? (
              <>
                <button
                  type="button"
                  aria-label="Confirm delete"
                  onClick={() => deleteTheme(entry.id)}
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
              </>
            ) : (
              <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover/theme:opacity-100">
                <button
                  type="button"
                  aria-label="Edit theme"
                  onClick={() => openEdit(entry.id)}
                  className="rounded p-0.5 text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground"
                >
                  <PencilIcon className="size-3.5" />
                </button>
                <button
                  type="button"
                  aria-label="Delete theme"
                  onClick={() => setConfirmingDelete(entry.id)}
                  className="rounded p-0.5 text-[var(--fg-dim)] hover:bg-[var(--c-red)]/15 hover:text-[var(--c-red)]"
                >
                  <XIcon className="size-3.5" />
                </button>
              </div>
            )
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <>
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger
          render={
            <Button
              variant="ghost"
              size="sm"
              title="Theme"
              className={cn(
                "h-7 w-7 rounded p-1 text-[var(--fg-faint)] hover:bg-[var(--bg-hover)] hover:text-foreground",
                className,
              )}
            />
          }
        >
          <Moon className="size-3.5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="min-w-56 font-mono text-[length:var(--fs-body)]"
        >
          {/* Mode: Dark | Light | System */}
          <div className="flex gap-0.5 rounded-md border border-[var(--border)] bg-[var(--bg-inset)] p-0.5">
            {MODES.map(({ key, label, Icon }) => (
              <button
                key={key}
                type="button"
                onClick={() => selectMode(key)}
                className={cn(
                  "flex flex-1 items-center justify-center gap-1 rounded px-1.5 py-1 text-[length:var(--fs-table)] transition-colors",
                  activeSegment === key
                    ? "bg-[var(--bg-hover)] text-foreground"
                    : "text-[var(--fg-dim)] hover:text-foreground",
                )}
              >
                <Icon className="size-3" />
                {label}
              </button>
            ))}
          </div>

          {darkEntries.length > 0 ? (
            <>
              <div className="px-1.5 pt-2 pb-1 text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
                Dark
              </div>
              {darkEntries.map(renderThemeRow)}
            </>
          ) : null}

          {lightEntries.length > 0 ? (
            <>
              <div className="px-1.5 pt-2 pb-1 text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
                Light
              </div>
              {lightEntries.map(renderThemeRow)}
            </>
          ) : null}

          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={openClone} className="gap-2">
            <PlusIcon className="size-3.5" />
            New theme
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <ThemeEditorDialog
        open={editorOpen}
        onOpenChange={setEditorOpen}
        editingId={editingId}
        cloneBaseId={cloneBaseId}
      />
    </>
  );
}
