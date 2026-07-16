import { apiFetch, API_BASE } from "@/lib/api";

// ── Types ──────────────────────────────────────────────────────────────────────

export type OrderFlowCall = {
  post_id: string;
  date: string;
  direction: string;
  level: number;
  points_net: number | null;
  exit_reason: string | null;
  has_mbo: boolean;
};

export type OrderFlowMeta = {
  post_id: string;
  symbol: string;
  post_ts: number; // epoch ms UTC
  entry_ts: number | null;
  exit_ts: number | null;
  exit_px: number | null;
  level: number;
  direction: string;
  points_net: number | null;
  exit_reason: string | null;
  window_start: number;
  window_end: number;
  tz: string;
};

export type BookEntry = {
  price: number;
  size: number;
};

export type BookSnapshot = {
  t: number; // epoch ms UTC
  bids: BookEntry[]; // sorted descending — best bid (highest price) first
  asks: BookEntry[]; // sorted ascending — best ask (lowest price) first
};

export type BookData = {
  snapshots: BookSnapshot[];
};

export type TapeEvent = {
  t: number; // epoch ms UTC
  action: string; // A=Add C=Cancel M=Modify T=Trade F=Fill
  side: string; // B=Bid A=Ask N=Neutral
  price: number;
  size: number;
  order_id: number;
};

export type TapeData = {
  events: TapeEvent[];
};

export type TradeEvent = {
  t: number; // epoch ms UTC
  price: number;
  size: number;
  side: string; // B = buy aggressor (hit ask), A = sell aggressor (hit bid)
};

// ── ET formatters (safe in both Node + browser — Intl is universal) ───────────

const ET_DATETIME = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const ET_TIME = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function formatET(epochMs: number | null | undefined): string {
  if (epochMs == null) return "—";
  return ET_DATETIME.format(new Date(epochMs));
}

export function formatTimeET(epochMs: number | null | undefined): string {
  if (epochMs == null) return "—";
  return ET_TIME.format(new Date(epochMs));
}

/** Like formatTimeET but includes milliseconds: "HH:MM:SS.mmm" — used in the tape where many MBO events share the same second. */
export function formatTimeMsET(epochMs: number | null | undefined): string {
  if (epochMs == null) return "—";
  const ms = epochMs % 1000;
  return `${ET_TIME.format(new Date(epochMs))}.${String(ms).padStart(3, "0")}`;
}

// ── API functions — reuse apiFetch (attaches X-API-Token, timeout) ────────────

export async function fetchOrderFlowCalls(): Promise<OrderFlowCall[]> {
  const res = await apiFetch(`${API_BASE}/api/orderflow/calls`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ??
        `OrderFlow calls error ${res.status}`,
    );
  }
  const data = await res.json();
  return data.calls ?? [];
}

export async function fetchOrderFlowMeta(postId: string): Promise<OrderFlowMeta> {
  const res = await apiFetch(
    `${API_BASE}/api/orderflow/${encodeURIComponent(postId)}/meta`,
    { cache: "no-store" },
    90_000,
  );
  if (!res.ok) throw new Error(`OrderFlow meta error ${res.status}`);
  return res.json();
}

export async function fetchOrderFlowBook(postId: string): Promise<BookData> {
  // Book reconstruction can take ~20 s on the largest parquet; 90 s matches candles/footprint.
  const res = await apiFetch(
    `${API_BASE}/api/orderflow/${encodeURIComponent(postId)}/book`,
    { cache: "no-store" },
    90_000,
  );
  if (!res.ok) throw new Error(`OrderFlow book error ${res.status}`);
  return res.json();
}

export async function fetchOrderFlowTape(
  postId: string,
  limit = 500,
): Promise<TapeData> {
  const res = await apiFetch(
    `${API_BASE}/api/orderflow/${encodeURIComponent(postId)}/tape?limit=${limit}`,
    { cache: "no-store" },
    90_000,
  );
  if (!res.ok) throw new Error(`OrderFlow tape error ${res.status}`);
  return res.json();
}

// ── Candle + Footprint types ──────────────────────────────────────────────────

export type CandleBar = {
  t: number; // epoch ms UTC
  o: number | null;
  h: number | null;
  l: number | null;
  c: number;
};

export type CandleData = {
  bars: CandleBar[];
};

export type FootprintCell = {
  price: number;
  bidVol: number;
  askVol: number;
};

export type FootprintBar = {
  t: number; // epoch ms UTC
  cells: FootprintCell[];
  delta: number;
  cumDelta: number;
};

export type FootprintData = {
  bars: FootprintBar[];
};

export async function fetchOrderFlowCandles(
  postId: string,
  bar = "30s",
): Promise<CandleData> {
  const res = await apiFetch(
    `${API_BASE}/api/orderflow/${encodeURIComponent(postId)}/candles?bar=${bar}`,
    { cache: "no-store" },
    90_000,
  );
  if (!res.ok) throw new Error(`OrderFlow candles error ${res.status}`);
  return res.json();
}

export async function fetchOrderFlowTrades(
  postId: string,
  fromMs?: number,
  toMs?: number,
): Promise<TradeEvent[]> {
  const params = new URLSearchParams();
  if (fromMs != null) params.set("from", String(fromMs));
  if (toMs != null) params.set("to", String(toMs));
  const qs = params.toString();
  const url =
    `${API_BASE}/api/orderflow/${encodeURIComponent(postId)}/trades` +
    (qs ? `?${qs}` : "");
  const res = await apiFetch(url, { cache: "no-store" }, 90_000);
  if (!res.ok) throw new Error(`OrderFlow trades error ${res.status}`);
  const data = await res.json();
  return data.trades ?? [];
}

export async function fetchOrderFlowFootprint(
  postId: string,
  bar = "30s",
  tick = 0.25,
): Promise<FootprintData> {
  const res = await apiFetch(
    `${API_BASE}/api/orderflow/${encodeURIComponent(postId)}/footprint?bar=${bar}&tick=${tick}`,
    { cache: "no-store" },
    90_000,
  );
  if (!res.ok) throw new Error(`OrderFlow footprint error ${res.status}`);
  return res.json();
}

/** Fetch tape events in a specific time window (for rolling window around cursor). */
export async function fetchOrderFlowTapeWindow(
  postId: string,
  fromMs: number,
  toMs: number,
  limit = 300,
): Promise<TapeData> {
  const res = await apiFetch(
    `${API_BASE}/api/orderflow/${encodeURIComponent(postId)}/tape?from=${fromMs}&to=${toMs}&limit=${limit}`,
    { cache: "no-store" },
    30_000,
  );
  if (!res.ok) throw new Error(`OrderFlow tape window error ${res.status}`);
  return res.json();
}

/**
 * Fetch fine-grained book snapshots (default 1s resolution) for a time window.
 * Used to drive the DOM ladder at ~1s granularity while scrubbing.
 * Note: uses a 120s timeout — book reconstruction is slow on large parquets.
 */
export async function fetchOrderFlowBookFine(
  postId: string,
  fromMs: number,
  toMs: number,
  res = "1s",
): Promise<BookData> {
  const url =
    `${API_BASE}/api/orderflow/${encodeURIComponent(postId)}/book` +
    `?res=${res}&from=${fromMs}&to=${toMs}`;
  const response = await apiFetch(url, { cache: "no-store" }, 120_000);
  if (!response.ok) throw new Error(`OrderFlow book fine error ${response.status}`);
  return response.json();
}
