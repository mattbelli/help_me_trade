import { API_BASE } from "./api";

export type ResearchDepth = "quick" | "standard" | "deep";

export type ResearchArtifact = {
  id: number;
  topic: string;
  symbol: string | null;
  markdown: string;
  status: string;
  depth: ResearchDepth;
  report: Record<string, unknown> | null;
  html: string | null;
  sources: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
};

export type ResearchRunResult = {
  artifact_id: number;
  topic: string;
  symbol: string | null;
  depth: ResearchDepth;
  markdown: string;
  html: string | null;
  status: string;
};

export async function listResearchArtifacts(
  limit = 20,
): Promise<ResearchArtifact[]> {
  const res = await fetch(`${API_BASE}/api/research?limit=${limit}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Failed to list research (${res.status})`);
  }
  const data = (await res.json()) as { artifacts: ResearchArtifact[] };
  return data.artifacts;
}

export async function getResearchArtifact(
  id: number,
): Promise<ResearchArtifact> {
  const res = await fetch(`${API_BASE}/api/research/${id}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Research not found (${res.status})`);
  }
  return res.json() as Promise<ResearchArtifact>;
}

export function researchHtmlUrl(id: number): string {
  return `${API_BASE}/api/research/${id}/html`;
}

export async function runResearch(body: {
  topic: string;
  symbol?: string;
  depth?: ResearchDepth;
}): Promise<ResearchRunResult> {
  const res = await fetch(`${API_BASE}/api/research/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `Research failed (${res.status})`);
  }
  return res.json() as Promise<ResearchRunResult>;
}

export type ResearchStreamEvent = {
  phase: string;
  data: Record<string, unknown>;
};

export async function streamResearch(
  body: {
    topic: string;
    symbol?: string;
    depth?: ResearchDepth;
  },
  onEvent: (event: ResearchStreamEvent) => void,
): Promise<ResearchRunResult | null> {
  const res = await fetch(`${API_BASE}/api/research/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `Research stream failed (${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let doneResult: ResearchRunResult | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      const lines = chunk.split("\n");
      let phase = "message";
      let dataLine = "";
      for (const line of lines) {
        if (line.startsWith("event:")) {
          phase = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLine = line.slice(5).trim();
        }
      }
      if (!dataLine) continue;
      const data = JSON.parse(dataLine) as Record<string, unknown>;
      onEvent({ phase, data });
      if (phase === "done") {
        doneResult = {
          artifact_id: Number(data.artifact_id),
          topic: String(data.topic ?? body.topic),
          symbol: (data.symbol as string | null) ?? body.symbol ?? null,
          depth: (data.depth as ResearchDepth) ?? body.depth ?? "standard",
          markdown: "",
          html: null,
          status: String(data.status ?? "completed"),
        };
      }
      if (phase === "error") {
        throw new Error(String(data.error ?? "Research stream error"));
      }
    }
  }

  return doneResult;
}
