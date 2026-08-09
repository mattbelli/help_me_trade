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
  /** Watchlist: ☰ → Edit list — rows show an always-visible remove ×. */
  editing?: boolean;
  /** Watchlist: header ⚙ toggles the columns & display settings overlay panel. */
  settingsOpen?: boolean;
  /** Watchlist: header ↗ toggles the floating aggregate-return panel. */
  aggregateOpen?: boolean;
  /** Chart studies toggled from the frame toolbar's Studies dropdown. */
  showSessionNodes?: boolean;
  studies?: string[];
  /** Chart: ETH (extended-hours) toggle — pre/post candles + session shading.
      Undefined = default ON for intraday; hidden/ignored on daily+ timeframes. */
  extendedHours?: boolean;
  /** Chart eye menu — visibility toggles. Undefined = shown. */
  showAlerts?: boolean;
  showDrawings?: boolean;
  /** Native chart: tag column (level names/bells) — hiding it hides all
      overlay lines with it (one-click clean chart). Undefined = shown. */
  showTagColumn?: boolean;
  /** Options chain: strike-count window around the money (6..40 | "all"). */
  strikes?: number | "all";
  /** Options flow: table sort column + direction. */
  flowSort?: {
    key: "score" | "time" | "size" | "premium";
    dir: "asc" | "desc";
  };
  /** Options flow: event-type filter (⚙). Undefined = the directional tape
      default (sweeps/blocks/pressure) — the nightly unusual-volume EOD batch is
      off until explicitly checked, so it never buries the live tape. */
  flowTypes?: FlowTypeKey[];
  /** Index pressure: which session bookend the gauge shows. Undefined = open. */
  pressureWindow?: "open" | "close" | "overnight" | "trend";
  /** Notifications: ding on new card arrival. Undefined = on. */
  ding?: boolean;
  /** Notifications: header ⚙ filter — "all" | "unread" | a category. */
  notifFilter?: string;
  /** News: bloop on new headline arrival. Undefined = on. */
  bloop?: boolean;
  /** News: feed scope. Undefined = ticker when on a channel, else watchlist. */
  newsScope?: "ticker" | "watchlist" | "sp500" | "all" | "filings";
};

/** Options-flow event kinds, filterable from the widget's ⚙ menu. */
export type FlowTypeKey =
  | "sweep"
  | "block"
  | "flow_burst"
  | "flow_cluster"
  | "directional_pressure"
  | "unusual_volume";

export const DEFAULT_FLOW_TYPES: FlowTypeKey[] = [
  "sweep",
  "block",
  "flow_burst",
  "flow_cluster",
  "directional_pressure",
];

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

/** A named group of pages — one cockpit arrangement (e.g. widescreen vs laptop).
    Pages are LINKED, not owned: `pageIds` reference the shared page library, so
    the same page in two workspaces is one object — edits apply everywhere. */
export type WorkspaceSnapshot = {
  id: string;
  name: string;
  pageIds: string[];
  activePageId: string;
};

export type WorkspacesState = {
  version: 2;
  /** The page library — every saved page, whether or not any workspace uses it. */
  pages: Record<string, WorkspacePage>;
  workspaces: Record<string, WorkspaceSnapshot>;
  defaultWorkspaceId: string;
};

/** v1 (2026-07-14, lived one night): each workspace embedded its own pages_state. */
type WorkspaceSnapshotV1 = {
  id: string;
  name: string;
  pages_state: WorkspacePagesState;
};

type WorkspacesStateV1 = {
  workspaces: Record<string, WorkspaceSnapshotV1>;
  defaultWorkspaceId: string;
};

export const MAX_WORKSPACES = 8;
export const MAX_PAGES_PER_WORKSPACE = 5;
export const MAX_LIBRARY_PAGES = 24;
export const DEFAULT_WORKSPACE_ID = "ws-default";

export interface LayoutPersistence {
  load(): WorkspaceLayoutItem[] | null;
  save(layout: WorkspaceLayoutItem[]): void;
}

const STORAGE_KEY_V2 = "hmt-workspace-layout-v2";
const STORAGE_KEY_V3 = "hmt-workspace-v3";

export const DEFAULT_CANVAS_PAGE_ID = "canvas";

export const DEFAULT_WORKSPACE_LAYOUT: WorkspaceLayoutItem[] = [
  // Positions carries the account-summary strip since the 2026-08-09 widget
  // consolidation; the orders blotter (Open tab) replaced open-orders.
  {
    i: "orders",
    type: "orders",
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

const WORKSPACES_STORAGE_KEY_V1 = "hmt-workspaces-v1";
const WORKSPACES_STORAGE_KEY = "hmt-workspaces-v2";
// Per-DEVICE choice: which workspace this browser shows. Deliberately NOT on
// the server — the widescreen and the laptop each remember their own.
const ACTIVE_WORKSPACE_KEY = "hmt-active-workspace";

function isValidPage(raw: unknown): raw is WorkspacePage {
  if (!raw || typeof raw !== "object") return false;
  const p = raw as Record<string, unknown>;
  return (
    typeof p.id === "string" &&
    typeof p.name === "string" &&
    Array.isArray(p.layout) &&
    p.layout.every(isValidLayoutItem)
  );
}

export function isValidWorkspacesState(raw: unknown): raw is WorkspacesState {
  if (!raw || typeof raw !== "object") return false;
  const o = raw as Record<string, unknown>;
  if (o.version !== 2) return false;
  const pages = o.pages;
  const workspaces = o.workspaces;
  if (
    !pages ||
    typeof pages !== "object" ||
    !workspaces ||
    typeof workspaces !== "object" ||
    typeof o.defaultWorkspaceId !== "string"
  ) {
    return false;
  }
  const library = pages as Record<string, unknown>;
  const libEntries = Object.values(library);
  if (libEntries.length === 0 || libEntries.length > MAX_LIBRARY_PAGES) {
    return false;
  }
  if (!libEntries.every(isValidPage)) return false;

  const wsEntries = Object.values(workspaces as Record<string, unknown>);
  if (wsEntries.length === 0 || wsEntries.length > MAX_WORKSPACES) return false;
  for (const ws of wsEntries) {
    if (!ws || typeof ws !== "object") return false;
    const w = ws as Record<string, unknown>;
    if (typeof w.id !== "string" || typeof w.name !== "string") return false;
    const pageIds = w.pageIds;
    if (!Array.isArray(pageIds) || pageIds.length === 0) return false;
    if (pageIds.length > MAX_PAGES_PER_WORKSPACE) return false;
    if (!pageIds.every((id) => typeof id === "string" && id in library)) {
      return false;
    }
    if (new Set(pageIds).size !== pageIds.length) return false;
    if (typeof w.activePageId !== "string" || !pageIds.includes(w.activePageId)) {
      return false;
    }
  }
  return o.defaultWorkspaceId in (workspaces as Record<string, unknown>);
}

function isValidWorkspacesStateV1(raw: unknown): raw is WorkspacesStateV1 {
  if (!raw || typeof raw !== "object") return false;
  const o = raw as Record<string, unknown>;
  if ("version" in o) return false;
  const workspaces = o.workspaces;
  if (
    !workspaces ||
    typeof workspaces !== "object" ||
    typeof o.defaultWorkspaceId !== "string"
  ) {
    return false;
  }
  const entries = Object.values(workspaces as Record<string, unknown>);
  if (entries.length === 0 || entries.length > MAX_WORKSPACES) return false;
  for (const ws of entries) {
    if (!ws || typeof ws !== "object") return false;
    const w = ws as Record<string, unknown>;
    if (typeof w.id !== "string" || typeof w.name !== "string") return false;
    if (!isValidPagesState(w.pages_state)) return false;
  }
  return o.defaultWorkspaceId in (workspaces as Record<string, unknown>);
}

/** Hoist v1 embedded pages into the shared library. Original page ids survive
    where unique; collisions across workspaces get fresh `pg-N` ids. */
export function migrateWorkspacesV1(v1: WorkspacesStateV1): WorkspacesState {
  const library: Record<string, WorkspacePage> = {};
  const workspaces: Record<string, WorkspaceSnapshot> = {};
  let counter = 1;
  for (const ws of Object.values(v1.workspaces)) {
    const idMap: Record<string, string> = {};
    for (const [oldId, page] of Object.entries(ws.pages_state.pages)) {
      let newId = oldId;
      while (newId in library) {
        newId = `pg-${counter}`;
        counter += 1;
      }
      idMap[oldId] = newId;
      library[newId] = { ...page, id: newId };
    }
    const pageIds = Object.keys(ws.pages_state.pages).map((id) => idMap[id]);
    const activePageId =
      idMap[ws.pages_state.activePageId] ?? pageIds[0];
    workspaces[ws.id] = { id: ws.id, name: ws.name, pageIds, activePageId };
  }
  return {
    version: 2,
    pages: library,
    workspaces,
    defaultWorkspaceId: v1.defaultWorkspaceId,
  };
}

export function wrapPagesAsWorkspacesState(
  pagesState: WorkspacePagesState,
): WorkspacesState {
  return migrateWorkspacesV1({
    workspaces: {
      [DEFAULT_WORKSPACE_ID]: {
        id: DEFAULT_WORKSPACE_ID,
        name: "Main",
        pages_state: pagesState,
      },
    },
    defaultWorkspaceId: DEFAULT_WORKSPACE_ID,
  });
}

/** Resolve a workspace's live pages view: an ORDERED record following pageIds
    (Object.keys order drives the page pills). Unknown refs are dropped. */
export function pagesStateForWorkspace(
  state: WorkspacesState,
  workspaceId: string,
): WorkspacePagesState | null {
  const ws = state.workspaces[workspaceId];
  if (!ws) return null;
  const pages: Record<string, WorkspacePage> = {};
  for (const id of ws.pageIds) {
    const page = state.pages[id];
    if (page) pages[id] = page;
  }
  if (Object.keys(pages).length === 0) return null;
  const activePageId =
    ws.activePageId in pages ? ws.activePageId : Object.keys(pages)[0];
  return { pages, activePageId };
}

export const localStorageWorkspacesPersistence = {
  load(): WorkspacesState | null {
    if (typeof window === "undefined") return null;
    try {
      const raw = window.localStorage.getItem(WORKSPACES_STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as unknown;
        if (isValidWorkspacesState(parsed)) return parsed;
      }
      // One-time migration from the v1 cache (shipped the night before v2).
      const rawV1 = window.localStorage.getItem(WORKSPACES_STORAGE_KEY_V1);
      if (rawV1) {
        const parsedV1 = JSON.parse(rawV1) as unknown;
        if (isValidWorkspacesStateV1(parsedV1)) {
          return migrateWorkspacesV1(parsedV1);
        }
      }
      return null;
    } catch {
      return null;
    }
  },
  save(state: WorkspacesState): void {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(WORKSPACES_STORAGE_KEY, JSON.stringify(state));
  },
};

export function loadActiveWorkspaceId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(ACTIVE_WORKSPACE_KEY);
  } catch {
    return null;
  }
}

export function saveActiveWorkspaceId(id: string): void {
  try {
    window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, id);
  } catch {
    // ignore quota / private mode
  }
}

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
