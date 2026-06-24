/**
 * TradeProposal Live Activity — Lock Screen banner + Dynamic Island.
 *
 * Started remotely by serv (push-to-start) when a proposal is created; shows the
 * proposal with Approve / Reject. Phase A: the buttons fire a UserInteractionEvent
 * the app handles — Approve opens the app to the Face-ID-gated approval card,
 * Reject calls the reject endpoint. (Phase B will add a Swift AppIntent for a true
 * one-tap gated approve.)
 *
 * IMPORTANT: the layout below carries the `'widget'` directive and is stringified
 * to run in an ISOLATED widget runtime — it CANNOT reference module-scope values
 * (constants, helpers, imports beyond the @expo/ui globals). Everything it needs
 * must be declared INSIDE the function. (Same rule as a reanimated worklet.)
 */
import { Button, HStack, Image, Spacer, Text, VStack } from '@expo/ui/swift-ui';
import { font, foregroundColor, padding } from '@expo/ui/swift-ui/modifiers';
import { createLiveActivity, type LiveActivityEnvironment } from 'expo-widgets';

export type TradeProposalState = {
  proposalId: string;
  symbol: string;
  side: 'buy' | 'sell';
  qty: number;
  orderType: string;
  winProb?: number;
  // 'failed' = couldn't execute AFTER approval (serv blocked/failed) — distinct
  // from 'rejected' (user declined). statusReason is the short "why" for failed.
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'executed' | 'failed';
  statusReason?: string;
};

function TradeProposalActivity(props: TradeProposalState, _env: LiveActivityEnvironment) {
  'widget';
  const ACCENT = '#5ad1be';
  const MUTED = '#9aa1ab';
  const sideColor = props.side === 'buy' ? '#5bc98a' : '#ef6f6c';
  const pending = props.status === 'pending';

  const wp = props.winProb != null ? ` · ${Math.round(props.winProb * 100)}% win` : '';
  const summary = `${props.side.toUpperCase()} ${props.qty} ${props.symbol} · ${props.orderType}${wp}`;
  const statusText =
    props.status === 'executed'
      ? 'Executed'
      : props.status === 'approved'
        ? 'Approved'
        : props.status === 'rejected'
          ? 'Rejected'
          : props.status === 'expired'
            ? 'Expired'
            : props.status === 'failed'
              ? props.statusReason
                ? `Couldn't execute · ${props.statusReason}`
                : "Couldn't execute"
              : 'Pending approval';

  const actions = pending ? (
    <HStack spacing={10}>
      <Button label="Reject" systemImage="xmark" role="destructive" target={`reject:${props.proposalId}`} />
      <Button label="Approve" systemImage="checkmark" target={`approve:${props.proposalId}`} />
    </HStack>
  ) : (
    <Text modifiers={[foregroundColor(MUTED), font({ size: 13 })]}>{statusText}</Text>
  );

  return {
    // Lock Screen
    banner: (
      <VStack spacing={8} modifiers={[padding({ all: 14 })]}>
        <HStack>
          <Text modifiers={[font({ size: 17, weight: 'bold' })]}>{props.symbol}</Text>
          <Spacer />
          <Text modifiers={[foregroundColor(ACCENT), font({ size: 13 })]}>trade proposal</Text>
        </HStack>
        <HStack>
          <Text modifiers={[foregroundColor(sideColor), font({ size: 14 })]}>{summary}</Text>
          <Spacer />
        </HStack>
        {actions}
      </VStack>
    ),
    // Dynamic Island — compact
    compactLeading: <Image systemName="chart.line.uptrend.xyaxis" color={ACCENT} />,
    compactTrailing: <Text modifiers={[font({ size: 13, weight: 'semibold' })]}>{props.symbol}</Text>,
    // minimal
    minimal: <Image systemName="chart.line.uptrend.xyaxis" color={ACCENT} />,
    // Dynamic Island — expanded
    expandedLeading: (
      <VStack>
        <Text modifiers={[font({ size: 15, weight: 'bold' })]}>{props.symbol}</Text>
        <Text modifiers={[foregroundColor(sideColor), font({ size: 12 })]}>
          {props.side.toUpperCase()} {props.qty}
        </Text>
      </VStack>
    ),
    expandedTrailing: (
      <Text modifiers={[foregroundColor(ACCENT), font({ size: 12 })]}>
        {props.winProb != null ? `${Math.round(props.winProb * 100)}% win` : props.orderType}
      </Text>
    ),
    expandedBottom: actions,
  };
}

export const tradeProposalActivity = createLiveActivity<TradeProposalState>(
  'TradeProposalActivity',
  TradeProposalActivity,
);
