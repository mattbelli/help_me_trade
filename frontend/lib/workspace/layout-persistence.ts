import type { ChannelId } from "./channels";

export type { ChannelId };

export type WidgetInstanceConfig = {
  channel?: ChannelId;
  symbol?: string;
  timeframe?: string;
  style?: "candle" | "line";
  watchlistId?: string;
  /** Watchlist: header "+" toggles the inline add-symbol row at the top of the list. */
  adding?: boolean;
  /** Chart studies toggled from the frame toolbar's Studies dropdown. */
  showSessionNodes?: boolean;
  studies?: string[];
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

export type WorkspacePage = {
  id: string;
  name: string;
  layout: WorkspaceLayoutItem[];
};

export type WorkspacePagesState = {
  pages: Record<string, WorkspacePage>;
  activePageId: string;
};

export interface LayoutPersistence {
  load(): WorkspaceLayoutItem[] | null;
  save(layout: WorkspaceLayoutItem[]): void;
}

const STORAGE_KEY_V2 = "hmt-workspace-layout-v2";
const STORAGE_KEY_V3 = "hmt-workspace-v3";

export const DEFAULT_CANVAS_PAGE_ID = "canvas";

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
  {
    i: "notifications",
    type: "notifications",
    x: 12,
    y: 14,
    w: 8,
    h: 6,
    minW: 4,
    minH: 3,
  },
];

export function wrapLayoutAsPagesState(
  layout: WorkspaceLayoutItem[],
): WorkspacePagesState {
  return {
    pages: {
      [DEFAULT_CANVAS_PAGE_ID]: {
        id: DEFAULT_CANVAS_PAGE_ID,
        name: "Page 1",
        layout,
      },
    },
    activePageId: DEFAULT_CANVAS_PAGE_ID,
  };
}

export const DEFAULT_PAGES_STATE: WorkspacePagesState = wrapLayoutAsPagesState(
  DEFAULT_WORKSPACE_LAYOUT,
);

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

function isValidPagesState(raw: unknown): raw is WorkspacePagesState {
  if (!raw || typeof raw !== "object") return false;
  const o = raw as Record<string, unknown>;
  const pages = o.pages;
  const activePageId = o.activePageId;
  if (!pages || typeof pages !== "object" || typeof activePageId !== "string") {
    return false;
  }
  const pageEntries = Object.entries(pages as Record<string, unknown>);
  if (pageEntries.length === 0) return false;

  for (const [, page] of pageEntries) {
    if (!page || typeof page !== "object") return false;
    const p = page as Record<string, unknown>;
    if (typeof p.id !== "string" || typeof p.name !== "string") return false;
    if (!Array.isArray(p.layout)) return false;
    if (!p.layout.every(isValidLayoutItem)) return false;
  }

  if (!(activePageId in (pages as Record<string, unknown>))) {
    return false;
  }
  return true;
}

function normalizePagesState(state: WorkspacePagesState): WorkspacePagesState {
  const activePageId =
    state.activePageId in state.pages
      ? state.activePageId
      : Object.keys(state.pages)[0];
  return { ...state, activePageId };
}

/** One-time fallback reader for the pre-multi-page localStorage key. */
export function loadLegacyV2Layout(): WorkspaceLayoutItem[] | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY_V2);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown[];
    if (!Array.isArray(parsed) || parsed.length === 0) return null;
    const valid = parsed.filter(isValidLayoutItem);
    return valid.length > 0 ? valid : null;
  } catch {
    return null;
  }
}

export const localStoragePagesPersistence = {
  load(): WorkspacePagesState | null {
    if (typeof window === "undefined") return null;
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY_V3);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as unknown;
      if (!isValidPagesState(parsed)) return null;
      return normalizePagesState(parsed);
    } catch {
      return null;
    }
  },
  save(state: WorkspacePagesState): void {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(STORAGE_KEY_V3, JSON.stringify(state));
  },
};

/** @deprecated Legacy v2 cache — kept for one-time client fallback only. */
export const localStorageLayoutPersistence: LayoutPersistence = {
  load() {
    return loadLegacyV2Layout();
  },
  save(layout) {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(STORAGE_KEY_V2, JSON.stringify(layout));
  },
};
