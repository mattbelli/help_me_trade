import { createAnthropic } from "@ai-sdk/anthropic";
import { createOpenAI } from "@ai-sdk/openai";
import {
  convertToModelMessages,
  getToolName,
  isToolUIPart,
  stepCountIs,
  streamText,
  type UIMessage,
} from "ai";

import { buildChatTools } from "@/lib/chat-tools";
import type { ChatToolId } from "@/lib/chat-toolbar-config";
import {
  inferChatToolsFromUserText,
  isOrderIntent,
  mergeEnabledChatTools,
} from "@/lib/infer-chat-tools";
import {
  fetchChatContext,
  fetchLlmRuntime,
  finalizeChatTurn,
  proxyPythonChat,
} from "@/lib/python-server";

export const maxDuration = 120;

/**
 * Which chat brain serves this route. `python` (default) proxies the whole turn
 * to the ONE Python loop (`POST /api/chat`) — native tools, approval HITL, and
 * session persistence all live there. `ts` keeps the legacy in-route AI-SDK
 * `streamText` loop below (kept intact for a one-line revert; it will be removed
 * once the Python path is proven). Non-native providers proxy to Python in both
 * modes (the Python loop degrades to a no-tools completion for them).
 */
const CHAT_BRAIN = (process.env.CHAT_BRAIN ?? "python").toLowerCase();

/** Thinking/reasoning models on these providers reject forced tool_choice. */
const NO_FORCED_TOOL_CHOICE = new Set(["deepseek"]);

type ChatRequestBody = {
  messages?: UIMessage[];
  id?: string;
  sessionId?: string | null;
  session_id?: string | null;
  mode?: string;
  model?: string;
  enabledTools?: ChatToolId[];
  autoInjectMemories?: boolean;
};

function lastUserText(messages: UIMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg.role !== "user") continue;
    const text = msg.parts
      .filter((p) => p.type === "text")
      .map((p) => ("text" in p ? p.text : ""))
      .join("\n")
      .trim();
    if (text) return text;
  }
  return "";
}

function normalizeMode(mode: string | undefined): string {
  const m = (mode ?? "temporary").toLowerCase();
  if (m === "quick") return "temporary";
  if (m === "standard" || m === "private") return "saved";
  return m;
}

/** Keep tools that were approved but may have been toggled off in the composer. */
function enabledToolsWithPendingApprovals(
  messages: UIMessage[],
  enabledTools?: ChatToolId[],
): ChatToolId[] | undefined {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "assistant") {
    return enabledTools;
  }

  const merged = new Set<string>(enabledTools ?? []);
  let hasApproval = false;

  for (const part of last.parts) {
    if (
      !isToolUIPart(part) ||
      part.state !== "approval-responded" ||
      part.approval?.approved !== true
    ) {
      continue;
    }
    hasApproval = true;
    merged.add(getToolName(part));
  }

  if (!hasApproval) return enabledTools;
  return [...merged] as ChatToolId[];
}

/** T0 latency instrumentation: collect named spans → a Server-Timing header. */
type PerfSpan = { name: string; dur: number };
function serverTimingHeader(spans: PerfSpan[]): string {
  return spans
    .map((s) => `${s.name};dur=${s.dur.toFixed(1)}`)
    .join(", ");
}

export async function POST(req: Request) {
  const tReq = performance.now();
  const spans: PerfSpan[] = [];

  let body: ChatRequestBody;
  try {
    body = (await req.json()) as ChatRequestBody;
  } catch {
    return Response.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const messages = body.messages;
  if (!Array.isArray(messages)) {
    return Response.json({ error: "messages must be a list" }, { status: 400 });
  }

  // Default path: the ONE Python brain. Forward the full body (messages,
  // session, mode, model, enabledTools, autoInjectMemories) and stream its
  // UIMessageStream bytes straight through — no TS loop, no context/finalize
  // round-trips (Python owns all of it).
  if (CHAT_BRAIN === "python") {
    return proxyPythonChat(body);
  }

  const sessionId =
    body.sessionId ?? body.session_id ?? body.id ?? null;
  const mode = normalizeMode(body.mode);
  const model = body.model?.trim() || undefined;
  const enabledTools = body.enabledTools;
  const autoInjectMemories = body.autoInjectMemories === true;

  if (mode === "saved" && !sessionId) {
    return Response.json(
      { error: "sessionId is required for saved chats" },
      { status: 400 },
    );
  }

  let runtime;
  const tRuntime = performance.now();
  try {
    runtime = await fetchLlmRuntime(model);
    spans.push({ name: "runtime", dur: performance.now() - tRuntime });
  } catch (err) {
    return Response.json(
      {
        error:
          err instanceof Error
            ? err.message
            : "Failed to load LLM runtime from Python",
      },
      { status: 503 },
    );
  }

  const isNative =
    runtime.openai_compatible || runtime.provider === "anthropic";
  if (!isNative) {
    // Non-native provider (e.g. ollama): the TS AI-SDK loop can't tool-call it,
    // so hand the whole turn to the Python loop, which degrades to a no-tools
    // completion. Forward the full body for parity (enabledTools etc.).
    return proxyPythonChat(body);
  }

  const userText = lastUserText(messages);
  let system = "";
  const tContext = performance.now();
  try {
    if (userText) {
      const ctx = await fetchChatContext(userText, autoInjectMemories, true);
      system = ctx.system;
    }
    spans.push({ name: "context", dur: performance.now() - tContext });
  } catch (err) {
    return Response.json(
      {
        error:
          err instanceof Error
            ? err.message
            : "Failed to load chat context from Python",
      },
      { status: 503 },
    );
  }

  const tools = buildChatTools(
    enabledToolsWithPendingApprovals(
      messages,
      mergeEnabledChatTools(enabledTools, userText),
    ),
  );
  const resolvedModel = runtime.model || model || "gpt-4o";
  const orderIntent = isOrderIntent(userText);
  const hasProposeOrder = "propose_order" in tools;

  let languageModel;
  if (runtime.provider === "anthropic") {
    const anthropic = createAnthropic({
      apiKey: runtime.api_key || "",
      ...(runtime.base_url && runtime.base_url !== "https://api.anthropic.com"
        ? { baseURL: runtime.base_url }
        : {}),
    });
    languageModel = anthropic(resolvedModel);
  } else {
    const openai = createOpenAI({
      apiKey: runtime.api_key || "not-needed",
      baseURL: runtime.base_url,
    });
    languageModel = openai.chat(resolvedModel);
  }

  const tConvert = performance.now();
  const modelMessages = await convertToModelMessages(messages);
  spans.push({ name: "convert", dur: performance.now() - tConvert });

  // Provider TTFB: wall-clock from request entry to the first streamed chunk.
  // Logged server-side (dev-visible) and folded into the debug readout; can't
  // ride the Server-Timing header since that flushes before streaming starts.
  const perfDebug = process.env.NEXT_PUBLIC_PERF_DEBUG === "1";
  let firstChunkSeen = false;

  const result = streamText({
    model: languageModel,
    system,
    messages: modelMessages,
    tools,
    stopWhen: stepCountIs(6),
    onChunk: () => {
      if (firstChunkSeen) return;
      firstChunkSeen = true;
      const ttfb = performance.now() - tReq;
      if (perfDebug) {
        console.log(
          `[perf] chat first-token ${ttfb.toFixed(0)}ms (${serverTimingHeader(spans)})`,
        );
      }
    },
    prepareStep: async ({ stepNumber }) => {
      if (stepNumber !== 0 || !orderIntent || !hasProposeOrder) {
        return {};
      }
      // DeepSeek thinking models return 400: "Thinking mode does not support tool_choice"
      if (NO_FORCED_TOOL_CHOICE.has(runtime.provider)) {
        return { activeTools: ["propose_order"] };
      }
      return {
        toolChoice: { type: "tool" as const, toolName: "propose_order" },
      };
    },
  });

  spans.push({ name: "preamble", dur: performance.now() - tReq });

  return result.toUIMessageStreamResponse({
    originalMessages: messages,
    headers: { "Server-Timing": serverTimingHeader(spans) },
    onFinish: async ({ responseMessage, messages: allMessages, isAborted }) => {
      if (isAborted) return;
      const assistantText = responseMessage.parts
        .filter((p) => p.type === "text")
        .map((p) => ("text" in p ? p.text : ""))
        .join("\n")
        .trim();
      const hasToolParts = responseMessage.parts.some(
        (p) => typeof p.type === "string" && p.type.startsWith("tool-"),
      );
      if (!userText && !assistantText && !hasToolParts) return;
      try {
        await finalizeChatTurn({
          messages: allMessages,
          assistantText,
          assistantMessage: responseMessage,
          sessionId,
          mode,
          model: resolvedModel,
        });
      } catch (err) {
        console.error("Chat finalize failed:", err);
      }
    },
  });
}
