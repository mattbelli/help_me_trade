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
  ALL_COLOR_GROUPS,
  applyInlinePreview,
  captureThemeVars,
  clearInlinePreview,
  deriveTokensFromVars,
  EDITABLE_TOKENS,
  isHexColor,
  mergeTokensIntoVars,
  normalizeHex,
  type EditableToken,
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
  const [tokens, setTokens] = useState<Record<EditableToken, string>>(
    () => ({}) as Record<EditableToken, string>,
  );
  const [baseVars, setBaseVars] = useState<Record<string, string>>({});
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [cloneBase, setCloneBase] = useState<string>("carbon");
  const [showAll, setShowAll] = useState(false);

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
      setTokens(editing.tokens);
      setBaseVars(editing.vars);
      setOverrides({});
      setCloneBase(editing.base);
    } else {
      // Clone-current mode: snapshot the resolved active theme.
      const vars = captureThemeVars();
      setName("");
      setTokens(deriveTokensFromVars(vars));
      setBaseVars(vars);
      setOverrides({});
      setCloneBase(theme ?? "carbon");
    }
  }

  // The working var map: quick-palette group fan-out with individual "All
  // colors" edits layered on top (individual edits win over group edits).
  const previewVars = useMemo(
    () => ({ ...mergeTokensIntoVars(baseVars, tokens), ...overrides }),
    [baseVars, tokens, overrides],
  );

  // Paint the live preview whenever the editor is open with seeded vars.
  useEffect(() => {
    if (!open || Object.keys(baseVars).length === 0) return;
    applyInlinePreview(previewVars);
  }, [open, baseVars, previewVars]);

  function handleColor(token: EditableToken, raw: string) {
    const hex = normalizeHex(raw);
    const nextTokens = { ...tokens, [token]: hex };
    setTokens(nextTokens);
    applyInlinePreview({ ...mergeTokensIntoVars(baseVars, nextTokens), ...overrides });
  }

  function handleVarChange(cssVar: string, raw: string) {
    const nextOverrides = { ...overrides, [cssVar]: raw };
    setOverrides(nextOverrides);
    applyInlinePreview({ ...mergeTokensIntoVars(baseVars, tokens), ...nextOverrides });
  }

  function handleCancel() {
    clearInlinePreview();
    onOpenChange(false);
  }

  function handleSave() {
    if (editing) {
      updateVars(editing.id, previewVars);
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
          <DialogTitle className="font-mono text-[12px] tracking-[0.1em] uppercase text-foreground">
            {editing ? "Edit theme" : "New theme"}
          </DialogTitle>
          <DialogDescription className="font-mono text-[10px] text-[var(--fg-dim)]">
            Cloned from {baseLabel(cloneBase, editing?.name)}
          </DialogDescription>
        </DialogHeader>

        <label className="flex items-center gap-3">
          <span className="w-20 shrink-0 font-mono text-[9px] tracking-[0.1em] uppercase text-[var(--fg-faint)]">
            Name
          </span>
          <TerminalInput
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My theme"
            className="h-7"
          />
        </label>

        <div className="flex flex-col gap-1.5">
          {EDITABLE_TOKENS.map(({ token, label }) => {
            const value = normalizeHex(tokens[token] ?? "#000000");
            return (
              <div key={token} className="flex items-center gap-3">
                <span className="w-20 shrink-0 font-mono text-[9px] tracking-[0.1em] uppercase text-[var(--fg-faint)]">
                  {label}
                </span>
                <input
                  type="color"
                  aria-label={label}
                  value={value}
                  onChange={(e) => handleColor(token, e.target.value)}
                  className="size-7 shrink-0 cursor-pointer rounded border border-[var(--border)] bg-transparent p-0.5"
                />
                <TerminalInput
                  value={tokens[token] ?? ""}
                  onChange={(e) => handleColor(token, e.target.value)}
                  spellCheck={false}
                  className="h-7 w-28 uppercase"
                />
              </div>
            );
          })}
        </div>

        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={() => setShowAll((s) => !s)}
            className="flex items-center gap-1.5 font-mono text-[9px] tracking-[0.1em] uppercase text-[var(--fg-dim)] hover:text-foreground"
          >
            <span className="w-2 text-[var(--fg-faint)]">
              {showAll ? "▾" : "▸"}
            </span>
            All colors
          </button>
          {showAll ? (
            <div className="flex max-h-[280px] flex-col gap-3 overflow-y-auto pr-1">
              {ALL_COLOR_GROUPS.map((group) => (
                <div key={group.label} className="flex flex-col gap-1">
                  <div className="font-mono text-[9px] tracking-[0.1em] uppercase text-[var(--fg-faint)]">
                    {group.label}
                  </div>
                  {group.vars.map(({ var: cssVar, label }) => {
                    const value = previewVars[cssVar] ?? "";
                    return (
                      <div key={cssVar} className="flex items-center gap-2">
                        <div className="flex w-32 shrink-0 flex-col leading-tight">
                          <span className="font-mono text-[10px] text-foreground">
                            {label}
                          </span>
                          <span className="font-mono text-[8px] text-[var(--fg-faint)]">
                            {cssVar}
                          </span>
                        </div>
                        {isHexColor(value) ? (
                          <input
                            type="color"
                            aria-label={label}
                            value={normalizeHex(value)}
                            onChange={(e) =>
                              handleVarChange(cssVar, e.target.value)
                            }
                            className="size-6 shrink-0 cursor-pointer rounded border border-[var(--border)] bg-transparent p-0.5"
                          />
                        ) : null}
                        <TerminalInput
                          value={value}
                          onChange={(e) =>
                            handleVarChange(cssVar, e.target.value)
                          }
                          spellCheck={false}
                          className="h-6 flex-1"
                        />
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          ) : null}
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
