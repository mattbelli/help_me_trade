import { useState } from 'react';
import { ScrollView, Switch, TextInput, View } from 'react-native';
import { useRouter } from 'expo-router';

import { useSettings, useUpdateSettings } from '@/api/queries';
import type { ReportsSettings } from '@/api/types';
import {
  BackRow,
  Button,
  ErrorState,
  Eyebrow,
  Row,
  Screen,
  Skeleton,
  Stack,
  Text,
} from '@/components/ui';
import { colors, hairline, radius, spacing } from '@/theme';

function parseReports(data: Record<string, unknown> | undefined): ReportsSettings {
  const raw = data?.reports;
  if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>;
    return {
      morning_levels_enabled: Boolean(obj.morning_levels_enabled ?? true),
      hour_et: typeof obj.hour_et === 'number' ? obj.hour_et : 8,
    };
  }
  return { morning_levels_enabled: true, hour_et: 8 };
}

export default function ReportsSettingsScreen() {
  const router = useRouter();
  const settings = useSettings();

  if (settings.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
          <BackRow onBack={() => router.back()} />
          <ErrorState onRetry={settings.refetch} />
        </View>
      </Screen>
    );
  }

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
          <BackRow onBack={() => router.back()} />
          <Text variant="title">Reports</Text>

          {settings.isLoading || !settings.data ? (
            <Stack gap={spacing.md}>
              <Skeleton height={52} />
              <Skeleton height={52} />
            </Stack>
          ) : (
            <ReportsForm key={settings.dataUpdatedAt} initial={parseReports(settings.data)} />
          )}
        </Stack>
      </ScrollView>
    </Screen>
  );
}

function ReportsForm({ initial }: { initial: ReportsSettings }) {
  const update = useUpdateSettings();
  const [morningLevels, setMorningLevels] = useState(initial.morning_levels_enabled);
  const [hourEt, setHourEt] = useState(String(initial.hour_et));
  const [justSaved, setJustSaved] = useState(false);

  const handleSave = () => {
    const parsed = Number(hourEt);
    const hour = Number.isFinite(parsed) ? Math.max(0, Math.min(23, Math.trunc(parsed))) : 8;
    update.mutate(
      { reports: { morning_levels_enabled: morningLevels, hour_et: hour } },
      {
        onSuccess: () => {
          setJustSaved(true);
          setTimeout(() => setJustSaved(false), 1500);
        },
      },
    );
  };

  return (
    <Stack gap={spacing.lg}>
      <Row justify="space-between" align="center">
        <Stack gap={2} style={{ flex: 1 }}>
          <Text variant="bodyStrong">Morning levels</Text>
          <Text variant="caption" color="textMuted">
            Daily SPY morning-levels report
          </Text>
        </Stack>
        <Switch
          value={morningLevels}
          onValueChange={setMorningLevels}
          trackColor={{ false: colors.border, true: colors.accent }}
          thumbColor={colors.surfaceRaised}
        />
      </Row>

      <Stack gap={spacing.sm}>
        <Eyebrow>hour (et)</Eyebrow>
        <TextInput
          value={hourEt}
          onChangeText={setHourEt}
          placeholder="8"
          placeholderTextColor={colors.textFaint}
          keyboardType="number-pad"
          selectionColor={colors.accent}
          style={{
            color: colors.text,
            fontSize: 16,
            paddingHorizontal: spacing.lg,
            paddingVertical: spacing.md,
            backgroundColor: colors.bgInset,
            borderRadius: radius.lg,
            borderWidth: hairline,
            borderColor: colors.border,
          }}
        />
      </Stack>

      <Button
        label={justSaved ? 'Saved' : 'Save'}
        onPress={handleSave}
        disabled={update.isPending || justSaved}
      />
    </Stack>
  );
}
