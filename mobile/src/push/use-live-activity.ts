/**
 * useLiveActivity — wires the Trade Proposal Live Activity to serv + the approve flow.
 *
 * - Registers the device's push-to-start token with serv (token_type 'la_pushtostart')
 *   so serv can START an activity remotely when a proposal is created.
 * - Handles the Approve / Reject button taps (UserInteractionEvent). Phase A safety:
 *   Approve OPENS THE APP to the Face-ID-gated approval card (no direct background
 *   approve — the widget Button can't carry an AppIntent auth policy; that's Phase B);
 *   Reject calls the reject endpoint directly.
 *
 * NOTE: per-activity UPDATE tokens (so serv can update/end a specific activity) are
 * registered where we know the proposalId — for serv-started activities that mapping
 * is a joint-test item (the instance API doesn't expose the proposalId). The
 * push-to-start token below is what unblocks serv's START path.
 */
import { useEffect, useRef } from 'react';
import { addPushToStartTokenListener, addUserInteractionListener } from 'expo-widgets';

import { registerDevice, rejectProposal } from '@/api/endpoints';

export function useLiveActivity({ onOpenApprovals }: { onOpenApprovals?: () => void } = {}) {
  const onOpenRef = useRef(onOpenApprovals);
  useEffect(() => {
    onOpenRef.current = onOpenApprovals;
  }, [onOpenApprovals]);

  useEffect(() => {
    // 1. push-to-start token → serv (lets serv START activities remotely)
    const tokenSub = addPushToStartTokenListener((e) => {
      const token = e.activityPushToStartToken;
      if (token) void registerDevice({ token, token_type: 'la_pushtostart' }).catch(() => {});
    });

    // 2. Approve / Reject button taps from the Live Activity
    const interactionSub = addUserInteractionListener((e) => {
      const [action, proposalId] = (e.target ?? '').split(':');
      if (!proposalId) return;
      if (action === 'approve') {
        // Open the app to the Face-ID-gated approval card (Phase A safety).
        onOpenRef.current?.();
      } else if (action === 'reject') {
        void rejectProposal(proposalId).catch(() => {});
      }
    });

    return () => {
      tokenSub.remove();
      interactionSub.remove();
    };
  }, []);
}
