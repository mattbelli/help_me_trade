import { API_BASE, apiFetch } from "@/lib/api";

/** A strategy BUY fill that couldn't be attributed to a managed position — real
 * (paper) shares held with no exit management until repaired by hand. */
export type FillOrphan = {
  id: string;
  execution_id: string | null;
  account_id: string | null;
  strategy_id: string | null;
  proposal_id: string | null;
  symbol: string | null;
  qty: string | null;
  reason: string;
  detail: string | null;
  status: "open" | "notified" | "repaired";
  notified_at: string | null;
  created_at: string;
  updated_at: string;
};

export async function fetchFillOrphans(status?: string): Promise<FillOrphan[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  const res = await apiFetch(`${API_BASE}/api/fill-orphans${qs}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.orphans as FillOrphan[];
}

export async function repairFillOrphan(id: string): Promise<void> {
  const res = await apiFetch(
    `${API_BASE}/api/fill-orphans/${encodeURIComponent(id)}/repair`,
    { method: "POST" },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(
      (err as { error?: string; detail?: string }).error ??
        (err as { detail?: string }).detail ??
        `API error ${res.status}`,
    );
  }
}
