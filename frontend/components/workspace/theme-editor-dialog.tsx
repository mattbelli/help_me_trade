"use client";

import { motion } from "motion/react";
import { XIcon } from "lucide-react";
import { useTheme } from "next-themes";
import { createPortal } from "react-dom";
import { useEffect, useMemo, useRef, useState } from "react";

import { TerminalButton, TerminalInput } from "@/components/ui/terminal";
import { motionStd, useMotionSafe } from "@/lib/workspace/motion";
import {
  applyInlinePreview,
  captureThemeVars,
  clearInlinePreview,
  flushInlinePreview,
  isHexColor,
  normalizeHex,
  resolveEditorPreviewIsDark,
  resolveThemeIsDark,
  syncHtmlDarkClass,
  THEME_SECTIONS,
  type ThemeKnob,
} from "@/lib/workspace/custom-themes";
import {
  isBuiltinTheme,
  WORKSPACE_THEME_LABELS,
  type WorkspaceTheme,
} from "@/lib/workspace/themes";
import { useCustomThemeStore } from "@/lib/workspace/use-custom-theme-store";

const PANEL_WIDTH = 416; // w-[26rem]

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
  // ONE working knob-var map: each row writes one var, last write wins. Saved
  // verbatim; every legacy/shadcn var derives from these in globals.css.
  const [vars, setVars] = useState<Record<string, string>>({});
  const [cloneBase, setCloneBase] = useState<string>("carbon");

  // Floating-window position (session-only, never persisted). null until the
  // window opens; seeded to the right edge below the toolbar.
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const dragOffset = useRef<{ dx: number; dy: number } | null>(null);
  const transition = useMotionSafe(motionStd);

  // Seed when a fresh editor session opens (render-time adjust on prop change —
  // the React-endorsed "store previous value in state" pattern, not an effect).
  const sessionKey = open ? `${editingId ?? "clone"}` : null;
  const [seededKey, setSeededKey] = useState<string | null>(null);
  if (seededKey !== sessionKey) {
    setSeededKey(sessionKey);
    if (sessionKey !== null) {
      // Default: right side of the viewport, just under the toolbar.
      setPos({ left: Math.max(8, window.innerWidth - PANEL_WIDTH - 24), top: 56 });
    }
    if (sessionKey === null) {
      // closed — nothing to seed
    } else if (editing) {
      setName(editing.name);
      setVars({ ...editing.vars });
      setCloneBase(editing.base);
    } else {
      // Clone-current mode: snapshot the resolved active theme.
      setName("");
      setVars(captureThemeVars());
      setCloneBase(theme ?? "carbon");
    }
  }

  // Paint the live preview whenever the editor is open with seeded vars.
  useEffect(() => {
    if (!open || Object.keys(vars).length === 0) return;
    applyInlinePreview(vars);
    syncHtmlDarkClass(resolveEditorPreviewIsDark(cloneBase, vars));
    return () => {
      syncHtmlDarkClass(resolveThemeIsDark(theme, themes));
    };
  }, [open, vars, cloneBase, theme, themes]);

  function handleVarChange(cssVar: string, raw: string) {
    const next = { ...vars, [cssVar]: raw };
    setVars(next);
    applyInlinePreview(next);
  }

  function handleCancel() {
    clearInlinePreview();
    onOpenChange(false);
  }

  // Title-bar drag: pointer-capture so move/up fire even off the header;
  // clamp so the window (and its title bar) can never be lost off-screen.
  function handleDragStart(e: React.PointerEvent) {
    if (!pos) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragOffset.current = { dx: e.clientX - pos.left, dy: e.clientY - pos.top };
  }

  function handleDragMove(e: React.PointerEvent) {
    if (!dragOffset.current) return;
    const w = panelRef.current?.offsetWidth ?? PANEL_WIDTH;
    const left = Math.min(
      Math.max(8, e.clientX - dragOffset.current.dx),
      window.innerWidth - w - 8,
    );
    const top = Math.min(
      Math.max(8, e.clientY - dragOffset.current.dy),
      window.innerHeight - 40,
    );
    setPos({ left, top });
  }

  function handleDragEnd(e: React.PointerEvent) {
    dragOffset.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
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
      // Flush any rAF-pending preview first so the last edit is in the DOM.
      flushInlinePreview();
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

  function renderKnob({ var: cssVar, label, desc }: ThemeKnob) {
    const value = vars[cssVar] ?? "";
    return (
      <div key={cssVar} className="flex items-center gap-2 pl-2">
        {/* subline = what the knob drives; the raw var name lives in the tooltip */}
        <div className="flex w-32 shrink-0 flex-col leading-tight" title={cssVar}>
          <span className="font-mono text-[length:var(--fs-table)] text-foreground">{label}</span>
          {desc ? (
            <span className="font-mono text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
              {desc}
            </span>
          ) : null}
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

  if (!open || !pos) return null;

  return createPortal(
    <motion.div
      ref={panelRef}
      role="dialog"
      aria-label={editing ? "Edit theme" : "New theme"}
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={transition}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          handleCancel();
        }
      }}
      style={{ left: pos.left, top: pos.top, width: PANEL_WIDTH }}
      className="fixed z-[65] flex flex-col gap-4 rounded-xl border border-[var(--border)] bg-[var(--panel)] p-4 font-mono text-[length:var(--fs-body)] shadow-[var(--panel-shadow)]"
    >
      <div
        onPointerDown={handleDragStart}
        onPointerMove={handleDragMove}
        onPointerUp={handleDragEnd}
        onPointerCancel={handleDragEnd}
        className="-mx-4 -mt-4 flex cursor-grab touch-none items-start gap-2 rounded-t-xl border-b border-[var(--line-divider)] bg-[var(--bg-inset)]/50 px-4 py-2.5 active:cursor-grabbing"
      >
        <div className="flex flex-col gap-1">
          <span className="font-mono text-[length:var(--fs-title)] tracking-[0.08em] uppercase text-foreground">
            {editing ? "Edit theme" : "New theme"}
          </span>
          <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
            Cloned from {baseLabel(cloneBase, editing?.name)}
          </span>
        </div>
        <button
          type="button"
          aria-label="Close"
          onClick={handleCancel}
          onPointerDown={(e) => e.stopPropagation()}
          className="ml-auto rounded p-0.5 text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground"
        >
          <XIcon className="size-3.5" />
        </button>
      </div>

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

        <div className="flex max-h-[min(420px,55vh)] flex-col gap-3 overflow-y-auto pr-1">
          {THEME_SECTIONS.map((section) => (
            <div key={section.title} className="flex flex-col gap-1.5">
              <span className="font-mono text-[length:var(--fs-micro)] tracking-[0.12em] uppercase text-[var(--fg-faint)]">
                {section.title}
              </span>
              {section.knobs.map(renderKnob)}
            </div>
          ))}
        </div>

        <div className="-mx-4 -mb-4 flex items-center gap-2 rounded-b-xl border-t border-[var(--line-divider)] bg-[var(--bg-inset)]/50 p-3">
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
    </motion.div>,
    document.body,
  );
}
