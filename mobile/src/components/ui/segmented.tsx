/**
 * Segmented — small in-screen tab control: lowercase mono labels over a sliding
 * active indicator (the terminal cousin of the iOS segmented control).
 */
import { useEffect, useState } from 'react';
import { Pressable, View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';

import { fonts, hairline, haptics, radius, spacing, spring, useColors } from '@/theme';

import { Text } from './text';

const PAD = 3;

export function Segmented<K extends string>({
  options,
  value,
  onChange,
}: {
  options: readonly { key: K; label: string }[];
  value: K;
  onChange: (key: K) => void;
}) {
  const colors = useColors();
  const [width, setWidth] = useState(0);
  const idx = Math.max(
    0,
    options.findIndex((o) => o.key === value),
  );
  const segW = width > 0 ? (width - PAD * 2) / options.length : 0;

  const x = useSharedValue(0);
  useEffect(() => {
    x.value = withSpring(idx * segW, spring.snappy);
  }, [idx, segW, x]);
  const indicator = useAnimatedStyle(() => ({ transform: [{ translateX: x.value }] }));

  return (
    <View
      onLayout={(e) => setWidth(e.nativeEvent.layout.width)}
      style={{
        flexDirection: 'row',
        padding: PAD,
        borderRadius: radius.md,
        backgroundColor: colors.bgInset,
        borderWidth: hairline,
        borderColor: colors.border,
      }}
    >
      {segW > 0 ? (
        <Animated.View
          style={[
            {
              position: 'absolute',
              top: PAD,
              bottom: PAD,
              left: PAD,
              width: segW,
              borderRadius: radius.sm,
              backgroundColor: colors.surfaceRaised,
              borderWidth: hairline,
              borderColor: colors.borderStrong,
            },
            indicator,
          ]}
        />
      ) : null}
      {options.map((opt) => {
        const active = opt.key === value;
        return (
          <Pressable
            key={opt.key}
            onPress={() => {
              if (opt.key === value) return;
              haptics.selection();
              onChange(opt.key);
            }}
            style={{ flex: 1, alignItems: 'center', paddingVertical: spacing.sm + 2 }}
          >
            <Text
              variant="caption"
              style={{
                fontFamily: fonts.mono,
                letterSpacing: 1.2,
                textTransform: 'lowercase',
                color: active ? colors.accent : colors.textMuted,
              }}
            >
              {opt.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}
