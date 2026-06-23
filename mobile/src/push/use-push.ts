/**
 * usePush — wires the lock-screen approval actions to the backend.
 *
 * SAFETY: the Approve notification action is declared `isAuthenticationRequired`
 * (notifications.ts), so iOS performs Face ID / passcode BEFORE this handler ever
 * runs — a pocket-press or glance cannot reach here. When it does run we send the
 * proposal's payload_hash (carried in the push data); the server re-validates and
 * the chokepoint executes. We surface the outcome as a local notification and
 * refresh cached state. Reject is auth-free (worst case: a forgone trade).
 *
 * Handles warm taps (listener) and cold start (getLastNotificationResponseAsync),
 * deduped so an action never fires twice.
 */
import * as Notifications from 'expo-notifications';
import { useEffect, useRef } from 'react';

import { ApiError } from '@/api/client';
import { queryClient } from '@/api/query-client';
import { approveProposal, rejectProposal } from '@/api/endpoints';
import { useAuth } from '@/auth/auth-context';

import {
  APPROVE_ACTION,
  REJECT_ACTION,
  configureNotificationHandler,
  registerForPush,
  registerTradeProposalCategory,
} from './notifications';

interface ProposalPushData {
  type?: string;
  proposal_id?: string;
  payload_hash?: string;
  expires_at?: string;
}

function refreshProposals(): void {
  void queryClient.invalidateQueries({ queryKey: ['proposals', 'pending'] });
  void queryClient.invalidateQueries({ queryKey: ['snapshot'] });
}

async function notifyOutcome(title: string, body: string): Promise<void> {
  try {
    await Notifications.scheduleNotificationAsync({ content: { title, body }, trigger: null });
  } catch {
    // best-effort feedback only
  }
}

/** Execute an Approve action. iOS has already authenticated the user. */
async function handleApprove(data: ProposalPushData): Promise<void> {
  const id = data.proposal_id;
  const hash = data.payload_hash;
  if (!id || !hash) return; // never act on a malformed payload
  try {
    const res = await approveProposal(id, hash);
    await notifyOutcome(
      res.status === 'executed' ? 'Approved' : 'Not executed',
      res.status === 'executed' ? 'Order sent to the broker.' : `Server: ${res.status}`,
    );
  } catch (err) {
    const status = err instanceof ApiError && err.body && typeof err.body === 'object' && 'status' in err.body
      ? String((err.body as { status: unknown }).status)
      : 'failed';
    await notifyOutcome('Not executed', status === 'failed' ? 'Network error — trade not sent.' : `Blocked: ${status}`);
  } finally {
    refreshProposals();
  }
}

async function handleReject(data: ProposalPushData): Promise<void> {
  const id = data.proposal_id;
  if (!id) return;
  try {
    await rejectProposal(id);
  } catch {
    // swallow — rejection failure just leaves it pending
  } finally {
    refreshProposals();
  }
}

export function usePush({ onOpenApprovals }: { onOpenApprovals?: () => void } = {}) {
  const { token } = useAuth();
  const onOpenRef = useRef(onOpenApprovals);
  useEffect(() => {
    onOpenRef.current = onOpenApprovals;
  }, [onOpenApprovals]);

  // One-time handler config (foreground display behavior).
  useEffect(() => {
    configureNotificationHandler();
  }, []);

  // Register category + device token whenever we have an authenticated session.
  useEffect(() => {
    if (!token) return;
    void (async () => {
      await registerTradeProposalCategory();
      await registerForPush();
    })();
  }, [token]);

  // Respond to taps / action buttons (warm + cold start), deduped by response id.
  useEffect(() => {
    const handled = new Set<string>();

    function dispatch(response: Notifications.NotificationResponse | null): void {
      if (!response) return;
      const key = response.notification.request.identifier;
      if (key && handled.has(key)) return;
      if (key) handled.add(key);

      const data = (response.notification.request.content.data ?? {}) as ProposalPushData;
      switch (response.actionIdentifier) {
        case APPROVE_ACTION:
          void handleApprove(data);
          break;
        case REJECT_ACTION:
          void handleReject(data);
          break;
        default:
          // Body tap → deep-link into the Approvals surface.
          onOpenRef.current?.();
      }
    }

    const sub = Notifications.addNotificationResponseReceivedListener(dispatch);
    // Cold start: app launched by tapping a notification / action.
    void Notifications.getLastNotificationResponseAsync().then(dispatch);

    return () => sub.remove();
  }, []);
}
