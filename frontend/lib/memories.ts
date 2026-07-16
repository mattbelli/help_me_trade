import { API_BASE, apiFetch } from "./api";

export type MemoryType =
  | "directive"
  | "preference"
  | "fact"
  | "lesson"
  | "thesis"
  | "research";

export type MemoryStatus = "active" | "superseded" | "archived" | "expired";
export type MemoryAuthority =
  | "user_authored"
  | "user_confirmed"
  | "agent_inferred";
export type MemorySource =
  | "user_explicit"
  | "chat_extract"
  | "agent_save"
  | "reflection"
  | "retrospective"
  | "research_promote"
  | "widget_manual"
  | "legacy"
  | string;

export type MemoryRecord = {
  id: number;
  type: MemoryType;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  status?: MemoryStatus;
  source?: MemorySource;
  authority?: MemoryAuthority;
  quote?: string;
  pinned?: boolean;
  supersedes_id?: number;
  /** Pending founder-review proposal (M-1): id of the protected row this row
   * proposes to replace. Set only while awaiting the founder's Accept/Keep-old. */
  proposed_supersedes_id?: number;
  valid_until?: string;
  symbols?: string[];
  last_used_at?: string;
  use_count?: number;
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
  includeInactive?: boolean;
}): Promise<MemoryRecord[]> {
  const search = new URLSearchParams();
  if (params?.type) search.set("type", params.type);
  if (params?.q?.trim()) search.set("q", params.q.trim());
  if (params?.limit) search.set("limit", String(params.limit));
  if (params?.includeInactive) search.set("include_inactive", "true");
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
    status?: MemoryStatus;
    pinned?: boolean;
    authority?: MemoryAuthority;
    valid_until?: string;
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

/** Pin/unpin — pinned rows are always injected (tier A). */
export function setMemoryPinned(
  id: number,
  pinned: boolean,
): Promise<MemoryRecord> {
  return updateMemory(id, { pinned });
}

/** Founder tap: promote an agent-inferred memory to user_confirmed (§7). */
export function confirmMemory(id: number): Promise<MemoryRecord> {
  return updateMemory(id, { authority: "user_confirmed" });
}

/** Restore an archived/superseded row back to active (History view). */
export function restoreMemory(id: number): Promise<MemoryRecord> {
  return updateMemory(id, { status: "active" });
}

/** Archive a memory (soft delete — restorable from History). */
export function archiveMemory(id: number): Promise<MemoryRecord> {
  return updateMemory(id, { status: "archived" });
}

/**
 * Founder resolution of a pending supersede proposal (M-1 review lane).
 * `accept` archives+supersedes the old protected row and promotes this proposal
 * to user_confirmed (this call IS the founder Confirm gesture); `reject`
 * ("keep old") archives the proposal and leaves the old row untouched.
 */
export async function resolveMemoryProposal(
  id: number,
  action: "accept" | "reject",
): Promise<MemoryRecord> {
  const res = await apiFetch(
    `${API_BASE}/api/memories/${id}/resolve-proposal`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `Failed to resolve proposal (${res.status})`);
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
