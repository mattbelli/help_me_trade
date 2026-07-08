/**
 * ToolTimeline — the compact "here's what it did" trail shown at the top of an
 * assistant bubble. Each row is a status glyph + a label + an optional muted
 * detail (a search query, a symbol). A running step pulses; once the answer text
 * lands the whole trail de-emphasises so the answer stays the focus.
 *
 * Replaces the old single "using a tool…" hint — a real per-tool timeline.
 */
import { useEffect } from 'react';
import { View } from 'react-native';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated';

import { Icon } from '@/components/ui/icon';
import { Row, Stack, Text } from '@/components/ui';
import { spacing, useColors } from '@/theme';

import type { ToolStep } from './use-chat';

export function ToolTimeline({
  steps,
  dimmed,
}: {
  steps: ToolStep[];
  /** True once answer text has arrived — de-emphasise the completed trail. */
  dimmed: boolean;
}) {
  if (steps.length === 0) return null;
  return (
    <Stack gap={spacing.xs} style={{ marginBottom: spacing.sm }}>
      {steps.map((step) => (
        <StepRow key={step.id} step={step} dimmed={dimmed} />
      ))}
    </Stack>
  );
}

function StepRow({ step, dimmed }: { step: ToolStep; dimmed: boolean }) {
  const labelColor = dimmed || step.status !== 'running' ? 'textMuted' : 'text';
  return (
    <Row gap={spacing.sm} align="center">
      <StatusGlyph status={step.status} />
      <Text
        variant="caption"
        color={step.status === 'failed' ? 'negative' : labelColor}
        numberOfLines={1}
        style={{ flexShrink: 1 }}
      >
        {step.label}
        {step.detail ? (
          <Text variant="caption" color="textFaint">
            {'  '}
            {step.detail}
          </Text>
        ) : null}
      </Text>
    </Row>
  );
}

function StatusGlyph({ status }: { status: ToolStep['status'] }) {
  const colors = useColors();
  if (status === 'running') return <PulseDot />;
  if (status === 'failed') {
    return (
      <View style={GLYPH_BOX}>
        <Icon name="close" size={12} color={colors.negative} strokeWidth={2.2} />
      </View>
    );
  }
  return (
    <View style={GLYPH_BOX}>
      <Icon name="check" size={12} color={colors.positive} strokeWidth={2.2} />
    </View>
  );
}

/** A single dot that breathes while its tool runs (same pattern as the bubble). */
function PulseDot() {
  const colors = useColors();
  const o = useSharedValue(0.4);
  useEffect(() => {
    o.value = withRepeat(
      withSequence(withTiming(1, { duration: 600 }), withTiming(0.4, { duration: 600 })),
      -1,
      true,
    );
  }, [o]);
  const style = useAnimatedStyle(() => ({ opacity: o.value }));
  return (
    <View style={GLYPH_BOX}>
      <Animated.View
        style={[style, { width: 7, height: 7, borderRadius: 4, backgroundColor: colors.accent }]}
      />
    </View>
  );
}

/** Fixed glyph column so labels align regardless of glyph. */
const GLYPH_BOX = {
  width: 14,
  height: 14,
  alignItems: 'center',
  justifyContent: 'center',
} as const;
