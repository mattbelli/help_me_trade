import { API_BASE, apiFetch } from "./api";

export type MemoryType =
  | "directive"
  | "preference"
  | "fact"
  | "lesson"
  | "thesis"
  | "research";

export type MemoryRecord = {
  id: number;
  type: MemoryType;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type SessionMemoryRecord = MemoryRecord & {
  sourceMessageId?: string | null;
};

export const MEMORY_TYPE_LABELS: Record<MemoryType, string> = {
  directive: "Directive",
  preference: "Preference",
  fact: "Fact",
  lesson: "Lesson",
  thesis: "Thesis",
  research: "Research",
};

export async function listMemories(params?: {
  type?: MemoryType;
  q?: string;
  limit?: number;
}): Promise<MemoryRecord[]> {
  const search = new URLSearchParams();
  if (params?.type) search.set("type", params.type);
  if (params?.q?.trim()) search.set("q", params.q.trim());
  if (params?.limit) search.set("limit", String(params.limit));
  const qs = search.toString();
  const res = await apiFetch(`${API_BASE}/api/memories${qs ? `?${qs}` : ""}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Failed to load memories (${res.status})`);
  }
  const data = (await res.json()) as { memories: MemoryRecord[] };
  return data.memories;
}

export async function createMemory(body: {
  type: MemoryType;
  content: string;
  metadata?: Record<string, unknown>;
}): Promise<MemoryRecord> {
  const res = await apiFetch(`${API_BASE}/api/memories`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `Failed to create memory (${res.status})`);
  }
  return res.json() as Promise<MemoryRecord>;
}

export async function updateMemory(
  id: number,
  body: {
    type?: MemoryType;
    content?: string;
    metadata?: Record<string, unknown>;
  },
): Promise<MemoryRecord> {
  const res = await apiFetch(`${API_BASE}/api/memories/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `Failed to update memory (${res.status})`);
  }
  return res.json() as Promise<MemoryRecord>;
}

export async function deleteMemory(id: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/memories/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `Failed to delete memory (${res.status})`);
  }
}

export async function extractTurnMemories(body: {
  sessionId?: string | null;
  sourceMessageId?: string | null;
  userText: string;
  assistantText: string;
  model?: string;
}): Promise<MemoryRecord[]> {
  const res = await apiFetch(`${API_BASE}/api/memory/extract`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sessionId: body.sessionId ?? undefined,
      sourceMessageId: body.sourceMessageId ?? undefined,
      userText: body.userText,
      assistantText: body.assistantText,
      model: body.model || undefined,
    }),
  });
  if (!res.ok) {
    throw new Error(`Failed to extract memories (${res.status})`);
  }
  const data = (await res.json()) as { memories: MemoryRecord[] };
  return data.memories ?? [];
}

export async function listSessionMemories(
  sessionId: string,
): Promise<SessionMemoryRecord[]> {
  const res = await apiFetch(
    `${API_BASE}/api/memory/session/${encodeURIComponent(sessionId)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`Failed to load session memories (${res.status})`);
  }
  const data = (await res.json()) as {
    memories: Array<MemoryRecord & { source_message_id?: string | null }>;
  };
  return (data.memories ?? [])
    .filter(
      (memory): memory is MemoryRecord & { source_message_id: string } =>
        typeof memory.source_message_id === "string" &&
        memory.source_message_id.length > 0,
    )
    .map(({ source_message_id, ...record }) => ({
      ...record,
      sourceMessageId: source_message_id,
    }));
}
