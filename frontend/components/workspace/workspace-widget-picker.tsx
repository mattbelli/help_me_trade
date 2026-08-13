"use client";

import { useState } from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import {
  getWidgetDefinition,
  WIDGET_REGISTRY,
} from "@/lib/workspace/widget-registry";

type Props = {
  onClose?: () => void;
};

export function WorkspaceWidgetPicker({ onClose }: Props) {
  const layout = useWorkspaceStore(
    (s) => s.pages[s.activePageId]?.layout ?? [],
  );
  const addWidget = useWorkspaceStore((s) => s.addWidget);
  const setPickerOpen = useWorkspaceStore((s) => s.setPickerOpen);

  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);

  const singletonTypesOnBoard = new Set(
    layout
      .filter((item) => !getWidgetDefinition(item.type)?.multiInstance)
      .map((item) => item.type),
  );

  const available = WIDGET_REGISTRY.filter(
    (w) => w.multiInstance || !singletonTypesOnBoard.has(w.id),
  );

  const q = query.trim().toLowerCase();
  const shown = q
    ? available.filter(
        (w) =>
          w.id.toLowerCase().includes(q) ||
          w.title.toLowerCase().includes(q) ||
          (w.description ?? "").toLowerCase().includes(q),
      )
    : available;

  // Clamp instead of resetting in render: the filter can shrink under the
  // current highlight.
  const active = shown.length ? Math.min(activeIndex, shown.length - 1) : -1;

  function close() {
    setPickerOpen(false);
    onClose?.();
  }

  function select(id: string) {
    addWidget(id);
    close();
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-[var(--scrim)] p-6 pt-[12vh]"
      onClick={close}
      onKeyDown={(e) => {
        if (e.key === "Escape") close();
      }}
      role="presentation"
    >
      <div
        className="w-full max-w-md overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)] shadow-[var(--panel-shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Add a widget"
      >
        <div className="flex items-center justify-between border-b border-[var(--line-divider)] px-3 py-2.5">
          <p className="font-mono text-[length:var(--fs-title)] font-semibold text-foreground">
            Add a widget
          </p>
          <button
            type="button"
            onClick={close}
            className="rounded p-1 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
            aria-label="Close widget picker"
          >
            <X className="size-3.5" />
          </button>
        </div>
        <div className="border-b border-[var(--line-divider)] px-3 py-2">
          <input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActiveIndex(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setActiveIndex(Math.min(active + 1, shown.length - 1));
                return;
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setActiveIndex(Math.max(active - 1, 0));
                return;
              }
              if (e.key === "Enter" && active >= 0) {
                e.preventDefault();
                select(shown[active].id);
              }
            }}
            placeholder="Search widgets…"
            // eslint-disable-next-line jsx-a11y/no-autofocus
            autoFocus
            className="w-full bg-transparent font-mono text-[length:var(--fs-body)] text-foreground outline-none placeholder:text-[var(--fg-faint)]"
          />
        </div>
        <div className="max-h-72 overflow-y-auto p-1">
          {shown.length === 0 ? (
            <p className="px-2 py-6 text-center font-mono text-[length:var(--fs-body)] text-[var(--fg-dim)]">
              No widgets.
            </p>
          ) : null}
          {shown.map((widget, i) => (
            <button
              key={widget.id}
              type="button"
              onClick={() => select(widget.id)}
              onMouseEnter={() => setActiveIndex(i)}
              // Keyboard highlight scrolls into view as arrows move it.
              ref={
                i === active
                  ? (el) => el?.scrollIntoView({ block: "nearest" })
                  : undefined
              }
              className={cn(
                "flex w-full flex-col items-start gap-0.5 rounded-sm px-2 py-2 text-left transition-colors",
                i === active && "bg-[var(--bg-hover)]",
              )}
            >
              <span className="font-mono text-[length:var(--fs-title)] font-semibold text-foreground">
                {widget.title}
              </span>
              {widget.description ? (
                <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
                  {widget.description}
                </span>
              ) : null}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
