"use client";

import { ChevronDown } from "lucide-react";

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

function channelLabel(channel: ChannelId): string {
  if (channel === "none") return "link";
  return `CH ${channel}`;
}

export function ChannelSelector({ channel, onChange }: Props) {
  const activeColor = channelColor(channel);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        type="button"
        className={cn(
          "inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] outline-none transition-colors",
          channel === "none"
            ? "border-[var(--border)] text-[var(--fg-faint)] hover:border-[var(--border-strong)] hover:bg-[var(--bg-hover)] hover:text-foreground"
            : "border-transparent text-foreground hover:brightness-110",
        )}
        style={
          channel !== "none"
            ? { backgroundColor: `color-mix(in srgb, ${activeColor} 22%, transparent)` }
            : undefined
        }
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
        aria-label="Select link channel"
      >
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            channel === "none" && "border border-[var(--border)] bg-[var(--bg-inset)]",
          )}
          style={channel !== "none" ? { backgroundColor: activeColor } : undefined}
        />
        <span className="leading-none">{channelLabel(channel)}</span>
        <ChevronDown className="size-2.5 shrink-0 opacity-70" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="min-w-36 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[11px] shadow-[var(--panel-shadow)]"
      >
        {CHANNELS.map((ch) => (
          <DropdownMenuItem
            key={ch.id}
            onSelect={() => onChange(ch.id)}
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
