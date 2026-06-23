/**
 * Chat — placeholder for the agent chat (PERCEIVE→REASON→DECIDE copilot). Wired
 * to the backend chat brain in a later pass; for now a tasteful "coming soon".
 */
import { View } from 'react-native';

import { Icon } from '@/components/ui/icon';
import { Screen, Stack, Text } from '@/components/ui';
import { colors, spacing } from '@/theme';

export function ChatScreen() {
  return (
    <Screen padded>
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.lg }}>
        <View
          style={{
            width: 64,
            height: 64,
            borderRadius: 999,
            backgroundColor: colors.accentWash,
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Icon name="chat" size={30} color={colors.accent} />
        </View>
        <Stack gap={spacing.sm}>
          <Text variant="title" center>
            Ask the agent
          </Text>
          <Text variant="callout" color="textMuted" center>
            Chat with your trading copilot — its thesis, your portfolio, what it’s watching. Coming soon.
          </Text>
        </Stack>
      </View>
    </Screen>
  );
}
