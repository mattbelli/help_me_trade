import { Alert, View } from 'react-native';

import { useHealth } from '@/api/queries';
import { useAuth } from '@/auth/auth-context';
import { Button, Card, Row, Screen, Skeleton, Stack, Text } from '@/components/ui';
import { API_BASE_URL, APP_VERSION } from '@/config';
import { colors, spacing } from '@/theme';

export function SettingsScreen() {
  const health = useHealth();
  const { signOut } = useAuth();

  const host = stripScheme(API_BASE_URL);
  const ok = health.data?.status === 'ok';

  const handleDisconnect = () => {
    Alert.alert('Disconnect?', 'This clears your saved API token and returns to onboarding.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Disconnect',
        style: 'destructive',
        onPress: () => {
          void signOut();
        },
      },
    ]);
  };

  return (
    <Screen>
      <View style={{ flex: 1, paddingTop: spacing['4xl'], paddingHorizontal: spacing.xl }}>
        <Stack gap={spacing.lg}>
          <Header />

          <Card>
            <Stack gap={spacing.md}>
              <Text variant="label" upper color="textFaint">
                connection
              </Text>
              {health.isLoading ? (
                <Skeleton height={20} width="80%" />
              ) : (
                <Row gap={spacing.sm} align="center">
                  <View
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 4,
                      backgroundColor: ok ? colors.positive : colors.negative,
                    }}
                  />
                  <Stack gap={2} style={{ flex: 1 }}>
                    <Text variant="bodyStrong">{host}</Text>
                    {health.data?.poll_healthy !== undefined ? (
                      <Text variant="caption" color="textMuted">
                        Poll {health.data.poll_healthy ? 'healthy' : 'unhealthy'}
                        {health.data.last_poll_age_seconds !== undefined
                          ? ` · last poll ${health.data.last_poll_age_seconds}s ago`
                          : ''}
                      </Text>
                    ) : health.data?.last_poll_age_seconds !== undefined ? (
                      <Text variant="caption" color="textMuted">
                        Last poll {health.data.last_poll_age_seconds}s ago
                      </Text>
                    ) : null}
                  </Stack>
                </Row>
              )}
            </Stack>
          </Card>

          <Card>
            <Stack gap={spacing.sm}>
              <Text variant="label" upper color="textFaint">
                app
              </Text>
              <Text variant="body">Version {APP_VERSION}</Text>
            </Stack>
          </Card>

          <Card>
            <Stack gap={spacing.md}>
              <Text variant="label" upper color="textFaint">
                account
              </Text>
              <Button tone="danger" label="Disconnect" onPress={handleDisconnect} />
            </Stack>
          </Card>

          <Text variant="callout" color="textMuted">
            Strategy configuration is coming in a later update.
          </Text>
        </Stack>
      </View>
    </Screen>
  );
}

function Header() {
  return (
    <Stack gap={2}>
      <Text variant="label" upper color="accent">
        settings
      </Text>
      <Text variant="title">Preferences</Text>
    </Stack>
  );
}

function stripScheme(url: string): string {
  return url.replace(/^https?:\/\//, '');
}
