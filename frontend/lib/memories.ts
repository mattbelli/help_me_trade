import { API_BASE, apiFetch } from "./api";

export type MemoryType = "preference" | "lesson" | "research";

export type MemoryRecord = {
  id: number;
  type: MemoryType;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export const MEMORY_TYPE_LABELS: Record<MemoryType, string> = {
  preference: "Preference",
  lesson: "Lesson",
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
