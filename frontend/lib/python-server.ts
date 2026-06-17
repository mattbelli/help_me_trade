/** Server-side fetch to the Python API (Next.js route handlers only). */

const PYTHON_API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

const PYTHON_API_TOKEN =
  process.env.API_TOKEN ?? process.env.NEXT_PUBLIC_API_TOKEN ?? "";

export async function pythonFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const url = `${PYTHON_API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
  const headers = new Headers(init?.headers);
  if (PYTHON_API_TOKEN) {
    headers.set("X-API-Token", PYTHON_API_TOKEN);
  }
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(url, { ...init, headers, cache: "no-store" });
}

export type LlmRuntime = {
  provider: string;
  api_key: string;
  base_url: string;
  model: string;
  openai_compatible: boolean;
};

export async function fetchLlmRuntime(model?: string): Promise<LlmRuntime> {
  const params = model ? `?model=${encodeURIComponent(model)}` : "";
  const res = await pythonFetch(`/api/chat/llm-runtime${params}`);
  if (!res.ok) {
    throw new Error(`LLM runtime error ${res.status}`);
  }
  return res.json();
}

export async function fetchChatContext(
  userText: string,
  injectMemories = false,
): Promise<{
  system: string;
  memories: unknown[];
}> {
  const res = await pythonFetch("/api/chat/context", {
    method: "POST",
    body: JSON.stringify({ userText, injectMemories }),
  });
  if (!res.ok) {
    throw new Error(`Chat context error ${res.status}`);
  }
  return res.json();
}

export async function executePythonTool(
  name: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  const res = await pythonFetch("/api/chat/tools/execute", {
    method: "POST",
    body: JSON.stringify({ name, arguments: args }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { error?: string }).error ?? `Tool execute error ${res.status}`,
    );
  }
  const data = await res.json();
  return data.result;
}

export async function finalizeChatTurn(payload: {
  messages: unknown[];
  assistantText: string;
  assistantMessage?: unknown;
  sessionId?: string | null;
  mode: string;
  model?: string;
}): Promise<void> {
  await pythonFetch("/api/chat/finalize", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function proxyLegacyPythonChat(body: unknown): Promise<Response> {
  const res = await pythonFetch("/api/chat", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    return Response.json(err, { status: res.status });
  }
  return new Response(res.body, {
    status: res.status,
    headers: {
      "Content-Type": res.headers.get("Content-Type") ?? "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "x-vercel-ai-ui-message-stream": "v1",
    },
  });
}
