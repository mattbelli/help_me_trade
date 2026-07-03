import { type ReactNode } from 'react';
import { View } from 'react-native';

import { Icon } from '@/components/ui/icon';
import { colors, hairline, spacing } from '@/theme';

import { PressableScale } from './button';
import { Row } from './layout';
import { Text } from './text';

export function SettingsRow({
  label,
  value,
  swatch,
  onPress,
  disabled,
  right,
}: {
  label: string;
  value?: string;
  swatch?: string;
  onPress?: () => void;
  disabled?: boolean;
  right?: ReactNode;
}) {
  const row = (
    <>
      <Row justify="space-between" align="center" style={{ paddingVertical: spacing.md }}>
        <Row gap={spacing.sm} align="center" style={{ flex: 1 }}>
          {swatch ? (
            <View
              style={{
                width: 18,
                height: 18,
                borderRadius: 9,
                backgroundColor: swatch,
                borderWidth: hairline,
                borderColor: colors.border,
              }}
            />
          ) : null}
          <Text variant="bodyStrong" color={disabled ? 'textMuted' : undefined}>
            {label}
          </Text>
        </Row>
        <Row gap={spacing.sm} align="center">
          {right ??
            (disabled ? (
              value ? (
                <Text variant="body" color="textMuted">
                  {value}
                </Text>
              ) : null
            ) : (
              <>
                {value ? (
                  <Text variant="body" color="textMuted">
                    {value}
                  </Text>
                ) : null}
                <Icon name="chevron-right" size={20} color={colors.textMuted} />
              </>
            ))}
        </Row>
      </Row>
      <View style={{ height: hairline, backgroundColor: colors.border }} />
    </>
  );

  if (disabled || !onPress) {
    return row;
  }

  return <PressableScale onPress={onPress}>{row}</PressableScale>;
}
