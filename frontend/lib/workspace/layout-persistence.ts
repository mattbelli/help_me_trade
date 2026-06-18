export type WorkspaceLayoutItem = {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
  minW?: number;
  minH?: number;
};

export interface LayoutPersistence {
  load(): WorkspaceLayoutItem[] | null;
  save(layout: WorkspaceLayoutItem[]): void;
}

const STORAGE_KEY = "hmt-workspace-layout";

export const DEFAULT_WORKSPACE_LAYOUT: WorkspaceLayoutItem[] = [
  { i: "account-summary", x: 0, y: 0, w: 12, h: 3, minW: 4, minH: 2 },
  { i: "open-orders", x: 0, y: 3, w: 6, h: 5, minW: 4, minH: 3 },
  { i: "positions", x: 6, y: 3, w: 6, h: 5, minW: 4, minH: 3 },
  { i: "equity-curve", x: 0, y: 8, w: 6, h: 5, minW: 4, minH: 3 },
  { i: "watchlist", x: 6, y: 8, w: 6, h: 5, minW: 4, minH: 3 },
];

export const localStorageLayoutPersistence: LayoutPersistence = {
  load() {
    if (typeof window === "undefined") return null;
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as WorkspaceLayoutItem[];
      if (!Array.isArray(parsed) || parsed.length === 0) return null;
      return parsed;
    } catch {
      return null;
    }
  },
  save(layout) {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  },
};
