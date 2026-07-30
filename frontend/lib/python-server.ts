/** Server-side fetch to the Python API (Next.js route handlers only).
 *
 * The chat-brain helpers that lived here (fetchLlmRuntime, fetchChatContext,
 * executePythonTool, finalizeChatTurn) were deleted with the TS chat brain on
 * 2026-07-30 — chat is one Python call now.
 */

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

export async function proxyPythonChat(body: unknown): Promise<Response> {
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
