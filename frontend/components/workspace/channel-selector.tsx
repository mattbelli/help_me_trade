"use client";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { channelColor, CHANNELS, type ChannelId } from "@/lib/workspace/channels";
import { cn } from "@/lib/utils";

type Props = {
  channel: ChannelId;
  onChange: (channel: ChannelId) => void;
};

export function ChannelSelector({ channel, onChange }: Props) {
  const activeColor = channelColor(channel);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        type="button"
        className={cn(
          "workspace-no-drag size-2.5 shrink-0 rounded-sm border border-[var(--border)] outline-none transition-opacity",
          channel === "none" && "opacity-50",
        )}
        style={
          channel !== "none" ? { backgroundColor: activeColor } : undefined
        }
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
        aria-label="Select link channel"
      />
      <DropdownMenuContent
        align="end"
        className="min-w-36 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[11px] shadow-[var(--panel-shadow)]"
      >
        {CHANNELS.map((ch) => (
          <DropdownMenuItem
            key={ch.id}
            onClick={() => onChange(ch.id)}
            className="flex items-center gap-2 px-2 py-1.5"
          >
            <span
              className={cn(
                "size-2 shrink-0 rounded-full",
                ch.id === "none" && "border border-[var(--border)]",
              )}
              style={{ backgroundColor: ch.color }}
            />
            <span
              className={
                channel === ch.id ? "text-foreground" : "text-[var(--fg-dim)]"
              }
            >
              {ch.label}
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
