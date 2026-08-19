import { useEffect, useRef, useState } from 'react';
import { Alert, ScrollView, View } from 'react-native';
import * as Device from 'expo-device';
import { useRouter } from 'expo-router';

import { tradeProposalActivity } from '../../../widgets/trade-proposal';

import { enrollApprovalDevice } from '@/api/endpoints';
import { useApprovalDevices, useHealth } from '@/api/queries';
import { getApprovalIdentity } from '@/auth/approval-key';
import { useAuth } from '@/auth/auth-context';
import { Button, Card, Eyebrow, PageHeading, Row, Screen, SettingsRow, Skeleton, Stack, Text } from '@/components/ui';
import { API_BASE_URL, APP_VERSION } from '@/config';
import { spacing, useColors, useThemeMode } from '@/theme';

export function SettingsScreen() {
  const colors = useColors();
  const router = useRouter();
  const health = useHealth();
  const { signOut } = useAuth();
  const { mode, options } = useThemeMode();
  const testLA = useRef<ReturnType<typeof tradeProposalActivity.start> | null>(null);

  const activeThemeLabel = options.find((opt) => opt.mode === mode)?.label ?? mode;

  const startTestLA = () => {
    testLA.current = tradeProposalActivity.start({
      proposalId: `test-${Date.now()}`,
      symbol: 'AAPL',
      side: 'buy',
      qty: 10,
      orderType: 'market',
      winProb: 0.62,
      status: 'pending',
    });
  };
  const endTestLA = () => {
    tradeProposalActivity.getInstances().forEach((a) => void a.end('immediate'));
    testLA.current = null;
  };

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

          {__DEV__ ? (
            <Card>
              <Stack gap={spacing.md}>
                <Eyebrow>dev · live activity</Eyebrow>
                <Row gap={spacing.sm}>
                  <Button label="Start test" onPress={startTestLA} />
                  <Button label="End test" tone="ghost" onPress={endTestLA} />
                </Row>
              </Stack>
            </Card>
          ) : null}

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
            <Stack gap={spacing.sm}>
              <Eyebrow>appearance</Eyebrow>
              <SettingsRow
                label="Theme"
                value={activeThemeLabel}
                onPress={() => router.push('/settings/appearance')}
              />
            </Stack>
          </Card>

          <Card>
            <Stack gap={spacing.sm}>
              <Eyebrow>configuration</Eyebrow>
              <SettingsRow label="Reports" onPress={() => router.push('/settings/reports')} />
              <SettingsRow label="Research" onPress={() => router.push('/settings/research')} />
              <SettingsRow label="Brokers" onPress={() => router.push('/settings/brokers')} />
              <SettingsRow label="Execution / Risk" value="soon" disabled />
              <SettingsRow label="Strategies" onPress={() => router.push('/settings/strategies')} />
              <SettingsRow label="AI" value="soon" disabled />
              <SettingsRow label="Import" value="soon" disabled />
            </Stack>
          </Card>

          <ApprovalDeviceCard />

          <Card>
            <Stack gap={spacing.sm}>
              <Eyebrow>price alerts</Eyebrow>
              <SettingsRow label="Price alerts" onPress={() => router.push('/alerts')} />
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
  return <PageHeading title="settings" />;
}

/**
 * The phone's approval identity. Approving a trade is signed with a key that
 * lives only in this phone's keychain; serv honours it once the public half is
 * enrolled (admin token, typed once here, never stored). Until a device is
 * enrolled serv still accepts the shared token — enrolling closes that path.
 */
function ApprovalDeviceCard() {
  const devices = useApprovalDevices();
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void getApprovalIdentity().then((id) => setDeviceId(id.deviceId));
  }, []);

  const mine = devices.data?.devices.find((d) => d.device_id === deviceId);
  const state = !deviceId || devices.isLoading
    ? 'loading'
    : mine && !mine.revoked_at
      ? 'enrolled'
      : mine
        ? 'revoked'
        : 'not enrolled';

  const enroll = () => {
    Alert.prompt(
      'Enroll this phone',
      'Enter the admin token once. After this, only enrolled phones (or the admin token on the desk) can approve trades.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Enroll',
          onPress: (adminToken?: string) => {
            const token = (adminToken ?? '').trim();
            if (!token) return;
            setBusy(true);
            void enrollApprovalDevice(token, Device.deviceName ?? 'iPhone')
              .then(() => devices.refetch())
              .catch((err: unknown) =>
                Alert.alert('Enrollment failed', err instanceof Error ? err.message : String(err)),
              )
              .finally(() => setBusy(false));
          },
        },
      ],
      'secure-text',
    );
  };

  return (
    <Card>
      <Stack gap={spacing.md}>
        <Eyebrow>approval device</Eyebrow>
        <Stack gap={2}>
          <Text variant="bodyStrong">
            {state === 'enrolled'
              ? `Enrolled · ${mine?.label || 'this phone'}`
              : state === 'revoked'
                ? 'Revoked — re-enroll to approve again'
                : state === 'loading'
                  ? '…'
                  : 'Not enrolled'}
          </Text>
          <Text variant="caption" color="textMuted">
            {state === 'enrolled'
              ? 'Trade approvals are signed by a key that lives only in this phone. A new phone (or a restore) must re-enroll.'
              : 'Approvals from this phone are refused until it is enrolled.'}
          </Text>
        </Stack>
        {state !== 'enrolled' && state !== 'loading' ? (
          <Button label={busy ? 'Enrolling…' : 'Enroll this phone'} onPress={enroll} disabled={busy} />
        ) : null}
      </Stack>
    </Card>
  );
}

function stripScheme(url: string): string {
  return url.replace(/^https?:\/\//, '');
}
