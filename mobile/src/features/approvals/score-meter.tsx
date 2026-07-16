/**
 * ScoreMeter — the calibrated decision score, rendered honestly. Mirrors the web
 * proposal-score component: a win-probability bar with a base-rate marker, EV,
 * confidence interval, honesty badges, and the top contributing factors.
 *
 * HARD RULE (brief §7): never invent or round-trip fabricated confidence. We
 * render exactly what the server's decision_scorer returns; if a field is
 * missing we say so. A null score renders an explicit "no score" state.
 */
import { View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';
import { useEffect } from 'react';

import type { ProposalScore } from '@/api/types';
import { Badge, Pct, Row, Stack, Text } from '@/components/ui';
import { radius, spacing, timing, useColors } from '@/theme';

function clamp01(n: number): number {
  return Math.max(0, Math.min(1, n));
}

export function ScoreMeter({ score }: { score?: ProposalScore | null }) {
  const colors = useColors();
  if (!score) {
    return (
      <Stack gap={spacing.xs}>
        <Text variant="label" upper color="textFaint">
          decision score
        </Text>
        <Text variant="callout" color="textMuted">
          No score available for this proposal.
        </Text>
      </Stack>
    );
  }

  const win = clamp01(score.win_prob);
  const base = clamp01(score.base_rate);
  // If the server sends a value outside [0,1], clamping would silently mislead —
  // flag it instead of presenting a confident 0%/100%.
  const winOutOfRange = score.win_prob !== win;

  return (
    <Stack gap={spacing.md}>
      <Row justify="space-between">
        <Text variant="label" upper color="textFaint">
          win probability
        </Text>
        <Row gap={spacing.xs}>
          {winOutOfRange ? <Badge label="check score" tone="negative" /> : null}
          {!score.sufficient ? <Badge label="thin data" tone="warning" /> : null}
          {score.low_confidence ? <Badge label="low confidence" tone="warning" /> : null}
        </Row>
      </Row>

      {/* Win% bar with a base-rate tick — "is this better than chance?" at a glance. */}
      <View>
        <Row justify="space-between" style={{ marginBottom: spacing.xs }}>
          <Text variant="figureLg" color="accent">
            {(win * 100).toFixed(0)}
            <Text variant="figure" color="textMuted">
              % win
            </Text>
          </Text>
          <Stack gap={0} style={{ alignItems: 'flex-end' }}>
            <Text variant="label" upper color="textFaint">
              expected value
            </Text>
            {score.ev_pct != null ? (
              <Pct value={score.ev_pct} size="md" signed pnl />
            ) : (
              <Text variant="figure" color="textMuted">
                —
              </Text>
            )}
          </Stack>
        </Row>
        <WinBar win={win} base={base} />
        <Row justify="space-between" style={{ marginTop: spacing.xs }}>
          <Text variant="caption" color="textFaint">
            base rate {(base * 100).toFixed(0)}%
          </Text>
          <Text variant="caption" color="textFaint">
            CI {(clamp01(score.ci_low) * 100).toFixed(0)}–{(clamp01(score.ci_high) * 100).toFixed(0)}% · n={score.n_samples}
          </Text>
        </Row>
      </View>

      {score.top_factors?.length ? (
        <Row gap={spacing.xs} style={{ flexWrap: 'wrap' }}>
          {score.top_factors.slice(0, 4).map((f, i) => {
            const bullish = f.direction === 'bullish';
            return (
              <View
                key={`${f.name}-${i}`}
                style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 4,
                  paddingHorizontal: spacing.sm,
                  paddingVertical: 4,
                  borderRadius: radius.pill,
                  backgroundColor: bullish ? colors.positiveWash : colors.negativeWash,
                }}
              >
                <Text variant="caption" style={{ color: bullish ? colors.positive : colors.negative }}>
                  {bullish ? '↑' : '↓'} {f.name}
                </Text>
              </View>
            );
          })}
        </Row>
      ) : null}
    </Stack>
  );
}

function WinBar({ win, base }: { win: number; base: number }) {
  const colors = useColors();
  const w = useSharedValue(0);
  useEffect(() => {
    w.value = withTiming(win, timing.slow);
  }, [win, w]);
  const fill = useAnimatedStyle(() => ({ width: `${w.value * 100}%` }));

  return (
    <View style={{ height: 10, borderRadius: radius.pill, backgroundColor: colors.surfaceRaised, overflow: 'hidden', justifyContent: 'center' }}>
      <Animated.View style={[{ height: '100%', borderRadius: radius.pill, backgroundColor: colors.accent }, fill]} />
      {/* base-rate marker */}
      <View
        style={{
          position: 'absolute',
          left: `${base * 100}%`,
          width: 2,
          top: -3,
          bottom: -3,
          backgroundColor: colors.text,
          opacity: 0.7,
        }}
      />
    </View>
  );
}
