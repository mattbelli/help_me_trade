/**
 * Small status + state primitives: Badge, Skeleton, EmptyState, ErrorState.
 * Every screen should render considered loading / empty / error states — the
 * brief makes "no placeholder UI" a requirement.
 */
import { type ReactNode } from 'react';
import { View, type ViewStyle } from 'react-native';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated';
import { useEffect } from 'react';

import { colors, hairline, radius, spacing } from '@/theme';

import { Button } from './button';
import { Text } from './text';

type BadgeTone = 'neutral' | 'accent' | 'positive' | 'negative' | 'warning';
const badgeColors: Record<BadgeTone, { bg: string; fg: string }> = {
  neutral: { bg: colors.surfaceRaised, fg: colors.textMuted },
  accent: { bg: colors.accentWash, fg: colors.accent },
  positive: { bg: colors.positiveWash, fg: colors.positive },
  negative: { bg: colors.negativeWash, fg: colors.negative },
  warning: { bg: 'rgba(227,179,65,0.14)', fg: colors.warning },
};

export function Badge({ label, tone = 'neutral' }: { label: string; tone?: BadgeTone }) {
  const c = badgeColors[tone];
  return (
    <View
      style={{
        backgroundColor: c.bg,
        paddingHorizontal: spacing.sm + 2,
        paddingVertical: 3,
        borderRadius: radius.pill,
        alignSelf: 'flex-start',
      }}
    >
      <Text variant="label" upper style={{ color: c.fg }}>
        {label}
      </Text>
    </View>
  );
}

/** A shimmering placeholder block sized to its container. */
export function Skeleton({ height = 16, width, style }: { height?: number; width?: number | string; style?: ViewStyle }) {
  const o = useSharedValue(0.4);
  useEffect(() => {
    o.value = withRepeat(withSequence(withTiming(0.9, { duration: 700 }), withTiming(0.4, { duration: 700 })), -1, true);
  }, [o]);
  const animated = useAnimatedStyle(() => ({ opacity: o.value }));
  return (
    <Animated.View
      style={[
        { height, width: (width as ViewStyle['width']) ?? '100%', backgroundColor: colors.surfaceRaised, borderRadius: radius.sm, borderWidth: hairline, borderColor: colors.border },
        animated,
        style,
      ]}
    />
  );
}

export function EmptyState({ icon, title, subtitle, action }: { icon?: ReactNode; title: string; subtitle?: string; action?: { label: string; onPress: () => void } }) {
  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing['3xl'], gap: spacing.md }}>
      {icon}
      <Text variant="heading" center>
        {title}
      </Text>
      {subtitle ? (
        <Text variant="callout" color="textMuted" center>
          {subtitle}
        </Text>
      ) : null}
      {action ? <Button label={action.label} tone="ghost" onPress={action.onPress} style={{ marginTop: spacing.md }} /> : null}
    </View>
  );
}

export function ErrorState({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <EmptyState
      title="Couldn’t load"
      subtitle={message ?? 'Check your connection to the trading server and try again.'}
      action={onRetry ? { label: 'Retry', onPress: onRetry } : undefined}
    />
  );
}
