/**
 * Per-device approval key — the credential that actually authorizes a trade.
 *
 * The shared API token proves "some client of ours"; it is compiled into the web
 * bundle and sits on every device, so it must not be what moves money. This
 * module holds an Ed25519 secret key that is generated ON THIS PHONE and stored
 * in the iOS Keychain as this-device-only (no backup, no transfer — a new phone
 * re-enrolls). Approving a proposal signs `hmt-approve\n{proposal_id}\n{payload_hash}\n{ts}`
 * with it; serv verifies against the public key enrolled under our device id
 * (api/approval_auth.py). The pure signing core is approval-sign.ts.
 *
 * Signing is deterministic, so the only randomness needed is the 32-byte seed at
 * key creation — expo-crypto's hardware RNG, never Math.random.
 */

import * as Crypto from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';

import { base64ToBytes, bytesToBase64, publicKeyB64, signApprovalMessage } from './approval-sign';

const SECRET_KEY = 'hmt_approval_secret'; // 32-byte seed, base64
const DEVICE_ID_KEY = 'hmt_approval_device_id';
const STORE_OPTS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

export interface ApprovalIdentity {
  deviceId: string;
  /** base64 of the raw 32-byte Ed25519 public key — what serv enrolls. */
  publicKey: string;
}

interface Loaded {
  deviceId: string;
  secret: Uint8Array;
}

let cached: Loaded | null = null;
// Single-flight: the settings card and a cold-start push approve can both ask at
// once; two concurrent first-runs would mint two keys and interleave the writes.
let inflight: Promise<Loaded> | null = null;

async function loadUncached(): Promise<Loaded> {
  let deviceId = await SecureStore.getItemAsync(DEVICE_ID_KEY, STORE_OPTS);
  let secretB64 = await SecureStore.getItemAsync(SECRET_KEY, STORE_OPTS);
  if (!deviceId || !secretB64) {
    deviceId = Crypto.randomUUID();
    secretB64 = bytesToBase64(Crypto.getRandomBytes(32));
    await SecureStore.setItemAsync(SECRET_KEY, secretB64, STORE_OPTS);
    await SecureStore.setItemAsync(DEVICE_ID_KEY, deviceId, STORE_OPTS);
  }
  return { deviceId, secret: base64ToBytes(secretB64) };
}

function load(): Promise<Loaded> {
  if (cached) return Promise.resolve(cached);
  if (!inflight) {
    inflight = loadUncached()
      .then((v) => {
        cached = v;
        return v;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/** This phone's approval identity (creates the key on first call). */
export async function getApprovalIdentity(): Promise<ApprovalIdentity> {
  const { deviceId, secret } = await load();
  return { deviceId, publicKey: publicKeyB64(secret) };
}

/** Headers that prove THIS phone approved THIS proposal at THESE terms. */
export async function signApproval(
  proposalId: string,
  payloadHash: string,
): Promise<Record<string, string>> {
  const { deviceId, secret } = await load();
  const ts = String(Math.floor(Date.now() / 1000));
  return {
    'X-Approval-Device': deviceId,
    'X-Approval-Signature': signApprovalMessage(secret, proposalId, payloadHash, ts),
    'X-Approval-Timestamp': ts,
  };
}
