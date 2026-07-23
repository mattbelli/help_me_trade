// Reports API client — kept separate from lib/api.ts on purpose (the reports
// widget owns this surface). All calls reuse the shared API_BASE + apiFetch.
import { API_BASE, apiFetch } from "@/lib/api";

export type ReportFormat = "text" | "html" | "markdown";

export type ReportRow = {
  id: string;
  kind: string;
  symbol: string;
  title: string;
  body: string;
  format?: ReportFormat;
  payload: Record<string, unknown>;
  generated_at: string;
};

/** Report kinds the "Generate now" picker offers (on_request needs a topic and
 * is driven separately). */
export const REPORT_KINDS = [
  { value: "levels", label: "Levels" },
  { value: "morning_brief", label: "Morning brief" },
  { value: "evening_brief", label: "Evening brief" },
  { value: "flow_decomp", label: "Flow decomposition" },
] as const;

export type GenerateKind =
  | "morning_levels"
  | "levels"
  | "morning_brief"
  | "evening_brief"
  | "on_request"
  | "flow_decomp";

async function readError(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return (body as { error?: string }).error ?? fallback;
}

export async function fetchReports(limit = 30): Promise<ReportRow[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  const res = await apiFetch(`${API_BASE}/api/reports?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(await readError(res, `Reports error ${res.status}`));
  }
  const data = (await res.json()) as { reports?: ReportRow[] };
  return data.reports ?? [];
}

export async function fetchReport(reportId: string): Promise<ReportRow> {
  const res = await apiFetch(
    `${API_BASE}/api/reports/${encodeURIComponent(reportId)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(await readError(res, `Report error ${res.status}`));
  }
  return res.json();
}

export async function generateReport(input: {
  kind: GenerateKind;
  symbols?: string[];
  topic?: string;
  push?: boolean;
}): Promise<ReportRow> {
  const params = input.push ? "?push=true" : "";
  const payload: Record<string, unknown> = { kind: input.kind };
  if (input.symbols && input.symbols.length > 0) payload.symbols = input.symbols;
  if (input.topic) payload.topic = input.topic;
  const res = await apiFetch(`${API_BASE}/api/reports/generate${params}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(await readError(res, `Generate report error ${res.status}`));
  }
  return res.json();
}
