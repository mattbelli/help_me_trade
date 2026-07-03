import { Icon } from '@/components/ui/icon';
import { colors, spacing } from '@/theme';

import { PressableScale } from './button';
import { Row } from './layout';
import { Text } from './text';

export function BackRow({ onBack }: { onBack: () => void }) {
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
