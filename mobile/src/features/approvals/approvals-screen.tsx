/**
 * Approvals — the hero surface. The agent's pending proposals as a stack of
 * rich cards. Calm when empty ("nothing to decide"), urgent when not.
 */
import { RefreshControl, ScrollView, View } from 'react-native';

import { usePendingProposals } from '@/api/queries';
import { ErrorState, Screen, Skeleton, Stack, Text } from '@/components/ui';
import { colors, spacing } from '@/theme';

import { ProposalCard } from './proposal-card';

export function ApprovalsScreen() {
  const { data, isLoading, isError, refetch, isRefetching } = usePendingProposals();
  const proposals = data?.proposals ?? [];

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{ padding: spacing.xl, paddingTop: spacing['4xl'], gap: spacing.lg, flexGrow: 1 }}
        refreshControl={
          <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={colors.accent} />
        }
      >
        <Header count={proposals.length} />

        {isLoading ? (
          <Stack gap={spacing.lg}>
            <Skeleton height={260} />
            <Skeleton height={260} />
          </Stack>
        ) : isError ? (
          <ErrorState onRetry={refetch} />
        ) : proposals.length === 0 ? (
          <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingVertical: spacing['6xl'], gap: spacing.sm }}>
            <Text variant="heading" center>
              Nothing to decide
            </Text>
            <Text variant="callout" color="textMuted" center>
              When the agent finds a trade, it’ll show up here for your approval.
            </Text>
          </View>
        ) : (
          proposals.map((p) => <ProposalCard key={p.id} proposal={p} />)
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
      <Text variant="title">
        {count > 0 ? `${count} waiting on you` : 'All clear'}
      </Text>
    </Stack>
  );
}
