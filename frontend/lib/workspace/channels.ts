export type ChannelId = "none" | "1" | "2" | "3" | "4" | "5" | "6";

export const CHANNELS: { id: ChannelId; label: string; color: string }[] = [
  { id: "none", label: "No link", color: "var(--fg-faint)" },
  { id: "1", label: "Channel 1", color: "var(--c-red)" },
  { id: "2", label: "Channel 2", color: "var(--c-amber)" },
  { id: "3", label: "Channel 3", color: "var(--c-yellow)" },
  { id: "4", label: "Channel 4", color: "var(--c-green)" },
  { id: "5", label: "Channel 5", color: "var(--c-blue)" },
  { id: "6", label: "Channel 6", color: "var(--c-violet)" },
];

export function channelColor(id: ChannelId): string {
  return CHANNELS.find((c) => c.id === id)?.color ?? "var(--fg-faint)";
}
