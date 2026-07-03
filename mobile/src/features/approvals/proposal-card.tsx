/**
 * ProposalCard — the hero interaction. Shows a single pending proposal (thesis,
 * calibrated score, sizing, expiry) and the asymmetric Approve / Reject actions.
 *
 * SAFETY (brief §6/§7), the load-bearing rules encoded here:
 *  - Approve is the ONLY money-moving action → gated behind device auth (Face ID /
 *    passcode) via expo-local-authentication. A glance/pocket-press cannot execute.
 *  - Fail-closed: no biometrics+passcode, a cancelled/failed auth, or any error
 *    must NOT execute. A synchronous ref lock prevents double-submit.
 *  - Approve always sends the proposal's CURRENT payload_hash and respects expiry
 *    (disabled at 0). The server re-validates and we surface its verdict honestly —
 *    including 409s (hash_mismatch / expired / already_decided / blocked), which
 *    arrive as ApiError. We never paint a failure as success.
 *  - Reject is one low-friction tap (worst case: a forgone trade), but while it's
 *    in flight Approve is disabled so the two can't race on one proposal.
 */
import * as LocalAuthentication from 'expo-local-authentication';
import { useRef, useState } from 'react';
import { View } from 'react-native';
import Animated, { FadeIn, Layout } from 'react-native-reanimated';

import { ApiError } from '@/api/client';
import { getProposal } from '@/api/endpoints';
import { useApproveProposal, useRejectProposal } from '@/api/queries';
import type { PendingProposal } from '@/api/types';
import { Badge, Button, Card, Divider, Row, Stack, Text } from '@/components/ui';
import { colors, haptics, radius, spacing } from '@/theme';

import { ScoreMeter } from './score-meter';
import { useCountdown } from './use-countdown';

type Phase =
  | { kind: 'idle' }
  | { kind: 'authing' }
  | { kind: 'submitting' }
  | { kind: 'rejecting' }
  | { kind: 'reconciling' }
  | { kind: 'done'; status: string; ok: boolean; message: string };

const RESULT_COPY: Record<string, { ok: boolean; message: string }> = {
  executed: { ok: true, message: 'Approved — order sent to the broker.' },
  hash_mismatch: { ok: false, message: 'Proposal changed since you saw it. Pull to refresh and review again.' },
  expired: { ok: false, message: 'This proposal expired before it could execute.' },
  already_decided: { ok: false, message: 'This proposal was already decided.' },
  blocked: { ok: false, message: 'Blocked by the risk engine.' },
  failed: { ok: false, message: 'The broker rejected the order.' },
  not_found: { ok: false, message: 'Proposal no longer exists.' },
};

function resultCopy(status: string, reason?: string): { ok: boolean; message: string } {
  const base = RESULT_COPY[status] ?? { ok: false, message: `Server returned “${status}”.` };
  return reason && !base.ok ? { ok: base.ok, message: `${base.message} (${reason})` } : base;
}

/** Pull a server status/reason out of a thrown ApiError body (409s land here). */
function statusFromError(err: unknown): { status: string; reason?: string } | null {
  if (!(err instanceof ApiError)) return null;
  const body = err.body;
  if (body && typeof body === 'object' && 'status' in body) {
    const status = String((body as { status: unknown }).status);
    const reason = 'reason' in body ? String((body as { reason: unknown }).reason) : undefined;
    return { status, reason };
  }
  return null;
}

/** Require Face ID / passcode before a money-moving approve. Fail closed. */
async function authenticateForApprove(summary: string): Promise<boolean> {
  const hasHardware = await LocalAuthentication.hasHardwareAsync();
  const enrolled = await LocalAuthentication.isEnrolledAsync();
  // Either missing → no trustworthy device auth → never silently approve money.
  if (!hasHardware || !enrolled) {
    return false;
  }
  const res = await LocalAuthentication.authenticateAsync({
    promptMessage: `Approve · ${summary}`,
    cancelLabel: 'Cancel',
    disableDeviceFallback: false, // allow passcode as a fallback to Face ID
  });
  return res.success;
}

export interface ProposalResolution {
  ok: boolean;
  message: string;
  status: string;
}

export function ProposalCard({
  proposal,
  onResolved,
}: {
  proposal: PendingProposal;
  /**
   * Fired once the proposal reaches a terminal verdict. The parent persists this
   * so the honest result (e.g. "Blocked by the risk engine") survives the
   * pending-list refetch that would otherwise unmount the card instantly.
   */
  onResolved?: (id: string, res: ProposalResolution) => void;
}) {
  const approve = useApproveProposal();
  const reject = useRejectProposal();
  const countdown = useCountdown(proposal.expires_at);
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  // Synchronous guard against double-fire before React re-renders the disabled state.
  const inFlight = useRef(false);

  function resolve(res: ProposalResolution) {
    setPhase({ kind: 'done', status: res.status, ok: res.ok, message: res.message });
    onResolved?.(proposal.id, res);
  }

  const isSell = proposal.side?.toLowerCase() === 'sell';
  const busy =
    phase.kind === 'authing' ||
    phase.kind === 'submitting' ||
    phase.kind === 'rejecting' ||
    phase.kind === 'reconciling';
  const locked = countdown.expired || phase.kind === 'done';

  async function onApprove() {
    if (inFlight.current || busy || locked) return;
    inFlight.current = true;
    setPhase({ kind: 'authing' });
    try {
      const authed = await authenticateForApprove(proposal.summary).catch(() => false);
      if (!authed) {
        haptics.warning();
        setPhase({ kind: 'idle' });
        return;
      }
      setPhase({ kind: 'submitting' });
      try {
        // Always send the CURRENT payload_hash; server re-validates against live state.
        const res = await approve.mutateAsync({ id: proposal.id, payloadHash: proposal.payload_hash });
        const copy = resultCopy(res.status, res.reason);
        copy.ok ? haptics.success() : haptics.error();
        resolve({ status: res.status, ...copy });
      } catch (err) {
        // 409s (hash_mismatch/expired/already_decided/blocked) arrive as ApiError —
        // surface the real reason, never a fake success or a misleading "network error".
        const fromServer = statusFromError(err);
        if (fromServer) {
          haptics.error();
          resolve({ status: fromServer.status, ...resultCopy(fromServer.status, fromServer.reason) });
        } else {
          // Connection lost after submit may have succeeded — reconcile via GET, never guess.
          setPhase({ kind: 'reconciling' });
          try {
            const updated = await getProposal(proposal.id);
            const status = updated.status;
            if (status === 'executed') {
              const copy = resultCopy('executed');
              haptics.success();
              resolve({ status, ...copy });
            } else if (status === 'pending_phone') {
              haptics.error();
              resolve({
                status,
                ok: false,
                message:
                  "We couldn't reach the server — the trade was not sent. Try again when you're back online.",
              });
            } else if (status === 'approving') {
              haptics.error();
              resolve({
                status,
                ok: false,
                message: 'Still processing — check Approvals in a moment.',
              });
            } else if (
              status === 'rejected' ||
              status === 'expired' ||
              status === 'failed' ||
              status === 'not_found'
            ) {
              const copy = resultCopy(status);
              haptics.error();
              resolve({ status, ...copy });
            } else {
              haptics.error();
              resolve({
                status,
                ok: false,
                message: "Couldn't confirm the result — check Approvals.",
              });
            }
          } catch (reconcileErr) {
            if (reconcileErr instanceof ApiError && reconcileErr.status === 404) {
              const copy = resultCopy('not_found');
              haptics.error();
              resolve({ status: 'not_found', ...copy });
            } else {
              haptics.error();
              resolve({
                status: 'unknown',
                ok: false,
                message: "Couldn't confirm the result. Check Approvals when you're back online.",
              });
            }
          }
        }
      }
    } finally {
      inFlight.current = false;
    }
  }

  async function onReject() {
    if (inFlight.current || busy || phase.kind === 'done') return;
    inFlight.current = true;
    haptics.tap();
    setPhase({ kind: 'rejecting' });
    try {
      await reject.mutateAsync(proposal.id);
      resolve({ status: 'rejected', ok: true, message: 'Rejected.' });
    } catch {
      haptics.error();
      // Recoverable — drop back to idle so the user can retry without a full refresh.
      setPhase({ kind: 'idle' });
    } finally {
      inFlight.current = false;
    }
  }

  return (
    <Animated.View entering={FadeIn} layout={Layout}>
      <Card raised style={{ borderRadius: radius['2xl'], gap: spacing.lg }}>
        {/* Header: side · symbol · account */}
        <Row justify="space-between" align="flex-start">
          <Row gap={spacing.md} align="center">
            <Badge label={isSell ? 'Sell' : 'Buy'} tone={isSell ? 'negative' : 'positive'} />
            <Text variant="title">{proposal.symbol}</Text>
          </Row>
          {proposal.account_id ? (
            <Stack gap={0} style={{ alignItems: 'flex-end' }}>
              <Text variant="label" upper color="textFaint">
                account
              </Text>
              <Text variant="caption" color="textMuted">
                {proposal.account_id}
              </Text>
            </Stack>
          ) : null}
        </Row>

        <Text variant="body" color="textMuted">
          {proposal.summary}
        </Text>

        {/* Sizing strip — derive the order line from limit_price so it's never blank */}
        <Row justify="space-between">
          <Detail label="Qty" value={String(proposal.qty)} />
          <Detail
            label="Order"
            value={proposal.limit_price != null ? `Limit · $${proposal.limit_price}` : 'Market'}
            align="flex-end"
          />
        </Row>

        <Divider />
        <ScoreMeter score={proposal.score} />
        <Divider />

        {/* Expiry */}
        <Row justify="space-between">
          <Text variant="label" upper color="textFaint">
            {countdown.expired ? 'expired' : 'expires in'}
          </Text>
          <Text variant="figure" color={countdown.expired ? 'negative' : countdown.urgent ? 'warning' : 'textMuted'}>
            {countdown.label}
          </Text>
        </Row>

        {/* Result banner or actions */}
        {phase.kind === 'done' ? (
          <Animated.View entering={FadeIn}>
            <View
              style={{
                backgroundColor: phase.ok ? colors.positiveWash : colors.negativeWash,
                borderRadius: radius.lg,
                padding: spacing.lg,
              }}
            >
              <Text variant="bodyStrong" color={phase.ok ? 'positive' : 'negative'}>
                {phase.message}
              </Text>
            </View>
          </Animated.View>
        ) : phase.kind === 'reconciling' ? (
          <Animated.View entering={FadeIn}>
            <View
              style={{
                backgroundColor: colors.accentWash,
                borderRadius: radius.lg,
                padding: spacing.lg,
              }}
            >
              <Text variant="bodyStrong" color="textMuted">
                Checking…
              </Text>
            </View>
          </Animated.View>
        ) : (
          <Row gap={spacing.md}>
            <Button label="Reject" tone="ghost" onPress={onReject} disabled={busy} style={{ flex: 1 }} />
            <Button
              label={
                phase.kind === 'authing'
                  ? 'Confirm…'
                  : phase.kind === 'submitting'
                    ? 'Sending…'
                    : 'Approve'
              }
              tone="primary"
              onPress={onApprove}
              disabled={busy || locked}
              loading={phase.kind === 'submitting'}
              style={{ flex: 1.4 }}
            />
          </Row>
        )}
      </Card>
    </Animated.View>
  );
}

function Detail({
  label,
  value,
  align = 'flex-start',
}: {
  label: string;
  value: string;
  align?: 'flex-start' | 'flex-end';
}) {
  return (
    <Stack gap={2} style={{ alignItems: align }}>
      <Text variant="label" upper color="textFaint">
        {label}
      </Text>
      <Text variant="figure">{value}</Text>
    </Stack>
  );
}
