"use client";

import { useMemo, type FC } from "react";
import type { Unstable_TriggerAdapter, Unstable_TriggerItem } from "@assistant-ui/core";
import { ComposerPrimitive, useAui } from "@assistant-ui/react";

import { CHAT_COMMANDS, findChatCommand } from "@/lib/chat-commands";
import type { ChatToolId } from "@/lib/chat-toolbar-config";
import { useChatSettings } from "@/lib/chat-settings-context";
import { cn } from "@/lib/utils";

function toTriggerItems(): Unstable_TriggerItem[] {
  return CHAT_COMMANDS.map((cmd) => ({
    id: cmd.id,
    type: "command",
    label: `/${cmd.label}`,
    description: cmd.description,
  }));
}

const slashAdapter: Unstable_TriggerAdapter = {
  categories: () => [{ id: "commands", label: "Commands" }],
  categoryItems: () => toTriggerItems(),
  search: (query) => {
    const q = query.trim().toLowerCase();
    const items = toTriggerItems();
    if (!q) return items;
    return items.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        item.description?.toLowerCase().includes(q),
    );
  },
};

export const ChatSlashCommands: FC = () => {
  const aui = useAui();
  const { setSettings } = useChatSettings();

  const onExecute = useMemo(
    () => (item: Unstable_TriggerItem) => {
      const cmd = findChatCommand(item.id);
      if (!cmd) return;
      aui.composer().setText(cmd.prompt);
      if (cmd.tools?.length) {
        setSettings((prev) => {
          const next = new Set<ChatToolId>(prev.enabledTools);
          for (const tool of cmd.tools ?? []) next.add(tool);
          return { ...prev, enabledTools: [...next] };
        });
      }
    },
    [aui, setSettings],
  );

  return (
    <ComposerPrimitive.Unstable_TriggerPopover
      char="/"
      adapter={slashAdapter}
      aria-label="Slash commands"
      className={cn(
        "bg-popover text-popover-foreground absolute bottom-full left-0 z-50 mb-2 w-full max-w-md",
        "overflow-hidden rounded-lg border border-border/60 shadow-lg",
      )}
    >
      <ComposerPrimitive.Unstable_TriggerPopover.Action
        onExecute={onExecute}
        removeOnExecute
      />
      <ComposerPrimitive.Unstable_TriggerPopoverItems className="max-h-56 overflow-y-auto p-1">
        {(items) =>
          items.map((item, index) => (
            <ComposerPrimitive.Unstable_TriggerPopoverItem
              key={item.id}
              item={item}
              index={index}
              className={cn(
                "flex w-full flex-col items-start gap-0.5 rounded-md px-2.5 py-2 text-left text-sm",
                "hover:bg-muted/80 data-highlighted:bg-muted",
              )}
            >
              <span className="font-medium">{item.label}</span>
              {item.description ? (
                <span className="text-muted-foreground text-xs">
                  {item.description}
                </span>
              ) : null}
            </ComposerPrimitive.Unstable_TriggerPopoverItem>
          ))
        }
      </ComposerPrimitive.Unstable_TriggerPopoverItems>
    </ComposerPrimitive.Unstable_TriggerPopover>
  );
};
