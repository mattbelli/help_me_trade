/**
 * useLiveActivity — wires the Trade Proposal Live Activity to serv + the approve flow.
 *
 * - Registers the device's push-to-start token with serv (token_type 'la_pushtostart')
 *   so serv can START an activity remotely when a proposal is created.
 * - Captures the per-activity UPDATE token for serv-STARTED activities and registers
 *   it so serv can UPDATE/END that specific card (see section 3).
 * - Handles the Approve / Reject button taps (UserInteractionEvent). Phase A safety:
 *   Approve OPENS THE APP to the Face-ID-gated approval card (no direct background
 *   approve — the widget Button can't carry an AppIntent auth policy; that's Phase B);
 *   Reject calls the reject endpoint directly.
 */
import { useEffect, useRef } from 'react';
import { AppState } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { addPushToStartTokenListener, addUserInteractionListener } from 'expo-widgets';

import { tradeProposalActivity } from '../../widgets/trade-proposal';
import { registerDevice, registerLiveActivityToken, rejectProposal } from '@/api/endpoints';
import { devLog } from '@/lib/dev-log';

const PUSH_TO_START_KEY = 'la.pushToStartToken.v1';

export function useLiveActivity({ onOpenApprovals }: { onOpenApprovals?: () => void } = {}) {
  const onOpenRef = useRef(onOpenApprovals);
  useEffect(() => {
    onOpenRef.current = onOpenApprovals;
  }, [onOpenApprovals]);

  useEffect(() => {
    // The device's push-to-start token. Doubles as the key serv correlates per-activity
    // update tokens against — a push-STARTED activity instance can't surface its proposalId.
    let deviceToken: string | null = null;

    void AsyncStorage.getItem(PUSH_TO_START_KEY)
      .then((stored) => {
        devLog('[LA] hydrated deviceToken from storage:', stored ? 'yes' : 'none');
        if (stored && !deviceToken) deviceToken = stored;
      })
      .catch(() => {});

    // 1. push-to-start token → serv (lets serv START activities remotely)
    const tokenSub = addPushToStartTokenListener((e) => {
      const token = e.activityPushToStartToken;
      if (!token) return;
      devLog('[LA] pushToStart token received');
      deviceToken = token;
      void AsyncStorage.setItem(PUSH_TO_START_KEY, token).catch(() => {});
      void registerDevice({ token, token_type: 'la_pushtostart' }).catch(() => {});
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

    // 3. Per-activity UPDATE token for serv's push-STARTED activities. The patched
    //    expo-widgets (patches/expo-widgets+56.0.19.patch — getInstances() now calls
    //    observePushTokenUpdates) makes each push-started instance emit its update
    //    token. We must HOLD the instance objects (SharedObject release cancels the
    //    native observer), then POST {activity_update_token, device_token} so serv can
    //    target UPDATE/END — it maps to the proposal by most-recent START per device.
    const registered = new Set<string>();
    const held: ReturnType<typeof tradeProposalActivity.getInstances> = [];
    const listenerSubs: { remove: () => void }[] = [];
    let pollTimer: ReturnType<typeof setInterval> | null = null;

    const sendToken = (t: string | null | undefined) => {
      // Wait for the device token; until it lands we skip without marking registered
      // so the next poll tick retries.
      devLog(
        '[LA] sendToken: updateTok=',
        !!t,
        'deviceTok=',
        !!deviceToken,
        'already=',
        registered.has(t ?? ''),
      );
      if (!t || !deviceToken || registered.has(t)) return;
      registered.add(t);
      devLog('[LA] registering update token → POST /api/live-activities');
      void registerLiveActivityToken({ activityUpdateToken: t, deviceToken }).catch((e) =>
        devLog('[LA] register POST failed', String(e)),
      );
    };

    const armCapture = () => {
      devLog('[LA] arming capture poll');
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      let tries = 0;
      let captured = false;

      pollTimer = setInterval(() => {
        tries += 1;
        if (!captured) {
          const instances = tradeProposalActivity.getInstances();
          if (instances.length > 0) {
            captured = true;
            devLog('[LA] captured', instances.length, 'instance(s)');
            instances.forEach((inst) => {
              held.push(inst); // keep alive so the native observer isn't released
              listenerSubs.push(
                inst.addPushTokenListener((e) => sendToken((e as { pushToken?: string }).pushToken)),
              );
            });
          }
        }
        held.forEach((inst) => void inst.getPushToken().then(sendToken).catch(() => {}));
        // ~90s window (45 × 2s): on a COLD launch, ActivityKit hydrates
        // `getInstances()` with a while-dead push-started activity only after a
        // race-y delay — a short poll can miss it. The long window + the
        // foreground re-arm together win that race in practice.
        if (tries >= 45 || (captured && registered.size > 0)) {
          if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
          }
        }
      }, 2000);
    };

    armCapture();

    const appStateSub = AppState.addEventListener('change', (s) => {
      if (s === 'active') armCapture();
    });

    return () => {
      appStateSub.remove();
      if (pollTimer) clearInterval(pollTimer);
      tokenSub.remove();
      interactionSub.remove();
      listenerSubs.forEach((s) => s.remove());
    };
  }, []);
}
