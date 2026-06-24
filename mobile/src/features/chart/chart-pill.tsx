import { View } from 'react-native';

import { PressableScale, Text } from '@/components/ui';
import { colors, radius, spacing } from '@/theme';

/** Small pill toggle used by the chart range + line/candle selectors. */
export function ChartPill({
  label,
  active,
  onPress,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
}) {
  return (
    <PressableScale onPress={onPress} haptic>
      <View
        style={{
          paddingHorizontal: spacing.sm,
          paddingVertical: spacing.xs,
          borderRadius: radius.pill,
          backgroundColor: active ? colors.accentWash : 'transparent',
          borderWidth: 1,
          borderColor: active ? colors.accent : colors.border,
        }}
      >
        <Text variant="caption" style={{ color: active ? colors.accent : colors.textMuted }}>
          {label}
        </Text>
      </View>
    </PressableScale>
  );
}
