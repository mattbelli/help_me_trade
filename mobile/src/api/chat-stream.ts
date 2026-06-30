/**
 * streamChat — the copilot's streaming client. Opens `POST /api/chat/stream`,
 * reads the `text/event-stream` response, and yields one typed event per SSE
 * `data:` line (the contract lives in References/MOBILE_CHAT_BRIEF.md).
 *
 * THE RN-STREAMING RISK (the thing the brief said to de-risk first): React
 * Native's stock `fetch` does NOT expose `response.body` as a ReadableStream, so
 * plain SSE parsing fails. We use **`expo/fetch`** (the Expo-app preferred path),
 * whose FetchResponse DOES give a real `ReadableStream<Uint8Array>` — so we can
 * `getReader()` + decode + parse incrementally. Confirmed on Expo SDK 56.
 *
 * Until serv ships the endpoint, `CHAT_STUB` routes to a canned local generator
 * so the whole UI + parser can be built and verified offline. Going live is the
 * one-line flip of CHAT_STUB.
 */
import { fetch as expoFetch } from 'expo/fetch';

import { getToken } from '@/auth/token-store';
import { API_BASE_URL, CHAT_STUB } from '@/config';

export type ChatRole = 'user' | 'assistant';

/** A message in the transcript, in the shape the endpoint expects. */
export interface ChatMessage {
  role: ChatRole;
  content: string;
}

export interface ChatStreamRequest {
  messages: ChatMessage[];
  sessionId?: string;
  mode?: 'saved' | 'temporary';
  model?: string;
}

/** The typed SSE events the server emits (one per `data:` line). */
export type ChatStreamEvent =
  | { type: 'start' }
  | { type: 'text-delta'; delta: string }
  | { type: 'tool-call'; toolCallId: string; name: string; args?: Record<string, unknown> }
  | { type: 'tool-result'; toolCallId: string; name: string; ok: boolean }
  | {
      type: 'proposal';
      proposalId: string;
      summary: string;
      symbol?: string;
      side?: string;
      qty?: number;
    }
  | { type: 'error'; message: string }
  | { type: 'done'; sessionId?: string; messageId?: string };

const isEvent = (v: unknown): v is ChatStreamEvent =>
  typeof v === 'object' && v !== null && typeof (v as { type?: unknown }).type === 'string';

/** Parse a single raw SSE line into an event, or null if it isn't a data line. */
function parseSseLine(line: string): ChatStreamEvent | null {
  const trimmed = line.trim();
  // Skip blanks, comments (`:` heartbeat), and non-data fields (event:/id:/retry:).
  if (!trimmed || trimmed.startsWith(':') || !trimmed.startsWith('data:')) return null;
  const payload = trimmed.slice('data:'.length).trim();
  if (!payload || payload === '[DONE]') return null;
  try {
    const parsed: unknown = JSON.parse(payload);
    return isEvent(parsed) ? parsed : null;
  } catch {
    return null; // tolerate a malformed frame rather than killing the stream
  }
}

/**
 * Stream a chat turn. Yields typed events until the turn completes (`done`),
 * errors, or the signal aborts. Routes to the stub when CHAT_STUB is on.
 */
export async function* streamChat(
  req: ChatStreamRequest,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  if (CHAT_STUB) {
    yield* streamChatStub(req, signal);
    return;
  }

  const token = await getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  };
  if (token) headers['X-API-Token'] = token;

  const res = await expoFetch(`${API_BASE_URL}/api/chat/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify(req),
    signal,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    yield { type: 'error', message: `Chat failed (${res.status})${text ? `: ${text}` : ''}` };
    return;
  }
  if (!res.body) {
    yield { type: 'error', message: 'Chat stream had no body.' };
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // Process complete lines; keep the trailing partial in the buffer.
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        const ev = parseSseLine(line);
        if (ev) yield ev;
      }
    }
    // Flush any final line left without a trailing newline.
    const last = parseSseLine(buffer + decoder.decode());
    if (last) yield last;
  } finally {
    // Release the reader so an aborted stream doesn't leak the connection.
    void reader.cancel().catch(() => {});
  }
}

// --- Stub -----------------------------------------------------------------

/** Hermes has no global DOMException — use a plain Error tagged AbortError so the
 *  hook can tell intentional aborts from real failures. */
function abortError(): Error {
  const e = new Error('Aborted');
  e.name = 'AbortError';
  return e;
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal?.aborted) return reject(abortError());
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(t);
        reject(abortError());
      },
      { once: true },
    );
  });

/**
 * Canned stream for offline UI work: start → optional tool chip → word-by-word
 * reply → optional proposal (when the user's last message mentions buying) →
 * done. Mirrors the real event shapes so swapping in the live endpoint changes
 * nothing downstream.
 */
async function* streamChatStub(
  req: ChatStreamRequest,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const last = req.messages[req.messages.length - 1]?.content ?? '';
  const buyMatch = last.match(/\b(?:buy|long|add)\b.*?\b([A-Z]{1,5})\b/i);
  const symbol = (buyMatch?.[1] ?? 'AAPL').toUpperCase();

  yield { type: 'start' };
  await sleep(220, signal);

  // A tool round-trip → drives the optional "thinking" chip.
  yield { type: 'tool-call', toolCallId: 't1', name: 'get_context', args: { symbol } };
  await sleep(520, signal);
  yield { type: 'tool-result', toolCallId: 't1', name: 'get_context', ok: true };

  const reply = buyMatch
    ? `Here's how I read ${symbol} right now. The setup looks constructive — momentum is firming up and the risk/reward at this level is reasonable. I've drafted a sized entry for you to review; approve it from the Approvals screen with Face ID and I'll route it through the broker.`
    : `I'm your trading copilot — I can walk through the portfolio, explain what I'm watching and why, and size up a setup when one's worth taking. Ask me about a ticker, your positions, or today's risk and I'll dig in.`;

  for (const word of reply.split(' ')) {
    yield { type: 'text-delta', delta: word + ' ' };
    await sleep(38, signal);
  }

  if (buyMatch) {
    await sleep(160, signal);
    yield {
      type: 'proposal',
      proposalId: `p_stub_${symbol}`,
      summary: `BUY 10 ${symbol} @ market`,
      symbol,
      side: 'buy',
      qty: 10,
    };
  }

  yield { type: 'done', sessionId: req.sessionId ?? 's_stub', messageId: 'm_stub' };
}
