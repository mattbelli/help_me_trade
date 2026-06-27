import Constants from 'expo-constants';

/**
 * Backend base URL (FastAPI). Override via EXPO_PUBLIC_API_URL.
 *
 * NOTE: serv serves plain HTTP on :8000 — Tailscale encrypts the link at the
 * network layer, so TLS isn't terminated at the app port. Reach it only by the
 * pinned `.ts.net` name (the Host allowlist rejects raw IPs). The iOS ATS
 * exception for this domain lives in app.json.
 */
export const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_URL ?? 'http://serv.tail78aab8.ts.net:8000';

/** App version from expo config, falling back to 1.0.0. */
export const APP_VERSION = Constants.expoConfig?.version ?? '1.0.0';

/**
 * Chat copilot: stream from a local canned stub instead of the live
 * `POST /api/chat/stream` endpoint. Now LIVE by default — serv shipped the
 * endpoint (References/SERV_chat_stream_DONE.md). Set EXPO_PUBLIC_CHAT_STUB=1 to
 * force the offline stub (e.g. UI work without the backend).
 */
export const CHAT_STUB = (process.env.EXPO_PUBLIC_CHAT_STUB ?? '0') !== '0';

/**
 * Build the authenticated market WebSocket URL.
 * Converts http→ws / https→wss and appends the token query param.
 */
export function wsUrl(token: string): string {
  const wsBase = API_BASE_URL.replace(/^http/, 'ws');
  return `${wsBase}/ws/market?token=${encodeURIComponent(token)}`;
}
