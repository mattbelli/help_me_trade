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

  function renderMemberRow({ var: cssVar, label, desc }: ThemeColorMember) {
    const value = vars[cssVar] ?? "";
    return (
      <div key={cssVar} className="flex items-center gap-2 pl-5">
        {/* subline = what the var drives; the raw var name lives in the tooltip */}
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
        className="-mx-4 -mt-4 flex cursor-grab touch-none items-start gap-2 rounded-t-xl border-b border-[var(--border)] bg-[var(--bg-inset)]/50 px-4 py-2.5 active:cursor-grabbing"
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

        <div className="flex max-h-[min(420px,55vh)] flex-col gap-1.5 overflow-y-auto pr-1">
          {THEME_COLOR_ROWS.map((row) => {
            if (row.kind === "single") {
              const value = vars[row.var] ?? "";
              return (
                <div key={row.var} className="flex items-center gap-2">
                  <span className="w-2 shrink-0" />
                  <span
                    className="w-24 shrink-0 flex-col font-mono leading-tight"
                    title={row.var}
                  >
                    <span className="block text-[length:var(--fs-micro)] tracking-[0.08em] uppercase text-[var(--fg-faint)]">
                      {row.label}
                    </span>
                    {row.desc ? (
                      <span className="block text-[length:var(--fs-micro)] normal-case text-[var(--fg-faint)]/70">
                        {row.desc}
                      </span>
                    ) : null}
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
    </motion.div>,
    document.body,
  );
}
