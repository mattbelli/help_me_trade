import { Alert, ScrollView, View } from 'react-native';
import { useRouter } from 'expo-router';

import { useHealth } from '@/api/queries';
import { useAuth } from '@/auth/auth-context';
import { Icon } from '@/components/ui/icon';
import { Button, Card, Eyebrow, PressableScale, Row, Screen, Skeleton, Stack, Text } from '@/components/ui';
import { API_BASE_URL, APP_VERSION } from '@/config';
import { colors, spacing } from '@/theme';

export function SettingsScreen() {
  const router = useRouter();
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
      <ScrollView
        contentContainerStyle={{
          paddingTop: spacing.sm,
          paddingHorizontal: spacing.xl,
          paddingBottom: 110,
        }}
      >
        <Stack gap={spacing.lg}>
          <Header />

          <Card>
            <Stack gap={spacing.md}>
              <Eyebrow>connection</Eyebrow>
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
            <Stack gap={spacing.md}>
              <Eyebrow>account</Eyebrow>
              <Button tone="danger" label="Disconnect" onPress={handleDisconnect} />
            </Stack>
          </Card>

          <Card>
            <Stack gap={spacing.sm}>
              <Eyebrow>price alerts</Eyebrow>
              <PressableScale onPress={() => router.push('/alerts')}>
                <Row justify="space-between" align="center">
                  <Text variant="bodyStrong">Price alerts</Text>
                  <Icon name="chevron-right" size={20} color={colors.textMuted} />
                </Row>
              </PressableScale>
            </Stack>
          </Card>

          <Card>
            <Stack gap={spacing.sm}>
              <Eyebrow>strategies</Eyebrow>
              <Row justify="space-between" align="center">
                <Stack gap={2} style={{ flex: 1 }}>
                  <Text variant="bodyStrong">Strategies</Text>
                  <Text variant="caption" color="textMuted">
                    configure the agent's strategies — coming soon
                  </Text>
                </Stack>
                <Icon name="chevron-right" size={20} color={colors.textMuted} />
              </Row>
            </Stack>
          </Card>

          <Card>
            <Stack gap={spacing.sm}>
              <Eyebrow>app</Eyebrow>
              <Text variant="body">Version {APP_VERSION}</Text>
            </Stack>
          </Card>
        </Stack>
      </ScrollView>
    </Screen>
  );
}

function Header() {
  return (
    <Stack gap={2}>
      <Eyebrow color="accent">settings</Eyebrow>
      <Text variant="title">Preferences</Text>
    </Stack>
  );
}

function stripScheme(url: string): string {
  return url.replace(/^https?:\/\//, '');
}
