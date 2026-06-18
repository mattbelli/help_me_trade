import type { ChannelId } from "./channels";

export type { ChannelId };

export type WidgetInstanceConfig = {
  channel?: ChannelId;
  symbol?: string;
  timeframe?: string;
  style?: "candle" | "line";
  watchlistId?: string;
};

export type WorkspaceLayoutItem = {
  i: string;
  type: string;
  x: number;
  y: number;
  w: number;
  h: number;
  minW?: number;
  minH?: number;
  config?: WidgetInstanceConfig;
};

export interface LayoutPersistence {
  load(): WorkspaceLayoutItem[] | null;
  save(layout: WorkspaceLayoutItem[]): void;
}

const STORAGE_KEY = "hmt-workspace-layout-v2";

export const DEFAULT_WORKSPACE_LAYOUT: WorkspaceLayoutItem[] = [
  {
    i: "account-summary",
    type: "account-summary",
    x: 0,
    y: 0,
    w: 24,
    h: 3,
    minW: 6,
    minH: 2,
  },
  {
    i: "open-orders",
    type: "open-orders",
    x: 0,
    y: 3,
    w: 12,
    h: 5,
    minW: 5,
    minH: 3,
  },
  {
    i: "positions",
    type: "positions",
    x: 12,
    y: 3,
    w: 12,
    h: 5,
    minW: 5,
    minH: 3,
  },
  {
    i: "watchlist-1",
    type: "watchlist",
    x: 0,
    y: 8,
    w: 8,
    h: 6,
    minW: 4,
    minH: 3,
    config: { channel: "1" },
  },
  {
    i: "chart-1",
    type: "chart",
    x: 8,
    y: 8,
    w: 16,
    h: 6,
    minW: 6,
    minH: 4,
    config: { channel: "1", timeframe: "1Day", style: "candle" },
  },
  {
    i: "equity-curve",
    type: "equity-curve",
    x: 0,
    y: 14,
    w: 12,
    h: 5,
    minW: 5,
    minH: 3,
  },
];

function isValidLayoutItem(item: unknown): item is WorkspaceLayoutItem {
  if (!item || typeof item !== "object") return false;
  const o = item as Record<string, unknown>;
  return (
    typeof o.i === "string" &&
    typeof o.type === "string" &&
    typeof o.x === "number" &&
    typeof o.y === "number" &&
    typeof o.w === "number" &&
    typeof o.h === "number"
  );
}

export const localStorageLayoutPersistence: LayoutPersistence = {
  load() {
    if (typeof window === "undefined") return null;
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as unknown[];
      if (!Array.isArray(parsed) || parsed.length === 0) return null;
      const valid = parsed.filter(isValidLayoutItem);
      return valid.length > 0 ? valid : null;
    } catch {
      return null;
    }
  },
  save(layout) {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  },
};
