/**
 * Manual desk client — ticket / chart trading / DOM.
 *
 * Money verbs ride the ADMIN token (same sessionStorage key as desk
 * approvals); a 403 comes back as `{ status: "forbidden" }` so surfaces can
 * point at Settings → Execution / Risk instead of throwing. Every other
 * outcome (executed / blocked / rejected / …) passes through verbatim —
 * `reason` is the RiskEngine's own words and is rendered, never rewritten.
 */

import { API_BASE, apiFetch } from "@/lib/api";

export type ManualOrderArgs = {
  symbol: string;
  side: "buy" | "sell";
  qty: string;
  order_type: "market" | "limit";
  limit_price?: string;
  account_id?: string | null;
  time_in_force?: "day" | "gtc";
  extended_hours?: boolean;
};

export type ManualVerbResult = {
  status: string;
  reason?: string;
  execution_id?: string;
  proposal_id?: string;
  replaced_execution_id?: string;
  cancelled_orders?: string[];
  [k: string]: unknown;
};

export type ManualLimits = {
  execution_mode: string;
  kill_switch_active: boolean;
  entries_halted: boolean;
  market_open: boolean;
  orders_today: number;
  orders_today_account: number;
  max_daily_orders: number;
  max_open_orders: number;
  open_orders: number;
  max_order_value_usd: number;
  account_id: string | null;
  admin_unlocked: boolean;
};

function adminToken(): string {
  return typeof window !== "undefined"
    ? (sessionStorage.getItem("hmt-admin-token") ?? "")
    : "";
}

async function verb(
  path: string,
  body: Record<string, unknown>,
): Promise<ManualVerbResult> {
  // Per-call idempotency id: a network-layer retry replays the first outcome
  // server-side instead of double-placing.
  const requestId =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : undefined;
  const res = await apiFetch(
    `${API_BASE}${path}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, client_request_id: requestId }),
    },
    20_000,
    adminToken(),
  );
  if (res.status === 403) return { status: "forbidden" };
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const data = await res.json();
      if (data?.detail) detail = String(data.detail);
    } catch {
      // fall through with the status code
    }
    return { status: "error", reason: detail };
  }
  return res.json();
}

export function placeManualOrder(args: ManualOrderArgs): Promise<ManualVerbResult> {
  return verb("/api/manual/orders", args);
}

export function cancelManualOrder(executionId: string): Promise<ManualVerbResult> {
  return verb(`/api/manual/orders/${encodeURIComponent(executionId)}/cancel`, {});
}

export function replaceManualOrder(
  executionId: string,
  limitPrice: string,
): Promise<ManualVerbResult> {
  return verb(`/api/manual/orders/${encodeURIComponent(executionId)}/replace`, {
    limit_price: limitPrice,
  });
}

export function closeManualPosition(
  symbol: string,
  accountId?: string | null,
): Promise<ManualVerbResult> {
  return verb("/api/manual/positions/close", {
    symbol,
    account_id: accountId ?? null,
  });
}

export async function fetchManualLimits(
  accountId?: string | null,
): Promise<ManualLimits> {
  const qs = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
  const res = await apiFetch(
    `${API_BASE}/api/manual/limits${qs}`,
    {},
    20_000,
    adminToken() || undefined,
  );
  if (!res.ok) throw new Error(`manual limits ${res.status}`);
  return res.json();
}

/** Alpaca sub-penny rule: ≥$1 → 2 decimals, <$1 → 4. */
export function snapPrice(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "";
  return value >= 1 ? value.toFixed(2) : value.toFixed(4);
}

/** Human words for verb outcomes that come back with a status and no reason
 *  (the server's `reason` — the RiskEngine's own words — always wins). */
const STATUS_TEXT: Record<string, string> = {
  already_decided: "already decided — nothing sent",
  expired: "took too long — try again",
  hash_mismatch: "order terms changed — try again",
  not_found: "order not found",
  not_working: "order is no longer working",
  cancel_sent: "cancel sent",
  cancel_pending: "cancel not confirmed yet — order untouched",
  filled_before_replace: "filled before the cancel landed — nothing replaced",
  partial_fill_no_replace: "partial fill — place the remainder as a new order",
  cancelled_no_replace: "old order cancelled; place the new one manually",
  nothing_to_close: "no position to close",
  forbidden: "admin token required — Settings → Execution / Risk",
  executed: "sent",
};

export function verbText(result: ManualVerbResult): string {
  if (result.reason) return String(result.reason);
  const status = String(result.status ?? "no response");
  return STATUS_TEXT[status] ?? status.replace(/_/g, " ");
}
