import { apiFetch, API_BASE } from "@/lib/api";

export type Strategy = {
  id: string;
  name: string;
  enabled: boolean;
  account_id: string | null;
  account_label: string | null;
  notional_usd: number;
  /** null = size off notional_usd; set = percent of account equity at scan time. */
  notional_pct: number | null;
  exit_mode: "auto" | "propose";
  entry_mode: "auto" | "propose";
  last_rebalanced_month?: string | null;
  params: Record<string, unknown>;
  universe: string;
  max_concurrent_positions: number | null;
  max_deployed_usd: number | null;
  created_at: string;
  updated_at: string;
};

export type StrategyAccount = {
  id: string;
  label: string;
  broker: string;
  is_default: boolean;
};

export type StrategyUniverse = {
  id: string;
  label: string;
  kind: "builtin" | "watchlist";
};

export type StrategyPatch = Partial<{
  enabled: boolean;
  account_id: string;
  notional_usd: number;
  /** Explicit null clears percent sizing (back to the static dollar amount). */
  notional_pct: number | null;
  exit_mode: "auto" | "propose";
  entry_mode: "auto" | "propose";
  params: Record<string, unknown>;
  universe: string;
  max_concurrent_positions: number | null;
  max_deployed_usd: number | null;
}>;

export async function fetchStrategies(): Promise<Strategy[]> {
  const res = await apiFetch(`${API_BASE}/api/strategies`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.strategies ?? [];
}

export async function fetchStrategyAccounts(): Promise<StrategyAccount[]> {
  const res = await apiFetch(`${API_BASE}/api/strategies/accounts`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.accounts ?? [];
}

export async function fetchStrategyUniverses(): Promise<StrategyUniverse[]> {
  const res = await apiFetch(`${API_BASE}/api/strategies/universes`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.universes ?? [];
}

export async function updateStrategy(
  id: string,
  patch: StrategyPatch,
): Promise<Strategy> {
  const res = await apiFetch(`${API_BASE}/api/strategies/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(
      (err as { detail?: string }).detail ?? `API error ${res.status}`,
    );
  }
  const data = await res.json();
  return data.strategy;
}
