import { useState } from 'react';
import { ScrollView, TextInput, View } from 'react-native';
import { useRouter } from 'expo-router';

import { useSettings, useUpdateSettings } from '@/api/queries';
import type { ResearchSettings } from '@/api/types';
import {
  BackRow,
  Button,
  ErrorState,
  Eyebrow,
  PressableScale,
  Row,
  Screen,
  Skeleton,
  Stack,
  Text,
} from '@/components/ui';
import { hairline, radius, spacing, useColors } from '@/theme';

const EFFORT_OPTIONS = ['quick', 'standard', 'deep', 'exhaustive'] as const;

function parseResearch(data: Record<string, unknown> | undefined): ResearchSettings {
  const raw = data?.research;
  if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>;
    const effort =
      typeof obj.effort === 'string' && EFFORT_OPTIONS.includes(obj.effort as (typeof EFFORT_OPTIONS)[number])
        ? obj.effort
        : 'standard';
    return {
      searxng_url: typeof obj.searxng_url === 'string' ? obj.searxng_url : '',
      effort,
    };
  }
  return { searxng_url: '', effort: 'standard' };
}

export default function ResearchSettingsScreen() {
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
          <Text variant="title">Research</Text>

          {settings.isLoading || !settings.data ? (
            <Stack gap={spacing.md}>
              <Skeleton height={52} />
              <Skeleton height={52} />
            </Stack>
          ) : (
            <ResearchForm key={settings.dataUpdatedAt} initial={parseResearch(settings.data)} />
          )}
        </Stack>
      </ScrollView>
    </Screen>
  );
}

function ResearchForm({ initial }: { initial: ResearchSettings }) {
  const colors = useColors();
  const update = useUpdateSettings();
  const [searxngUrl, setSearxngUrl] = useState(initial.searxng_url);
  const [effort, setEffort] = useState(initial.effort);
  const [justSaved, setJustSaved] = useState(false);

  const handleSave = () => {
    update.mutate(
      { research: { searxng_url: searxngUrl.trim(), effort } },
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
      <Stack gap={spacing.sm}>
        <Eyebrow>searxng url</Eyebrow>
        <TextInput
          value={searxngUrl}
          onChangeText={setSearxngUrl}
          placeholder="https://..."
          placeholderTextColor={colors.textFaint}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
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

      <Stack gap={spacing.sm}>
        <Eyebrow>effort</Eyebrow>
        <Row gap={spacing.xs} style={{ flexWrap: 'wrap' }}>
          {EFFORT_OPTIONS.map((opt) => {
            const active = effort === opt;
            return (
              <PressableScale key={opt} onPress={() => setEffort(opt)}>
                <View
                  style={{
                    paddingHorizontal: spacing.md,
                    paddingVertical: spacing.sm - 1,
                    borderRadius: 999,
                    backgroundColor: active ? colors.accentWash : colors.surfaceRaised,
                    borderWidth: hairline,
                    borderColor: active ? colors.accent : colors.border,
                  }}
                >
                  <Text variant="caption" style={{ color: active ? colors.accent : colors.textMuted }}>
                    {opt}
                  </Text>
                </View>
              </PressableScale>
            );
          })}
        </Row>
      </Stack>

      <Button
        label={justSaved ? 'Saved' : 'Save'}
        onPress={handleSave}
        disabled={update.isPending || justSaved}
      />
    </Stack>
  );
}
