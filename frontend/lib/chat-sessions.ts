import { API_BASE, apiFetch } from "./api";

/** UI-facing chat modes (legacy DB values are normalized on read). */
export type ChatMode = "saved" | "temporary";

export type LegacyChatMode = ChatMode | "standard" | "private" | "quick";

export type ChatSession = {
  id: string;
  title: string;
  mode: LegacyChatMode;
  created_at: string;
  updated_at: string;
};

export type ChatSessionMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  parts: Array<Record<string, unknown> & { type: string }>;
  created_at: string;
};

export type ChatSessionDetail = ChatSession & {
  messages: ChatSessionMessage[];
};

export function normalizeChatMode(mode: string): ChatMode {
  if (mode === "quick" || mode === "temporary") return "temporary";
  return "saved";
}

export function sessionLearnsByDefault(mode: string): boolean {
  return normalizeChatMode(mode) === "saved" && mode !== "private";
}

export const CHAT_MODE_LABELS: Record<
  ChatMode,
  { label: string; description: string }
> = {
  saved: {
    label: "Saved",
    description: "Persisted in sidebar; memories may be updated after turns",
  },
  temporary: {
    label: "Temporary",
    description: "Ephemeral — not saved to sidebar or memory",
  },
};

export async function fetchChatSessions(): Promise<ChatSession[]> {
  const res = await apiFetch(`${API_BASE}/api/chat/sessions`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Chat sessions error ${res.status}`);
  const data = await res.json();
  return data.sessions ?? [];
}

export async function createChatSession(
  title = "New chat",
): Promise<ChatSession> {
  const res = await apiFetch(`${API_BASE}/api/chat/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "saved", title }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Create session error ${res.status}`,
    );
  }
  return res.json();
}

export async function fetchChatSession(
  sessionId: string,
): Promise<ChatSessionDetail> {
  const res = await apiFetch(`${API_BASE}/api/chat/sessions/${sessionId}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Chat session error ${res.status}`);
  return res.json();
}

export async function deleteChatSession(sessionId: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Delete session error ${res.status}`);
}

export async function renameChatSession(
  sessionId: string,
  title: string,
): Promise<ChatSession> {
  const res = await apiFetch(`${API_BASE}/api/chat/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`Rename session error ${res.status}`);
  return res.json();
}
