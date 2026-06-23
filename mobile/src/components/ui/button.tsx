/**
 * Pressable primitives.
 *  - PressableScale: any child that springs down on press (UI-thread, transform-only).
 *  - Button: the standard labeled button in primary / ghost / danger tones.
 *
 * Buttons fire a light haptic on press-in so taps feel physical.
 */
import { type ReactNode } from 'react';
import { Pressable, type PressableProps, type ViewStyle } from 'react-native';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withSpring,
} from 'react-native-reanimated';

import { colors, haptics, MIN_TOUCH, radius, spacing, spring } from '@/theme';

import { Text } from './text';

const AnimatedPressable = Animated.createAnimatedComponent(Pressable);

export function PressableScale({
  children,
  onPress,
  disabled,
  style,
  scaleTo = 0.96,
  haptic = true,
  ...rest
}: PressableProps & {
  children: ReactNode;
  style?: ViewStyle;
  scaleTo?: number;
  haptic?: boolean;
}) {
  const pressed = useSharedValue(0);
  const animatedStyle = useAnimatedStyle(() => ({
    transform: [{ scale: withSpring(1 - pressed.value * (1 - scaleTo), spring.snappy) }],
    opacity: withSpring(1 - pressed.value * 0.12, spring.snappy),
  }));

  return (
    <AnimatedPressable
      onPressIn={() => {
        pressed.value = 1;
        if (haptic && !disabled) haptics.tap();
      }}
      onPressOut={() => {
        pressed.value = 0;
      }}
      onPress={onPress}
      disabled={disabled}
      style={[animatedStyle, style]}
      {...rest}
    >
      {children}
    </AnimatedPressable>
  );
}

type Tone = 'primary' | 'ghost' | 'danger';

const toneStyle: Record<Tone, { bg: string; fg: string; border?: string }> = {
  primary: { bg: colors.accent, fg: colors.onAccent },
  ghost: { bg: 'transparent', fg: colors.text, border: colors.borderStrong },
  danger: { bg: colors.negativeWash, fg: colors.negative, border: colors.negative },
};

export function Button({
  label,
  onPress,
  tone = 'primary',
  disabled,
  loading,
  style,
  leading,
}: {
  label: string;
  onPress?: () => void;
  tone?: Tone;
  disabled?: boolean;
  loading?: boolean;
  style?: ViewStyle;
  leading?: ReactNode;
}) {
  const t = toneStyle[tone];
  const isDisabled = disabled || loading;
  return (
    <PressableScale
      onPress={onPress}
      disabled={isDisabled}
      style={{
        minHeight: MIN_TOUCH + 6,
        paddingHorizontal: spacing.xl,
        borderRadius: radius.pill,
        backgroundColor: t.bg,
        borderWidth: t.border ? 1 : 0,
        borderColor: t.border,
        alignItems: 'center',
        justifyContent: 'center',
        flexDirection: 'row',
        gap: spacing.sm,
        opacity: isDisabled ? 0.4 : 1,
        ...style,
      }}
    >
      {leading}
      <Text variant="bodyStrong" style={{ color: t.fg }}>
        {loading ? '…' : label}
      </Text>
    </PressableScale>
  );
}
