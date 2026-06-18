"use client";

import { X } from "lucide-react";

import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import { WIDGET_REGISTRY } from "@/lib/workspace/widget-registry";

type Props = {
  onClose?: () => void;
};

export function WorkspaceWidgetPicker({ onClose }: Props) {
  const layout = useWorkspaceStore((s) => s.layout);
  const addWidget = useWorkspaceStore((s) => s.addWidget);
  const setPickerOpen = useWorkspaceStore((s) => s.setPickerOpen);

  const onBoard = new Set(layout.map((item) => item.i));
  const available = WIDGET_REGISTRY.filter((w) => !onBoard.has(w.id));

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
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/50 p-6 pt-[12vh]"
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
        <div className="flex items-center justify-between border-b border-[var(--border)] px-3 py-2.5">
          <p className="font-mono text-[11.5px] font-semibold text-foreground">
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
        <Command className="rounded-none border-0 bg-transparent">
          <CommandInput
            placeholder="Search widgets…"
            className="font-mono text-xs"
          />
          <CommandList className="max-h-72">
            <CommandEmpty>No widgets available.</CommandEmpty>
            <CommandGroup>
              {available.map((widget) => (
                <CommandItem
                  key={widget.id}
                  value={`${widget.id} ${widget.title} ${widget.description ?? ""}`}
                  onSelect={() => select(widget.id)}
                  className="flex flex-col items-start gap-0.5 py-2"
                >
                  <span className="font-mono text-[12px] font-semibold text-foreground">
                    {widget.title}
                  </span>
                  {widget.description ? (
                    <span className="font-mono text-[10.5px] text-[var(--fg-dim)]">
                      {widget.description}
                    </span>
                  ) : null}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </div>
    </div>
  );
}
