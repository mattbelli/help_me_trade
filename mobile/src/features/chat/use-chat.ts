/**
 * useChat — drives one chat session: the transcript, the assistant bubble that
 * grows as `text-delta`s land, inline proposal hand-offs, the "thinking" tool
 * hint, and streaming/error state. Owns an AbortController so a turn can be
 * stopped mid-stream (and is aborted on unmount).
 *
 * Proposals are NOT acted on here — they're surfaced as hand-off refs that the
 * UI turns into a deep-link to the existing Approvals flow (the mission rule).
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { streamChat, type ChatMessage } from '@/api/chat-stream';
import { createChatSession, getChatSession } from '@/api/endpoints';
import type { ChatSessionMessage } from '@/api/types';

/** Join the `{type:'text', text}` entries of a stored message's parts array. */
function partsToText(parts: ChatSessionMessage['parts']): string {
  return parts
    .filter((p) => p?.type === 'text' && typeof p.text === 'string')
    .map((p) => p.text as string)
    .join('');
}

/** Title for a new session: the first user message, truncated. */
function titleFrom(text: string): string {
  const t = text.trim().replace(/\s+/g, ' ');
  return t.length > 60 ? `${t.slice(0, 57)}…` : t;
}

export interface ProposalRef {
  proposalId: string;
  summary: string;
  symbol?: string;
  side?: string;
  qty?: number;
}

export interface ChatTurn {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  proposals: ProposalRef[];
  /** True while this assistant turn is still streaming. */
  streaming?: boolean;
  /** A friendly "using a tool…" label shown while the agent works. */
  toolHint?: string | null;
  /** True if the turn ended on an error event / thrown stream. */
  failed?: boolean;
}

/** Human-friendly label for the optional thinking chip. */
function toolLabel(name: string): string {
  const map: Record<string, string> = {
    get_context: 'Reading context',
    get_quote: 'Checking the price',
    get_portfolio: 'Reviewing the portfolio',
    get_positions: 'Reviewing positions',
    search_symbols: 'Searching symbols',
    get_news: 'Scanning the news',
  };
  return map[name] ?? `Using ${name.replace(/_/g, ' ')}`;
}

export interface UseChat {
  turns: ChatTurn[];
  status: 'idle' | 'streaming';
  error: string | null;
  send: (text: string) => void;
  stop: () => void;
  reset: () => void;
  /** Load a persisted session's transcript into the chat and adopt its id. */
  loadSession: (id: string) => Promise<void>;
}

export function useChat(): UseChat {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [status, setStatus] = useState<'idle' | 'streaming'>('idle');
  const [error, setError] = useState<string | null>(null);

  const seq = useRef(0);
  const sessionId = useRef<string | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);

  const nextId = () => `t${(seq.current += 1)}`;

  // Patch a single turn by id (functional update — safe under rapid deltas).
  const patch = useCallback((id: string, p: Partial<ChatTurn>) => {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...p } : t)));
  }, []);

  const send = useCallback(
    (raw: string) => {
      const text = raw.trim();
      if (!text || status === 'streaming') return;
      setError(null);

      const userTurn: ChatTurn = { id: nextId(), role: 'user', content: text, proposals: [] };
      const botId = nextId();
      const botTurn: ChatTurn = {
        id: botId,
        role: 'assistant',
        content: '',
        proposals: [],
        streaming: true,
        toolHint: null,
      };

      // Build the request from the prior committed turns + this new user message.
      const history: ChatMessage[] = [
        ...turns.map((t) => ({ role: t.role, content: t.content })),
        { role: 'user' as const, content: text },
      ];

      setTurns((prev) => [...prev, userTurn, botTurn]);
      setStatus('streaming');

      const controller = new AbortController();
      abortRef.current = controller;

      void (async () => {
        let acc = '';
        const proposals: ProposalRef[] = [];

        // Persist every chat: on the first turn of a fresh session, create a
        // saved session and stream with `mode:'saved'` + its id (serv persists
        // each turn server-side). If session creation fails, fall back to the
        // ephemeral temporary stream so the chat never blocks.
        let mode: 'saved' | 'temporary' = 'saved';
        if (!sessionId.current) {
          try {
            const session = await createChatSession(titleFrom(text));
            sessionId.current = session.id;
          } catch {
            mode = 'temporary';
          }
        }

        try {
          for await (const ev of streamChat(
            { messages: history, sessionId: sessionId.current, mode },
            controller.signal,
          )) {
            switch (ev.type) {
              case 'start':
                break;
              case 'text-delta':
                acc += ev.delta;
                patch(botId, { content: acc, toolHint: null });
                break;
              case 'tool-call':
                patch(botId, { toolHint: toolLabel(ev.name) });
                break;
              case 'tool-result':
                patch(botId, { toolHint: null });
                break;
              case 'proposal':
                proposals.push({
                  proposalId: ev.proposalId,
                  summary: ev.summary,
                  symbol: ev.symbol,
                  side: ev.side,
                  qty: ev.qty,
                });
                patch(botId, { proposals: [...proposals] });
                break;
              case 'error':
                setError(ev.message);
                patch(botId, { failed: true });
                break;
              case 'done':
                sessionId.current = ev.sessionId ?? sessionId.current;
                break;
            }
          }
        } catch (err) {
          // Aborts are intentional (stop / unmount) — not an error to surface.
          if (!(err instanceof Error && err.name === 'AbortError')) {
            setError('The chat connection dropped. Tap to retry.');
            patch(botId, { failed: true });
          }
        } finally {
          patch(botId, { streaming: false, toolHint: null });
          setStatus('idle');
          abortRef.current = null;
        }
      })();
    },
    [turns, status, patch],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    sessionId.current = undefined;
    setError(null);
    setTurns([]);
    setStatus('idle');
  }, []);

  const loadSession = useCallback(async (id: string) => {
    abortRef.current?.abort();
    setError(null);
    try {
      const detail = await getChatSession(id);
      // Historical proposal cards aren't reconstructed (v1) — only the text.
      const loaded: ChatTurn[] = detail.messages.map((m) => ({
        id: `s${(seq.current += 1)}`,
        role: m.role,
        content: partsToText(m.parts),
        proposals: [],
      }));
      sessionId.current = id;
      setTurns(loaded);
      setStatus('idle');
    } catch {
      setError("Couldn't open that chat. Tap to retry.");
    }
  }, []);

  // Abort any in-flight stream when the screen unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  return { turns, status, error, send, stop, reset, loadSession };
}
