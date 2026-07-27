/**
 * usePush — wires the lock-screen approval actions to the backend.
 *
 * SAFETY: the Approve notification action is declared `isAuthenticationRequired`
 * (notifications.ts), so iOS performs Face ID / passcode before delivering it from
 * the lock screen / background. Foreground trade pushes are NOT shown as actionable
 * banners (that path wouldn't enforce auth) — in-app approval goes through the
 * Face-ID-gated Approvals card instead. When an action does run we send the
 * proposal's payload_hash (carried in the push data); the server re-validates and
 * the chokepoint executes. Outcome is surfaced via a local notification.
 *
 * Handles warm taps (listener) and cold start (getLastNotificationResponseAsync),
 * deduped via a MODULE-LEVEL set so a component remount can't replay an action.
 */
import * as Notifications from 'expo-notifications';
import { router } from 'expo-router';
import { useEffect, useRef } from 'react';

import { ApiError } from '@/api/client';
import { queryClient } from '@/api/query-client';
import { approveProposal, rejectProposal } from '@/api/endpoints';
import { useAuth } from '@/auth/auth-context';
import { symbolRoute } from '@/lib/symbol-route';

import {
  APPROVE_ACTION,
  REJECT_ACTION,
  registerForPush,
  registerTradeProposalCategory,
} from './notifications';

// Survives component remounts within a session — prevents a cold-start action
// from firing twice if the host component re-mounts.
const handledResponses = new Set<string>();
// Background actions get a hard deadline; iOS suspends the process quickly.
const ACTION_TIMEOUT_MS = 20_000;

// Known push `type`s. trade_proposal + the risk_* kinds + proposal_missed route to
// Approvals (Inbox); price_alert + fill_report deep-link to the symbol; research_ready
// is a web-only surface today (shows, no route). risk_* sub-fields are provisional so we
// route on `type` only (SERV_TO_MAC handoff §3); fill_report carries a confirmed `symbol`
// (Seam 3 contract) which is safe to use for the deep-link.
type PushType =
  | 'trade_proposal'
  | 'price_alert'
  | 'risk_breaker'
  | 'risk_block'
  | 'research_ready'
  | 'proposal_missed'
  | 'fill_report'
  | 'report_ready';

interface ProposalPushData {
  type?: PushType | (string & {});
  proposal_id?: string;
  payload_hash?: string;
  expires_at?: string;
  // Price-alert push (type === 'price_alert') — flat custom keys for tap-routing.
  symbol?: string;
  alert_id?: number;
  // Nightly book/report push (type === 'report_ready') — MOBILE_AGENT_BRIEF 2026-07-23.
  report_id?: string;
}

function refreshProposals(): void {
  void queryClient.invalidateQueries({ queryKey: ['proposals', 'pending'] });
  void queryClient.invalidateQueries({ queryKey: ['snapshot'] });
}

async function notifyOutcome(title: string, body: string): Promise<void> {
  try {
    await Notifications.scheduleNotificationAsync({
      content: { title, body: body.slice(0, 80) },
      trigger: null,
    });
  } catch {
    // best-effort feedback only
  }
}

function withTimeout(): { signal: AbortSignal; done: () => void } {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ACTION_TIMEOUT_MS);
  return { signal: controller.signal, done: () => clearTimeout(timer) };
}

/** Execute an Approve action. iOS has already authenticated the user. */
async function handleApprove(data: ProposalPushData): Promise<void> {
  const id = data.proposal_id?.trim();
  const hash = data.payload_hash?.trim();
  if (!id || !hash) return; // never act on a malformed payload
  const { signal, done } = withTimeout();
  try {
    const res = await approveProposal(id, hash, signal);
    await notifyOutcome(
      res.status === 'executed' ? 'Approved' : 'Not executed',
      res.status === 'executed' ? 'Order sent to the broker.' : `Server: ${res.status}`,
    );
  } catch (err) {
    const status =
      err instanceof ApiError && err.body && typeof err.body === 'object' && 'status' in err.body
        ? String((err.body as { status: unknown }).status)
        : 'failed';
    await notifyOutcome('Not executed', status === 'failed' ? 'Network error — trade not sent.' : `Blocked: ${status}`);
  } finally {
    done();
    refreshProposals();
  }
}

async function handleReject(data: ProposalPushData): Promise<void> {
  const id = data.proposal_id?.trim();
  if (!id) return;
  const { signal, done } = withTimeout();
  try {
    await rejectProposal(id, signal);
  } catch {
    // swallow — rejection failure just leaves it pending
  } finally {
    done();
    refreshProposals();
  }
}

export function usePush({ onOpenApprovals }: { onOpenApprovals?: () => void } = {}) {
  const { token } = useAuth();
  const onOpenRef = useRef(onOpenApprovals);
  useEffect(() => {
    onOpenRef.current = onOpenApprovals;
  }, [onOpenApprovals]);

  // Register category + device token whenever we have an authenticated session.
  useEffect(() => {
    if (!token) return;
    void (async () => {
      try {
        await registerTradeProposalCategory();
      } catch {
        // If the category fails to register, action buttons won't appear — log-only.
      }
      await registerForPush();
    })();
  }, [token]);

  // Respond to taps / action buttons (warm + cold start), deduped across remounts.
  useEffect(() => {
    function dispatch(response: Notifications.NotificationResponse | null): void {
      if (!response) return;
      const key = response.notification.request.identifier;
      if (key) {
        if (handledResponses.has(key)) return;
        handledResponses.add(key);
      }

      const data = (response.notification.request.content.data ?? {}) as ProposalPushData;
      switch (response.actionIdentifier) {
        case APPROVE_ACTION:
          void handleApprove(data);
          break;
        case REJECT_ACTION:
          void handleReject(data);
          break;
        default: {
          // Body tap → deep-link. Trade + risk + missed pushes open Approvals (Inbox);
          // price alerts and fill reports jump to the symbol's detail screen; nightly
          // book reports open the report viewer. Unknown / research_ready kinds have
          // no mobile surface, so the banner just shows.
          const t = data.type;
          if (
            t === 'trade_proposal' ||
            t === 'risk_breaker' ||
            t === 'risk_block' ||
            t === 'proposal_missed'
          ) {
            onOpenRef.current?.();
          } else if ((t === 'price_alert' || t === 'fill_report') && data.symbol) {
            router.push(symbolRoute(data.symbol));
          } else if (t === 'report_ready' && data.report_id?.trim()) {
            router.push(`/report/${encodeURIComponent(data.report_id.trim())}`);
          }
        }
      }
    }

    const sub = Notifications.addNotificationResponseReceivedListener(dispatch);
    // Cold start: app launched by tapping a notification / action.
    void Notifications.getLastNotificationResponseAsync().then(dispatch);

    return () => sub.remove();
  }, []);
}
