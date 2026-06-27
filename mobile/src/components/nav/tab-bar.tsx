/**
 * TabBar — a floating frosted pill (expo-blur) with an Instagram-style highlight
 * capsule that SLIDES behind the active icon (teal wash). Fixed pill shape; only
 * the inner capsule animates, so it's cheap and smooth.
 */
import { useEffect } from 'react';
import { View } from 'react-native';
import { BlurView } from 'expo-blur';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { PressableScale } from '@/components/ui';
import { Icon, type IconName } from '@/components/ui/icon';
import { colors, hairline, isLightTheme, spacing, spring, useThemeMode } from '@/theme';

export interface TabDef {
  key: string;
  label: string;
  icon: IconName;
}

const CELL_W = 48;
const CELL_H = 40;
const GAP = 4;
const PAD_H = 10;
const PAD_V = 6;
const STRIDE = CELL_W + GAP; // distance between cell origins
const CAP_W = CELL_W;
const CAP_H = 38;
const CAP_TOP = PAD_V + (CELL_H - CAP_H) / 2;

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
  const { resolved } = useThemeMode();
  const light = isLightTheme(resolved);
  // The frosted pill is tinted for the active theme — a dark glass on dark themes,
  // a light glass in light mode (otherwise the dark pill + dark icons vanish).
  const pillBg = light ? 'rgba(255,255,255,0.62)' : 'rgba(40,45,54,0.5)';
  const pillBorder = light ? 'rgba(0,0,0,0.08)' : 'rgba(255,255,255,0.16)';
  const capX = useSharedValue(index * STRIDE);

  useEffect(() => {
    capX.value = withSpring(index * STRIDE, spring.snappy);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index]);

  const capStyle = useAnimatedStyle(() => ({ transform: [{ translateX: capX.value }] }));

  return (
    <View
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        bottom: Math.max(insets.bottom, spacing.md),
        alignItems: 'center',
        pointerEvents: 'box-none',
      }}
    >
      <View style={{ borderRadius: 999, ...shadow }}>
        <BlurView
          intensity={64}
          tint={light ? 'light' : 'dark'}
          style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: GAP,
            borderRadius: 999,
            overflow: 'hidden',
            borderWidth: hairline,
            borderColor: pillBorder,
            paddingHorizontal: PAD_H,
            paddingVertical: PAD_V,
            backgroundColor: pillBg,
          }}
        >
          {/* Sliding highlight capsule (behind the icons) */}
          <Animated.View
            style={[
              {
                position: 'absolute',
                top: CAP_TOP,
                left: PAD_H,
                width: CAP_W,
                height: CAP_H,
                borderRadius: 15,
                backgroundColor: colors.accentWash,
                borderWidth: 1,
                borderColor: colors.accent,
              },
              capStyle,
            ]}
          />
          {tabs.map((t, i) => (
            <TabItem key={t.key} def={t} active={i === index} badge={badges?.[t.key]} onPress={() => onSelect(i)} />
          ))}
        </BlurView>
      </View>
    </View>
  );
}

function TabItem({
  def,
  active,
  badge,
  onPress,
}: {
  def: TabDef;
  active: boolean;
  badge?: boolean;
  onPress: () => void;
}) {
  // scaleTo=1: never scale the SVG icon on press (scaling rasterizes → pixelates).
  return (
    <PressableScale onPress={onPress} scaleTo={1} haptic={false} style={{ borderRadius: 15 }}>
      <View style={{ width: CELL_W, height: CELL_H, alignItems: 'center', justifyContent: 'center' }}>
        <Icon
          name={def.icon}
          size={26}
          color={active ? colors.accent : colors.textMuted}
          strokeWidth={active ? 2.2 : 1.8}
        />
        {badge ? (
          <View
            style={{
              position: 'absolute',
              top: 3,
              right: 6,
              width: 7,
              height: 7,
              borderRadius: 4,
              backgroundColor: colors.warm,
              borderWidth: 1.5, // gold dot stands out against the teal active capsule
              borderColor: colors.bgInset,
            }}
          />
        ) : null}
      </View>
    </PressableScale>
  );
}

const shadow = {
  boxShadow: '0px 4px 14px rgba(0,0,0,0.38)',
  elevation: 8,
} as const;
