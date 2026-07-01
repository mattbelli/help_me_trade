import { API_BASE, apiFetch } from "@/lib/api";

export type ScanPreset = {
  id: string;
  label: string;
  description: string;
  metric_label: string;
};

export type ScanResultRow = {
  symbol: string;
  value: number;
  price: number | null;
  change_pct: number | null;
  rsi14: number | null;
  vs_sma200_pct: number | null;
  rvol: number | null;
  dist_52w_high_pct: number | null;
};

export type ScanRunResult = {
  preset_id: string;
  preset_label: string;
  metric_label: string;
  universe_size: number;
  count: number;
  results: ScanResultRow[];
  as_of: string;
};

export async function fetchScanPresets(): Promise<ScanPreset[]> {
  const res = await apiFetch(`${API_BASE}/api/scan/presets`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Scan presets error ${res.status}`,
    );
  }
  const data = (await res.json()) as { presets: ScanPreset[] };
  return data.presets;
}

export async function runScan(
  presetId: string,
  limit = 50,
): Promise<ScanRunResult> {
  const params = new URLSearchParams({
    preset: presetId,
    limit: String(limit),
  });
  const res = await apiFetch(`${API_BASE}/api/scan/run?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Scan error ${res.status}`,
    );
  }
  return res.json();
}
