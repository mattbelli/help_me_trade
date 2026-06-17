import type { UIMessage } from "ai";

import type { ChatSessionMessage } from "@/lib/chat-sessions";

function restorePart(
  part: ChatSessionMessage["parts"][number],
): UIMessage["parts"][number] {
  if (part.type === "text") {
    return { type: "text", text: part.text ?? "" };
  }
  // Round-trip tool / step / reasoning parts saved from AI SDK UIMessage
  if (
    typeof part.type === "string" &&
    (part.type.startsWith("tool-") ||
      part.type === "dynamic-tool" ||
      part.type === "step-start" ||
      part.type === "reasoning")
  ) {
    return part as UIMessage["parts"][number];
  }
  return { type: "text", text: "" };
}

export function toUiMessage(message: ChatSessionMessage): UIMessage {
  return {
    id: message.id,
    role: message.role as UIMessage["role"],
    parts: message.parts.map(restorePart),
  };
}
