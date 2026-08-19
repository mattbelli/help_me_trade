/**
 * Pure signing core for phone approvals — no Expo imports, so it can be run
 * under plain Node against the server's test vector (tests/test_approval_auth.py
 * `test_cross_side_vector`). Everything that touches the keychain or the RNG
 * lives in approval-key.ts.
 *
 * Message format MUST stay in lockstep with api/approval_auth.py::approval_message.
 * Vector (seed = bytes 0..31, "prop-123", "deadbeef", "1755600000") →
 *   pub A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=
 *   sig 9608h1TQFXfcOm/QpX4q20AYggk1h2W0kSEqz6po4LuNvN8TFFBIEHKm+TIfs4CaChDRQDR+klXyHiF2RjxCBQ==
 * — identical from this file under Node and from the server (tests/test_approval_auth.py).
 */

import * as ed from '@noble/ed25519';
import { sha512 } from '@noble/hashes/sha2.js';
import { fromByteArray, toByteArray } from 'base64-js';

ed.hashes.sha512 = sha512;

// Hermes has no btoa/atob — base64-js is what React Native itself uses.
export const bytesToBase64 = (bytes: Uint8Array): string => fromByteArray(bytes);
export const base64ToBytes = (b64: string): Uint8Array => toByteArray(b64);

export function approvalMessage(proposalId: string, payloadHash: string, ts: string): Uint8Array {
  return new TextEncoder().encode(`hmt-approve\n${proposalId}\n${payloadHash}\n${ts}`);
}

/** base64 raw 32-byte public key for a 32-byte seed. */
export function publicKeyB64(secret: Uint8Array): string {
  return bytesToBase64(ed.getPublicKey(secret));
}

/** base64 64-byte Ed25519 signature over the approval message. */
export function signApprovalMessage(
  secret: Uint8Array,
  proposalId: string,
  payloadHash: string,
  ts: string,
): string {
  return bytesToBase64(ed.sign(approvalMessage(proposalId, payloadHash, ts), secret));
}
