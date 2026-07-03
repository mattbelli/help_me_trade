import { RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { useRouter } from 'expo-router';

import { useDeletePriceAlert, usePriceAlerts } from '@/api/queries';
import type { PriceAlert } from '@/api/types';
import { Icon } from '@/components/ui/icon';
import {
  BackRow,
  EmptyState,
  ErrorState,
  Eyebrow,
  Money,
  PressableScale,
  Row,
  Screen,
  Skeleton,
  Stack,
  Text,
} from '@/components/ui';
import { usePullRefresh } from '@/hooks/use-pull-refresh';
import { spacing, useColors } from '@/theme';

export default function AlertsScreen() {
  const colors = useColors();
  const router = useRouter();
  const alerts = usePriceAlerts();
  const deleteAlert = useDeletePriceAlert();
  const items = alerts.data?.alerts ?? [];
  const { refreshing, onRefresh } = usePullRefresh(() => alerts.refetch());

  if (alerts.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
          <BackRow onBack={() => router.back()} />
          <ErrorState onRetry={alerts.refetch} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <FlashList
        data={items}
        keyExtractor={(item) => String(item.id)}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />
        }
        contentContainerStyle={{ paddingHorizontal: spacing.xl, paddingBottom: 110 }}
        ListHeaderComponent={
          <View style={{ paddingTop: spacing.sm, paddingBottom: spacing.lg }}>
            <Stack gap={spacing.lg}>
              <BackRow onBack={() => router.back()} />
              <Stack gap={2}>
                <Eyebrow color="accent">alerts</Eyebrow>
                <Text variant="title">Active alerts</Text>
              </Stack>
            </Stack>
          </View>
        }
        renderItem={({ item }) => (
          <AlertListRow
            alert={item}
            onOpenSymbol={() => router.push(`/symbol/${item.symbol}`)}
            onDelete={() => deleteAlert.mutate(item.id)}
            deleting={deleteAlert.isPending}
          />
        )}
        ListEmptyComponent={
          alerts.isLoading ? (
            <Stack gap={spacing.md}>
              <Skeleton height={52} />
              <Skeleton height={52} />
              <Skeleton height={52} />
            </Stack>
          ) : (
            <EmptyState
              title="No active alerts"
              subtitle="Set price alerts from a stock's detail page."
            />
          )
        }
      />
    </Screen>
  );
}

function AlertListRow({
  alert,
  onOpenSymbol,
  onDelete,
  deleting,
}: {
  alert: PriceAlert;
  onOpenSymbol: () => void;
  onDelete: () => void;
  deleting?: boolean;
}) {
  const colors = useColors();
  return (
    <Row justify="space-between" align="center" style={{ paddingVertical: spacing.md }}>
      <PressableScale onPress={onOpenSymbol} style={{ flex: 1 }}>
        <Stack gap={2}>
          <Text variant="bodyStrong">{alert.symbol}</Text>
          <Row gap={spacing.xs} align="center">
            <Text variant="body">{alert.direction}</Text>
            <Money value={alert.price} size="sm" />
          </Row>
          {alert.note ? (
            <Text variant="caption" color="textMuted">
              {alert.note}
            </Text>
          ) : null}
        </Stack>
      </PressableScale>
      <PressableScale onPress={onDelete} disabled={deleting} haptic>
        <View style={{ padding: spacing.sm }}>
          <Icon name="close" size={18} color={colors.textMuted} />
        </View>
      </PressableScale>
    </Row>
  );
}
