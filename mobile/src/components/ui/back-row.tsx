import { Icon } from '@/components/ui/icon';
import { spacing, useColors } from '@/theme';

import { PressableScale } from './button';
import { Row } from './layout';
import { Text } from './text';

export function BackRow({ onBack }: { onBack: () => void }) {
  const colors = useColors();
  return (
    <PressableScale onPress={onBack} haptic>
      <Row gap={spacing.xs} align="center">
        <Icon name="chevron-left" size={20} color={colors.textMuted} />
        <Text variant="body" color="textMuted">
          Back
        </Text>
      </Row>
    </PressableScale>
  );
}
