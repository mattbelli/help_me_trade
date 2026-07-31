import { proxyPythonChat } from "@/lib/python-server";

export const maxDuration = 120;

/**
 * Chat is served entirely by the ONE Python brain (`POST /api/chat`) — native
 * tool-calling, approval HITL, and session persistence all live there. This
 * route only forwards the request body and streams the response back.
 *
 * The in-route AI-SDK `streamText` loop that used to live here (selected by
 * `CHAT_BRAIN=ts`) was deleted on 2026-07-30 along with `lib/chat-tools.ts`,
 * `lib/infer-chat-tools.ts` and the Python endpoints that served it. Its
 * order/alert intent prompt blocks were ported into `ai/chat/loop.py`'s
 * `assemble_system_prompt` first, so the deletion did not take them with it.
 * Its OTHER guard — `prepareStep` forcing `toolChoice: propose_order` on an
 * order turn — was NOT ported: the Python loop is `tool_choice="auto"`
 * throughout, and forcing it needs the per-provider handling this route had
 * (DeepSeek thinking models 400 on tool_choice). Tracked on the board.
 */
export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  if (!Array.isArray((body as { messages?: unknown }).messages)) {
    return Response.json({ error: "messages must be a list" }, { status: 400 });
  }

  return proxyPythonChat(body);
}
