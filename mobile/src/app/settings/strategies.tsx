import { useState, type ReactNode } from 'react';
import { ScrollView, Switch, TextInput, View } from 'react-native';
import { useRouter } from 'expo-router';

import { useStrategies, useUpdateStrategy } from '@/api/queries';
import type { Strategy, StrategyEntryMode, StrategyExitMode } from '@/api/types';
import {
  BackRow,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PressableScale,
  Row,
  Screen,
  Skeleton,
  Stack,
  Text,
} from '@/components/ui';
import { hairline, radius, spacing, useColors } from '@/theme';

interface StrategyDraft {
  enabled: boolean;
  notionalUsd: string;
  entryMode: StrategyEntryMode;
  exitMode: StrategyExitMode;
  universe: string;
  maxConcurrentPositions: string;
  maxDeployedUsd: string;
}

function draftFromStrategy(strategy: Strategy): StrategyDraft {
  return {
    enabled: strategy.enabled,
    notionalUsd: String(strategy.notional_usd),
    entryMode: strategy.entry_mode,
    exitMode: strategy.exit_mode,
    universe: strategy.universe,
    maxConcurrentPositions:
      strategy.max_concurrent_positions === null
        ? ''
        : String(strategy.max_concurrent_positions),
    maxDeployedUsd:
      strategy.max_deployed_usd === null ? '' : String(strategy.max_deployed_usd),
  };
}

function buildStrategyPatch(
  draft: StrategyDraft,
  fallbackNotional: number,
): Record<string, unknown> {
  const parsedNotional = Number(draft.notionalUsd);
  const notionalUsd = Number.isFinite(parsedNotional) ? parsedNotional : fallbackNotional;

  const maxConcurrentRaw = draft.maxConcurrentPositions.trim();
  const maxConcurrentPositions =
    maxConcurrentRaw === '' ? null : Math.trunc(Number(maxConcurrentRaw));

  const maxDeployedRaw = draft.maxDeployedUsd.trim();
  const maxDeployedUsd = maxDeployedRaw === '' ? null : Number(maxDeployedRaw);

  return {
    enabled: draft.enabled,
    notional_usd: notionalUsd,
    entry_mode: draft.entryMode,
    exit_mode: draft.exitMode,
    universe: draft.universe.trim(),
    max_concurrent_positions: Number.isFinite(maxConcurrentPositions ?? NaN)
      ? maxConcurrentPositions
      : null,
    max_deployed_usd: Number.isFinite(maxDeployedUsd ?? NaN) ? maxDeployedUsd : null,
  };
}

export default function StrategiesSettingsScreen() {
  const router = useRouter();
  const strategies = useStrategies();

  if (strategies.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
          <BackRow onBack={() => router.back()} />
          <ErrorState onRetry={strategies.refetch} />
        </View>
      </Screen>
    );
  }

  const list = strategies.data?.strategies ?? [];

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
          <Text variant="title">Strategies</Text>

          {strategies.isLoading ? (
            <Stack gap={spacing.md}>
              <Skeleton height={180} />
              <Skeleton height={180} />
            </Stack>
          ) : list.length === 0 ? (
            <EmptyState title="No strategies" subtitle="No trading strategies are configured yet." />
          ) : (
            list.map((strategy) => <StrategyCard key={strategy.id} strategy={strategy} />)
          )}
        </Stack>
      </ScrollView>
    </Screen>
  );
}

function StrategyCard({ strategy }: { strategy: Strategy }) {
  const colors = useColors();
  const update = useUpdateStrategy();
  const [draft, setDraft] = useState(() => draftFromStrategy(strategy));
  const [justSaved, setJustSaved] = useState(false);

  const isSaving = update.isPending && update.variables?.id === strategy.id;

  const handleSave = () => {
    update.mutate(
      { id: strategy.id, patch: buildStrategyPatch(draft, strategy.notional_usd) },
      {
        onSuccess: () => {
          setJustSaved(true);
          setTimeout(() => setJustSaved(false), 1500);
        },
      },
    );
  };

  return (
    <Card>
      <Stack gap={spacing.md}>
        <Row justify="space-between" align="center">
          <Stack gap={2} style={{ flex: 1 }}>
            <Text variant="bodyStrong">{strategy.name}</Text>
            <Text variant="caption" color="textMuted">
              {strategy.account_label}
            </Text>
          </Stack>
          <Switch
            value={draft.enabled}
            onValueChange={(enabled) => setDraft((d) => ({ ...d, enabled }))}
            trackColor={{ false: colors.border, true: colors.accent }}
            thumbColor={colors.surfaceRaised}
          />
        </Row>

        <LabeledField label="Notional (USD)">
          <SettingsTextInput
            value={draft.notionalUsd}
            onChangeText={(notionalUsd) => setDraft((d) => ({ ...d, notionalUsd }))}
            placeholder="10000"
            keyboardType="decimal-pad"
          />
        </LabeledField>

        <LabeledField label="Entry mode">
          <ModePills
            value={draft.entryMode}
            onChange={(entryMode) => setDraft((d) => ({ ...d, entryMode }))}
          />
        </LabeledField>

        <LabeledField label="Exit mode">
          <ModePills
            value={draft.exitMode}
            onChange={(exitMode) => setDraft((d) => ({ ...d, exitMode }))}
          />
        </LabeledField>

        <LabeledField label="Universe">
          <SettingsTextInput
            value={draft.universe}
            onChangeText={(universe) => setDraft((d) => ({ ...d, universe }))}
            placeholder="sp500"
            autoCapitalize="none"
            autoCorrect={false}
          />
        </LabeledField>

        <LabeledField label="Max concurrent positions">
          <SettingsTextInput
            value={draft.maxConcurrentPositions}
            onChangeText={(maxConcurrentPositions) =>
              setDraft((d) => ({ ...d, maxConcurrentPositions }))
            }
            placeholder="Unlimited"
            keyboardType="number-pad"
          />
        </LabeledField>

        <LabeledField label="Max deployed (USD)">
          <SettingsTextInput
            value={draft.maxDeployedUsd}
            onChangeText={(maxDeployedUsd) => setDraft((d) => ({ ...d, maxDeployedUsd }))}
            placeholder="Unlimited"
            keyboardType="decimal-pad"
          />
        </LabeledField>

        {/* Advanced: strategy-specific params and account re-binding — deferred. */}
        <Text variant="caption" color="textMuted">
          Account is read-only here. Strategy params and account changes come in a later pass.
        </Text>

        <Button
          label={justSaved ? 'Saved' : 'Save'}
          onPress={handleSave}
          disabled={isSaving || justSaved}
        />
      </Stack>
    </Card>
  );
}

function ModePills({
  value,
  onChange,
}: {
  value: StrategyEntryMode | StrategyExitMode;
  onChange: (mode: StrategyEntryMode | StrategyExitMode) => void;
}) {
  const colors = useColors();

  return (
    <Row gap={spacing.xs}>
      {(['auto', 'propose'] as const).map((mode) => {
        const active = value === mode;
        const label = mode === 'auto' ? 'Auto' : 'Propose';
        return (
          <PressableScale key={mode} onPress={() => onChange(mode)}>
            <View
              style={{
                paddingHorizontal: spacing.lg,
                paddingVertical: spacing.sm,
                borderRadius: 999,
                backgroundColor: active ? colors.accent : colors.surfaceRaised,
                borderWidth: hairline,
                borderColor: active ? colors.accent : colors.border,
              }}
            >
              <Text
                variant="caption"
                style={{ color: active ? colors.onAccent : colors.textMuted }}
              >
                {label}
              </Text>
            </View>
          </PressableScale>
        );
      })}
    </Row>
  );
}

function LabeledField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Stack gap={spacing.sm}>
      <Text variant="label" upper color="textFaint">
        {label}
      </Text>
      {children}
    </Stack>
  );
}

function SettingsTextInput({
  value,
  onChangeText,
  placeholder,
  keyboardType,
  autoCapitalize,
  autoCorrect,
}: {
  value: string;
  onChangeText: (text: string) => void;
  placeholder?: string;
  keyboardType?: 'default' | 'decimal-pad' | 'number-pad';
  autoCapitalize?: 'none' | 'sentences' | 'words' | 'characters';
  autoCorrect?: boolean;
}) {
  const colors = useColors();

  return (
    <TextInput
      value={value}
      onChangeText={onChangeText}
      placeholder={placeholder}
      placeholderTextColor={colors.textFaint}
      keyboardType={keyboardType}
      autoCapitalize={autoCapitalize}
      autoCorrect={autoCorrect}
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
  );
}
