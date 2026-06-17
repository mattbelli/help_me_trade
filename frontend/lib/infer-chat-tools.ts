import type { ChatToolId } from "@/lib/chat-toolbar-config";

/** User wants to buy/sell — enable propose_order even if Tools menu is empty. */
const ORDER_INTENT =
  /\b(buy|sell|purchase|short)\b[\s\S]{0,80}\b(shares?|stock|stocks|position)\b/i;

const ORDER_INTENT_TICKER =
  /\b(buy|sell|purchase|short)\b[\s\S]{0,40}\b[A-Za-z]{1,5}\b/i;

const ORDER_INTENT_EXPLICIT =
  /\b(propose|place|submit)\s+(an?\s+)?(paper\s+)?order\b/i;

const ORDER_FOR_SHARES =
  /\b(?:place\s+(?:an?\s+)?order|order)\s+for\s+\d+\s+[A-Za-z]{1,5}\s+shares?\b/i;

export function isOrderIntent(userText: string): boolean {
  const t = userText.trim();
  if (!t) return false;
  return (
    ORDER_INTENT.test(t) ||
    ORDER_INTENT_TICKER.test(t) ||
    ORDER_INTENT_EXPLICIT.test(t) ||
    ORDER_FOR_SHARES.test(t)
  );
}

export function inferChatToolsFromUserText(userText: string): ChatToolId[] {
  const inferred: ChatToolId[] = [];
  if (isOrderIntent(userText)) {
    inferred.push("propose_order");
  }
  return inferred;
}

export function mergeEnabledChatTools(
  enabledTools: ChatToolId[] | undefined,
  userText: string,
): ChatToolId[] | undefined {
  const inferred = inferChatToolsFromUserText(userText);
  if (inferred.length === 0) return enabledTools;

  const merged = new Set<string>(enabledTools ?? []);
  for (const id of inferred) merged.add(id);
  return [...merged] as ChatToolId[];
}
