/**
 * Approvals — the hero surface. The agent's pending proposals as a stack of
 * rich cards. Calm when empty ("nothing to decide"), urgent when not.
 *
 * Decided proposals leave the pending list immediately (server-side), so we keep
 * their verdict on screen as a dismissible banner — the user must always be able
 * to read WHY a trade executed, was rejected, or was blocked.
 */
import { useState } from 'react';
import { RefreshControl, ScrollView, View } from 'react-native';
import Animated, { FadeIn } from 'react-native-reanimated';

import { usePendingProposals } from '@/api/queries';
import { Button, ErrorState, Row, Screen, Skeleton, Stack, Text } from '@/components/ui';
import { colors, radius, spacing } from '@/theme';

import { ProposalCard, type ProposalResolution } from './proposal-card';

export function ApprovalsScreen() {
  const { data, isLoading, isError, refetch, isRefetching } = usePendingProposals();
  const [resolved, setResolved] = useState<Record<string, ProposalResolution>>({});

  // Don't render a live card for something we've already decided this session.
  const active = (data?.proposals ?? []).filter((p) => !resolved[p.id]);
  const resolvedEntries = Object.entries(resolved);

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{ padding: spacing.xl, paddingTop: spacing['4xl'], gap: spacing.lg, flexGrow: 1 }}
        refreshControl={
          <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={colors.accent} />
        }
      >
        <Header count={active.length} />

        {resolvedEntries.map(([id, res]) => (
          <ResolvedBanner
            key={`resolved-${id}`}
            result={res}
            onDismiss={() =>
              setResolved((prev) => {
                const next = { ...prev };
                delete next[id];
                return next;
              })
            }
          />
        ))}

        {isLoading ? (
          <Stack gap={spacing.lg}>
            <Skeleton height={260} />
            <Skeleton height={260} />
          </Stack>
        ) : isError ? (
          <ErrorState onRetry={refetch} />
        ) : active.length === 0 && resolvedEntries.length === 0 ? (
          <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingVertical: spacing['6xl'], gap: spacing.sm }}>
            <Text variant="heading" center>
              Nothing to decide
            </Text>
            <Text variant="callout" color="textMuted" center>
              When the agent finds a trade, it’ll show up here for your approval.
            </Text>
          </View>
        ) : (
          active.map((p) => (
            <ProposalCard
              key={p.id}
              proposal={p}
              onResolved={(id, res) => setResolved((prev) => ({ ...prev, [id]: res }))}
            />
          ))
        )}
      </ScrollView>
    </Screen>
  );
}

function Header({ count }: { count: number }) {
  return (
    <Stack gap={2}>
      <Text variant="label" upper color="accent">
        approvals
      </Text>
      <Text variant="title">{count > 0 ? `${count} waiting on you` : 'All clear'}</Text>
    </Stack>
  );
}

function ResolvedBanner({ result, onDismiss }: { result: ProposalResolution; onDismiss: () => void }) {
  return (
    <Animated.View entering={FadeIn}>
      <View
        style={{
          backgroundColor: result.ok ? colors.positiveWash : colors.negativeWash,
          borderRadius: radius.lg,
          padding: spacing.lg,
          gap: spacing.md,
        }}
      >
        <Row justify="space-between" align="flex-start" gap={spacing.md}>
          <Text variant="bodyStrong" color={result.ok ? 'positive' : 'negative'} style={{ flex: 1 }}>
            {result.message}
          </Text>
        </Row>
        <Button label="Dismiss" tone="ghost" onPress={onDismiss} />
      </View>
    </Animated.View>
  );
}
