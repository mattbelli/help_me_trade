import { API_BASE } from "./api";

export type ChatMode = "standard" | "private" | "quick";

export type ChatSession = {
  id: string;
  title: string;
  mode: ChatMode;
  created_at: string;
  updated_at: string;
};

export type ChatSessionMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  parts: Array<{ type: string; text?: string }>;
  created_at: string;
};

export type ChatSessionDetail = ChatSession & {
  messages: ChatSessionMessage[];
};

export const CHAT_MODE_LABELS: Record<
  ChatMode,
  { label: string; description: string }
> = {
  standard: {
    label: "Standard",
    description: "Saved in sidebar + important parts go to memory",
  },
  private: {
    label: "Private",
    description: "Saved in sidebar only — not sent to memory",
  },
  quick: {
    label: "Quick",
    description: "Ephemeral — not saved anywhere",
  },
};

export async function fetchChatSessions(): Promise<ChatSession[]> {
  const res = await fetch(`${API_BASE}/api/chat/sessions`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Chat sessions error ${res.status}`);
  const data = await res.json();
  return data.sessions ?? [];
}

export async function createChatSession(
  mode: Exclude<ChatMode, "quick">,
  title = "New chat",
): Promise<ChatSession> {
  const res = await fetch(`${API_BASE}/api/chat/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, title }),
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
  const res = await fetch(`${API_BASE}/api/chat/sessions/${sessionId}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Chat session error ${res.status}`);
  return res.json();
}

export async function deleteChatSession(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Delete session error ${res.status}`);
}

export async function renameChatSession(
  sessionId: string,
  title: string,
): Promise<ChatSession> {
  const res = await fetch(`${API_BASE}/api/chat/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`Rename session error ${res.status}`);
  return res.json();
}
