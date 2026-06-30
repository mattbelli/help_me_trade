"use client";

import { useTheme } from "next-themes";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  applyInlinePreview,
  captureThemeVars,
  clearInlinePreview,
  deriveTokensFromVars,
  EDITABLE_TOKENS,
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
  const updateTokens = useCustomThemeStore((s) => s.updateTokens);
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
  const [cloneBase, setCloneBase] = useState<string>("carbon");

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
      setCloneBase(editing.base);
    } else {
      // Clone-current mode: snapshot the resolved active theme.
      const vars = captureThemeVars();
      setName("");
      setTokens(deriveTokensFromVars(vars));
      setBaseVars(vars);
      setCloneBase(theme ?? "carbon");
    }
  }

  // Paint the live preview whenever the editor is open with seeded vars.
  useEffect(() => {
    if (!open || Object.keys(baseVars).length === 0) return;
    applyInlinePreview(mergeTokensIntoVars(baseVars, tokens));
  }, [open, baseVars, tokens]);

  function handleColor(token: EditableToken, raw: string) {
    const hex = normalizeHex(raw);
    const nextTokens = { ...tokens, [token]: hex };
    setTokens(nextTokens);
    applyInlinePreview(mergeTokensIntoVars(baseVars, nextTokens));
  }

  function handleCancel() {
    clearInlinePreview();
    onOpenChange(false);
  }

  function handleSave() {
    if (editing) {
      for (const { token } of EDITABLE_TOKENS) {
        updateTokens(editing.id, token, tokens[token]);
      }
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
      <DialogContent className="sm:max-w-md font-mono" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{editing ? "Edit theme" : "New theme"}</DialogTitle>
          <DialogDescription>
            Cloned from {baseLabel(cloneBase, editing?.name)}
          </DialogDescription>
        </DialogHeader>

        <label className="flex items-center gap-3 text-xs text-[var(--fg-dim)]">
          <span className="w-20 shrink-0">Name</span>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My theme"
            className="h-7 font-mono text-xs"
          />
        </label>

        <div className="flex flex-col gap-1.5">
          {EDITABLE_TOKENS.map(({ token, label }) => {
            const value = normalizeHex(tokens[token] ?? "#000000");
            return (
              <div key={token} className="flex items-center gap-3">
                <span className="w-20 shrink-0 text-xs text-[var(--fg-dim)]">
                  {label}
                </span>
                <input
                  type="color"
                  aria-label={label}
                  value={value}
                  onChange={(e) => handleColor(token, e.target.value)}
                  className="size-7 shrink-0 cursor-pointer rounded border border-[var(--border)] bg-transparent p-0.5"
                />
                <Input
                  value={tokens[token] ?? ""}
                  onChange={(e) => handleColor(token, e.target.value)}
                  spellCheck={false}
                  className="h-7 w-28 font-mono text-xs uppercase"
                />
              </div>
            );
          })}
        </div>

        <div className="-mx-4 -mb-4 flex items-center gap-2 rounded-b-xl border-t border-[var(--border)] bg-[var(--bg-inset)]/50 p-3">
          {editing ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleDelete}
              className="text-[var(--c-red)] hover:bg-[var(--c-red)]/10 hover:text-[var(--c-red)]"
            >
              Delete
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="sm"
            onClick={handleCancel}
            className="ml-auto"
          >
            Cancel
          </Button>
          <Button size="sm" onClick={handleSave}>
            Save
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
