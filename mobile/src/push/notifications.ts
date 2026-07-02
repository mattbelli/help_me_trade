/**
 * Push notification client — direct APNs (decided in RN_APP_BRIEF §3).
 *
 * What this sets up so the lock-screen approval unlock works once serv's
 * `_send_apns` + APNs creds land:
 *  - the actionable `TRADE_PROPOSAL` category with Approve / Reject buttons,
 *    Approve gated by `isAuthenticationRequired` (Face ID / passcode at the OS
 *    level before the action runs — the brief's anti-fat-finger requirement),
 *  - permission request + native APNs device-token registration with the
 *    backend (`token_type: 'apns'`), and
 *  - a foreground handler so in-app pushes still surface.
 *
 * The actual Approve/Reject execution lives in use-push.ts (money-moving path).
 * Everything here no-ops safely on a simulator (no real push token there).
 */
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

import { registerDevice } from '@/api/endpoints';

export const TRADE_PROPOSAL_CATEGORY = 'TRADE_PROPOSAL';
export const APPROVE_ACTION = 'APPROVE';
export const REJECT_ACTION = 'REJECT';

/**
 * Foreground handler. CRITICAL safety choice: a trade-proposal push is NOT shown
 * as an actionable in-app banner — iOS does NOT enforce `isAuthenticationRequired`
 * for banner taps while the app is foregrounded, so an in-app Approve button would
 * bypass Face ID. In the foreground the user approves via the in-app Approvals card
 * (which IS Face-ID gated); the lock-screen/background path keeps the iOS auth gate.
 * Other (non-trade) pushes display normally. Registered once at module load.
 */
Notifications.setNotificationHandler({
  handleNotification: async (notification) => {
    const data = notification.request.content.data as { type?: string } | undefined;
    const isTradeProposal = data?.type === 'trade_proposal';
    return {
      shouldShowBanner: !isTradeProposal,
      shouldShowList: !isTradeProposal,
      shouldPlaySound: !isTradeProposal,
      shouldSetBadge: false,
    };
  },
});

/**
 * Register the actionable category. Approve requires device auth (Face ID /
 * passcode) and runs in the background; Reject is one low-friction tap. Neither
 * forces the app to the foreground — the user acts from the lock screen.
 */
export async function registerTradeProposalCategory(): Promise<void> {
  await Notifications.setNotificationCategoryAsync(TRADE_PROPOSAL_CATEGORY, [
    {
      identifier: APPROVE_ACTION,
      buttonTitle: 'Approve',
      options: {
        isAuthenticationRequired: true, // Face ID / passcode before it runs
        opensAppToForeground: false,
      },
    },
    {
      identifier: REJECT_ACTION,
      buttonTitle: 'Reject',
      options: {
        isDestructive: true,
        opensAppToForeground: false,
      },
    },
  ]);
  try {
    const cats = await Notifications.getNotificationCategoriesAsync();
    console.log('[push] categories:', JSON.stringify(cats.map((c) => ({ id: c.identifier, actions: c.actions.map((a) => a.identifier) }))));
  } catch (e) {
    console.log('[push] getNotificationCategories error', String(e));
  }
}

/**
 * Ask permission, grab the native APNs device token, and register it with the
 * backend. Returns the token, or null if unavailable (simulator, denied perms,
 * or no push entitlement yet). Never throws.
 */
export async function registerForPush(): Promise<string | null> {
  try {
    // Real push tokens require a physical device.
    if (!Device.isDevice) return null;

    const settings = await Notifications.getPermissionsAsync();
    let status = settings.status;
    if (status !== 'granted') {
      const req = await Notifications.requestPermissionsAsync();
      status = req.status;
    }
    if (status !== 'granted') return null;

    // Direct APNs: the NATIVE device token (not an Expo push token). iOS returns a
    // hex string, but defensively hex-encode if a raw byte array ever surfaces.
    const deviceToken = await Notifications.getDevicePushTokenAsync();
    const raw: unknown = deviceToken.data;
    const token =
      raw instanceof Uint8Array
        ? Array.from(raw).map((b) => b.toString(16).padStart(2, '0')).join('')
        : String(raw);
    if (!token) return null;

    await registerDevice({ token, platform: Platform.OS, token_type: 'apns' });
    return token;
  } catch {
    // Push is additive — a failure here must never break the app.
    return null;
  }
}
