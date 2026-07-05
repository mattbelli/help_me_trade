"use client";

import { useTheme } from "next-themes";
import { useEffect, useMemo, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { TerminalButton, TerminalInput } from "@/components/ui/terminal";
import {
  applyInlinePreview,
  bulkSetValue,
  captureThemeVars,
  clearInlinePreview,
  isHexColor,
  normalizeHex,
  THEME_COLOR_ROWS,
  type ThemeColorMember,
} from "@/lib/workspace/custom-themes";
import {
  isBuiltinTheme,
  WORKSPACE_THEME_LABELS,
  type WorkspaceTheme,
} from "@/lib/workspace/themes";
import { useCustomThemeStore } from "@/lib/workspace/use-custom-theme-store";

type ThemeEditorDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Absent ⇒ clone-the-current-theme mode. */
  editingId?: string;
};

function baseLabel(base: string, customName: string | undefined): string {
  if (isBuiltinTheme(base)) {
    return WORKSPACE_THEME_LABELS[base as WorkspaceTheme];
  }
  return customName ?? "Custom";
}

/** Distinct current values among a group's plain-color members (chip stripes). */
function distinctMemberColors(
  members: ThemeColorMember[],
  vars: Record<string, string>,
): string[] {
  const values = members
    .filter((m) => (m.bulkSet ?? "color") === "color")
    .map((m) => (vars[m.var] ?? "").trim())
    .filter(Boolean);
  return Array.from(new Set(values));
}

export function ThemeEditorDialog({
  open,
  onOpenChange,
  editingId,
}: ThemeEditorDialogProps) {
  const { theme, setTheme } = useTheme();
  const themes = useCustomThemeStore((s) => s.themes);
  const createFromCurrent = useCustomThemeStore((s) => s.createFromCurrent);
  const updateVars = useCustomThemeStore((s) => s.updateVars);
  const rename = useCustomThemeStore((s) => s.rename);
  const remove = useCustomThemeStore((s) => s.remove);

  const editing = useMemo(
    () => (editingId ? themes.find((t) => t.id === editingId) : undefined),
    [editingId, themes],
  );

  const [name, setName] = useState("");
  // ONE working var map: group swatches write all members, member rows write
  // one var, last write wins. Saved verbatim (tokens re-derived at save).
  const [vars, setVars] = useState<Record<string, string>>({});
  const [cloneBase, setCloneBase] = useState<string>("carbon");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // Seed when a fresh editor session opens (render-time adjust on prop change —
  // the React-endorsed "store previous value in state" pattern, not an effect).
  const sessionKey = open ? `${editingId ?? "clone"}` : null;
  const [seededKey, setSeededKey] = useState<string | null>(null);
  if (seededKey !== sessionKey) {
    setSeededKey(sessionKey);
    if (sessionKey === null) {
      // closed — nothing to seed
    } else if (editing) {
      setName(editing.name);
      setVars({ ...editing.vars });
      setCloneBase(editing.base);
      setExpanded({});
    } else {
      // Clone-current mode: snapshot the resolved active theme.
      setName("");
      setVars(captureThemeVars());
      setCloneBase(theme ?? "carbon");
      setExpanded({});
    }
  }

  // Paint the live preview whenever the editor is open with seeded vars.
  useEffect(() => {
    if (!open || Object.keys(vars).length === 0) return;
    applyInlinePreview(vars);
  }, [open, vars]);

  function handleVarChange(cssVar: string, raw: string) {
    const next = { ...vars, [cssVar]: raw };
    setVars(next);
    applyInlinePreview(next);
  }

  function handleGroupSet(members: ThemeColorMember[], raw: string) {
    const hex = normalizeHex(raw);
    const next = { ...vars };
    for (const m of members) {
      const value = bulkSetValue(m.bulkSet, hex);
      if (value !== null) next[m.var] = value;
    }
    setVars(next);
    applyInlinePreview(next);
  }

  function handleCancel() {
    clearInlinePreview();
    onOpenChange(false);
  }

  function handleSave() {
    if (editing) {
      updateVars(editing.id, vars);
      if (name.trim() && name.trim() !== editing.name) {
        rename(editing.id, name.trim());
      }
      clearInlinePreview();
      setTheme(editing.id);
    } else {
      // Inline preview already reflects the edits → captured by createFromCurrent.
      const id = createFromCurrent(name, cloneBase);
      clearInlinePreview();
      setTheme(id);
    }
    onOpenChange(false);
  }

  function handleDelete() {
    if (!editing) return;
    remove(editing.id);
    clearInlinePreview();
    if (theme === editing.id) {
      setTheme(isBuiltinTheme(editing.base) ? editing.base : "carbon");
    }
    onOpenChange(false);
  }

  function renderMemberRow({ var: cssVar, label }: ThemeColorMember) {
    const value = vars[cssVar] ?? "";
    return (
      <div key={cssVar} className="flex items-center gap-2 pl-5">
        <div className="flex w-32 shrink-0 flex-col leading-tight">
          <span className="font-mono text-[length:var(--fs-table)] text-foreground">{label}</span>
          <span className="font-mono text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
            {cssVar}
          </span>
        </div>
        {isHexColor(value) ? (
          <input
            type="color"
            aria-label={label}
            value={normalizeHex(value)}
            onChange={(e) => handleVarChange(cssVar, e.target.value)}
            className="size-6 shrink-0 cursor-pointer rounded border border-[var(--border)] bg-transparent p-0.5"
          />
        ) : null}
        <TerminalInput
          value={value}
          onChange={(e) => handleVarChange(cssVar, e.target.value)}
          spellCheck={false}
          className="h-6 flex-1"
        />
      </div>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) clearInlinePreview();
        onOpenChange(next);
      }}
    >
      <DialogContent
        className="border border-[var(--border)] bg-[var(--panel)] font-mono shadow-[var(--panel-shadow)] sm:max-w-md"
        showCloseButton={false}
      >
        <DialogHeader>
          <DialogTitle className="font-mono text-[length:var(--fs-title)] tracking-[0.08em] uppercase text-foreground">
            {editing ? "Edit theme" : "New theme"}
          </DialogTitle>
          <DialogDescription className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
            Cloned from {baseLabel(cloneBase, editing?.name)}
          </DialogDescription>
        </DialogHeader>

        <label className="flex items-center gap-3">
          <span className="w-20 shrink-0 font-mono text-[length:var(--fs-micro)] tracking-[0.08em] uppercase text-[var(--fg-faint)]">
            Name
          </span>
          <TerminalInput
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My theme"
            className="h-7"
          />
        </label>

        <div className="flex max-h-[min(420px,55vh)] flex-col gap-1.5 overflow-y-auto pr-1">
          {THEME_COLOR_ROWS.map((row) => {
            if (row.kind === "single") {
              const value = vars[row.var] ?? "";
              return (
                <div key={row.var} className="flex items-center gap-2">
                  <span className="w-2 shrink-0" />
                  <span className="w-24 shrink-0 font-mono text-[length:var(--fs-micro)] tracking-[0.08em] uppercase text-[var(--fg-faint)]">
                    {row.label}
                  </span>
                  {isHexColor(value) ? (
                    <input
                      type="color"
                      aria-label={row.label}
                      value={normalizeHex(value)}
                      onChange={(e) => handleVarChange(row.var, e.target.value)}
                      className="size-7 shrink-0 cursor-pointer rounded border border-[var(--border)] bg-transparent p-0.5"
                    />
                  ) : null}
                  <TerminalInput
                    value={value}
                    onChange={(e) => handleVarChange(row.var, e.target.value)}
                    spellCheck={false}
                    className="h-7 w-28"
                  />
                </div>
              );
            }

            const isOpen = expanded[row.label] ?? false;
            const distinct = distinctMemberColors(row.members, vars);
            const uniform = distinct.length <= 1;
            const leadValue = normalizeHex(vars[row.lead] ?? "#000000");
            return (
              <div key={row.label} className="flex flex-col gap-1">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    aria-label={`${isOpen ? "Collapse" : "Expand"} ${row.label}`}
                    onClick={() =>
                      setExpanded((s) => ({ ...s, [row.label]: !isOpen }))
                    }
                    className="w-2 shrink-0 font-mono text-[length:var(--fs-micro)] text-[var(--fg-faint)] hover:text-foreground"
                  >
                    {isOpen ? "▾" : "▸"}
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      setExpanded((s) => ({ ...s, [row.label]: !isOpen }))
                    }
                    className="w-24 shrink-0 text-left font-mono text-[length:var(--fs-micro)] tracking-[0.08em] uppercase text-[var(--fg-faint)] hover:text-foreground"
                  >
                    {row.label}
                  </button>
                  {uniform ? (
                    <input
                      type="color"
                      aria-label={`${row.label} (all)`}
                      value={leadValue}
                      onChange={(e) => handleGroupSet(row.members, e.target.value)}
                      className="size-7 shrink-0 cursor-pointer rounded border border-[var(--border)] bg-transparent p-0.5"
                    />
                  ) : (
                    <label
                      className="relative size-7 shrink-0 cursor-pointer overflow-hidden rounded border border-[var(--border)] p-0.5"
                      title="Multiple colors — expand to edit individually, or pick to set all"
                    >
                      <span className="flex size-full overflow-hidden rounded-[2px]">
                        {distinct.slice(0, 4).map((c) => (
                          <span
                            key={c}
                            className="flex-1"
                            style={{ background: c }}
                          />
                        ))}
                      </span>
                      <input
                        type="color"
                        aria-label={`${row.label} (all)`}
                        value={leadValue}
                        onChange={(e) =>
                          handleGroupSet(row.members, e.target.value)
                        }
                        className="absolute inset-0 size-full cursor-pointer opacity-0"
                      />
                    </label>
                  )}
                </div>
                {isOpen ? (
                  <div className="flex flex-col gap-1">
                    {row.members.map(renderMemberRow)}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>

        <div className="-mx-4 -mb-4 flex items-center gap-2 rounded-b-xl border-t border-[var(--border)] bg-[var(--bg-inset)]/50 p-3">
          {editing ? (
            <TerminalButton variant="danger" onClick={handleDelete}>
              Delete
            </TerminalButton>
          ) : null}
          <TerminalButton onClick={handleCancel} className="ml-auto">
            Cancel
          </TerminalButton>
          <TerminalButton variant="primary" onClick={handleSave}>
            Save
          </TerminalButton>
        </div>
      </DialogContent>
    </Dialog>
  );
}
