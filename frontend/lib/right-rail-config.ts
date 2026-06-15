export type RailPanelId = "watchlist" | "rss" | "insights";

export type RightRailConfig = {
  open: boolean;
  watchlistHeightPct: number;
  panels: Record<RailPanelId, boolean>;
};

export const DEFAULT_RAIL_CONFIG: RightRailConfig = {
  open: true,
  watchlistHeightPct: 62,
  panels: {
    watchlist: true,
    rss: false,
    insights: false,
  },
};

const STORAGE_KEY = "help-me-trade-right-rail";

export function loadRailConfig(): RightRailConfig {
  if (typeof window === "undefined") return DEFAULT_RAIL_CONFIG;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_RAIL_CONFIG;
    const parsed = JSON.parse(raw) as Partial<RightRailConfig>;
    return {
      open: parsed.open ?? DEFAULT_RAIL_CONFIG.open,
      watchlistHeightPct: clamp(
        parsed.watchlistHeightPct ?? DEFAULT_RAIL_CONFIG.watchlistHeightPct,
        28,
        82,
      ),
      panels: {
        ...DEFAULT_RAIL_CONFIG.panels,
        ...parsed.panels,
      },
    };
  } catch {
    return DEFAULT_RAIL_CONFIG;
  }
}

export function saveRailConfig(config: RightRailConfig) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
}

function clamp(n: number, min: number, max: number) {
  return Math.min(max, Math.max(min, n));
}
