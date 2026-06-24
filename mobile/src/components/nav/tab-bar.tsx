/**
 * TabBar — a floating oval pill. Tap to jump; the active tab's icon springs into
 * a teal capsule and scales up. Pairs with swipe (this just adds direct access).
 */
import { useEffect } from 'react';
import { View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { PressableScale } from '@/components/ui';
import { Icon, type IconName } from '@/components/ui/icon';
import { colors, hairline, spacing, spring } from '@/theme';

export interface TabDef {
  key: string;
  label: string;
  icon: IconName;
}

export function TabBar({
  tabs,
  index,
  onSelect,
  badges,
}: {
  tabs: TabDef[];
  index: number;
  onSelect: (i: number) => void;
  badges?: Record<string, boolean>;
}) {
  const insets = useSafeAreaInsets();
  return (
    <View
      style={{ position: 'absolute', left: 0, right: 0, bottom: Math.max(insets.bottom, spacing.md), alignItems: 'center', pointerEvents: 'box-none' }}
    >
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          gap: spacing.xs,
          backgroundColor: 'rgba(14,16,19,0.88)',
          borderRadius: 999,
          borderWidth: hairline,
          borderColor: 'rgba(255,255,255,0.12)',
          paddingHorizontal: spacing.md,
          paddingVertical: spacing.sm,
          ...shadow,
        }}
      >
        {tabs.map((t, i) => (
          <TabItem key={t.key} def={t} active={i === index} badge={badges?.[t.key]} onPress={() => onSelect(i)} />
        ))}
      </View>
    </View>
  );
}

function TabItem({ def, active, badge, onPress }: { def: TabDef; active: boolean; badge?: boolean; onPress: () => void }) {
  const a = useSharedValue(active ? 1 : 0);
  useEffect(() => {
    a.value = withSpring(active ? 1 : 0, spring.snappy);
  }, [active, a]);

  // Animate ONLY the capsule background layer — the icon stays untransformed so
  // its SVG never gets rasterized (which was making it briefly pixelate).
  const capsule = useAnimatedStyle(() => ({
    opacity: a.value,
    transform: [{ scale: 0.7 + a.value * 0.3 }],
  }));

  return (
    <PressableScale onPress={onPress} scaleTo={0.88} haptic={false} style={{ borderRadius: 999 }}>
      <View style={{ width: 54, height: 44, borderRadius: 999, alignItems: 'center', justifyContent: 'center' }}>
        <Animated.View
          style={[
            { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, borderRadius: 999, backgroundColor: colors.accentWash },
            capsule,
          ]}
        />
        <Icon name={def.icon} size={24} color={active ? colors.accent : colors.textFaint} strokeWidth={active ? 2.2 : 1.8} />
        {badge ? (
          <View
            style={{
              position: 'absolute',
              top: 4,
              right: 8,
              width: 7,
              height: 7,
              borderRadius: 4,
              backgroundColor: colors.accent,
              borderWidth: 1.5,
              borderColor: colors.bgInset,
            }}
          />
        ) : null}
      </View>
    </PressableScale>
  );
}

const shadow = {
  boxShadow: '0px 8px 20px rgba(0,0,0,0.40)',
  elevation: 10,
} as const;
