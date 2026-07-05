"use client";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { StandInMark } from "@/components/workspace/stand-in-mark";
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
          "workspace-no-drag inline-flex shrink-0 items-center justify-center rounded p-1 outline-none transition-colors hover:bg-[var(--bg-hover)]",
        )}
        style={{
          color: channel !== "none" ? activeColor : "var(--fg-faint)",
        }}
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
        aria-label="Select link channel"
        title={
          channel !== "none" ? `Linked \u00b7 Ch ${channel}` : "Link channel"
        }
      >
        <StandInMark
          className={cn("size-4", channel === "none" && "opacity-70")}
        />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="min-w-36 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[length:var(--fs-body)] shadow-[var(--panel-shadow)]"
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
