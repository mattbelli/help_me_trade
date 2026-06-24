/**
 * Inbox — one feed of everything the agent does: pending approvals (actionable),
 * order proposals, fills/exits, and triggered price alerts. Filter chips up top,
 * newest first, unread floated up + marked with a dot. Pending approvals are
 * always actionable (the full Face-ID-gated card) and sort to the top.
 *
 * Replaces the old Approvals + Activity pages. Away-from-app approvals still
 * arrive via the lock-screen push.
 */
import { useEffect, useRef, useState } from 'react';
import { RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';

import { getRecentAlerts } from '@/api/endpoints';
import { usePendingProposals } from '@/api/queries';
import type { MarketAlert, PendingProposal } from '@/api/types';
import { Badge, Eyebrow, formatUsd, PressableScale, Row, Screen, Stack, Text } from '@/components/ui';
import { ProposalCard } from '@/features/approvals/proposal-card';
import { usePullRefresh } from '@/hooks/use-pull-refresh';
import { useAlerts } from '@/state/alerts';
import { colors, hairline, spacing } from '@/theme';

type Filter = 'all' | 'approvals' | 'orders' | 'alerts';
const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'approvals', label: 'Approvals' },
  { key: 'orders', label: 'Orders' },
  { key: 'alerts', label: 'Alerts' },
];

type Row_ =
  | { kind: 'approval'; id: string; ts: number; unread: true; proposal: PendingProposal }
  | { kind: 'event'; id: string; ts: number; unread: boolean; alert: MarketAlert; category: Filter };

function categorize(a: MarketAlert): Filter {
  const at = a.action?.type;
  const src = a.source ?? '';
  if (at === 'order_proposal' || src === 'proposal') return 'approvals';
  if (src === 'execution' || src === 'fill' || src === 'exit_engine' || at === 'trade') return 'orders';
  return 'alerts'; // rule / signal_scan / price_alert / other
}

function alertTitle(a: MarketAlert): string {
  if (a.source === 'price_alert' && a.symbol && a.direction && a.target_price != null) {
    const now = a.trigger_price != null ? ` — now ${formatUsd(a.trigger_price)}` : '';
    const note = a.note ? ` (${a.note})` : '';
    return `${a.symbol} ${a.direction} ${formatUsd(a.target_price)}${now}${note}`;
  }
  return a.action?.summary ?? a.action?.message ?? a.rule_name ?? a.symbol ?? 'Activity';
}

function tone(cat: Filter): 'accent' | 'positive' | 'warning' | 'neutral' {
  if (cat === 'approvals') return 'accent';
  if (cat === 'orders') return 'positive';
  if (cat === 'alerts') return 'warning';
  return 'neutral';
}

function timeAgo(iso: string): string {
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function InboxScreen() {
  const { alerts, seed, isRead, markRead, markAllRead } = useAlerts();
  const pending = usePendingProposals();
  const [filter, setFilter] = useState<Filter>('all');
  const [loadingSeed, setLoadingSeed] = useState(true);
  const seeded = useRef(false);

  const { refreshing, onRefresh } = usePullRefresh(() =>
    Promise.all([pending.refetch(), getRecentAlerts().then((r) => seed(r.alerts ?? [])).catch(() => undefined)]),
  );

  // Seed once from the REST recent-alerts; live updates arrive via the store (WS).
  useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    let cancelled = false;
    void getRecentAlerts()
      .then((r) => { if (!cancelled) seed(r.alerts ?? []); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoadingSeed(false); });
    return () => { cancelled = true; };
  }, [seed]);

  const rows: Row_[] = [];
  for (const p of pending.data?.proposals ?? []) {
    rows.push({ kind: 'approval', id: `p-${p.id}`, ts: tsOf(p.created_at), unread: true, proposal: p });
  }
  for (const a of alerts) {
    rows.push({ kind: 'event', id: `a-${a.id}`, ts: tsOf(a.ts), unread: !isRead(a.id), alert: a, category: categorize(a) });
  }
  const filtered = rows.filter((r) => {
    if (filter === 'all') return true;
    return r.kind === 'approval' ? filter === 'approvals' : r.category === filter;
  });
  // Unread first, then newest.
  filtered.sort((x, y) => Number(y.unread) - Number(x.unread) || y.ts - x.ts);

  const unreadCount = rows.filter((r) => r.unread).length;

  return (
    <Screen>
      <FlashList
        data={filtered}
        keyExtractor={(r) => r.id}
        contentContainerStyle={{ paddingHorizontal: spacing.xl, paddingBottom: 110 }}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />
        }
        ListHeaderComponent={
          <Stack gap={spacing.md} style={{ paddingTop: spacing.sm, paddingBottom: spacing.sm }}>
            <Row justify="space-between" align="flex-end">
              <Stack gap={2}>
                <Eyebrow color="accent">inbox</Eyebrow>
                <Text variant="title">{unreadCount > 0 ? `${unreadCount} unread` : 'All caught up'}</Text>
              </Stack>
              {unreadCount > 0 ? (
                <PressableScale onPress={markAllRead} haptic={false}>
                  <Text variant="caption" color="textMuted">Mark all read</Text>
                </PressableScale>
              ) : null}
            </Row>
            <Row gap={spacing.xs}>
              {FILTERS.map((f) => {
                const active = f.key === filter;
                return (
                  <PressableScale key={f.key} onPress={() => setFilter(f.key)} haptic={false}>
                    <View style={{ paddingHorizontal: spacing.md, paddingVertical: spacing.sm - 1, borderRadius: 999, backgroundColor: active ? colors.accentWash : colors.surface, borderWidth: hairline, borderColor: active ? colors.accent : colors.border }}>
                      <Text variant="caption" style={{ color: active ? colors.accent : colors.textMuted }}>{f.label}</Text>
                    </View>
                  </PressableScale>
                );
              })}
            </Row>
          </Stack>
        }
        renderItem={({ item }) =>
          item.kind === 'approval' ? (
            <View style={{ paddingVertical: spacing.sm }}>
              <ProposalCard proposal={item.proposal} />
            </View>
          ) : (
            <EventRow item={item} onPress={() => markRead(item.alert.id)} />
          )
        }
        ListEmptyComponent={
          loadingSeed && rows.length === 0 ? null : (
            <View style={{ alignItems: 'center', paddingVertical: spacing['6xl'], gap: spacing.sm }}>
              <Text variant="heading" center>Quiet for now</Text>
              <Text variant="callout" color="textMuted" center>The agent's actions and approvals will stream here.</Text>
            </View>
          )
        }
      />
    </Screen>
  );
}

function EventRow({ item, onPress }: { item: Extract<Row_, { kind: 'event' }>; onPress: () => void }) {
  const { alert, category, unread } = item;
  return (
    <PressableScale onPress={onPress} haptic={false}>
      <Row gap={spacing.md} align="center" style={{ paddingVertical: spacing.md, borderBottomWidth: hairline, borderBottomColor: colors.border }}>
        <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: unread ? colors.accent : 'transparent' }} />
        <Stack gap={3} style={{ flex: 1 }}>
          <Text variant={unread ? 'bodyStrong' : 'body'} numberOfLines={2}>{alertTitle(alert)}</Text>
          <Row gap={spacing.sm}>
            <Badge label={category} tone={tone(category)} />
            {alert.symbol ? <Text variant="caption" color="textFaint">{alert.symbol}</Text> : null}
          </Row>
        </Stack>
        <Text variant="caption" color="textFaint">{alert.ts ? timeAgo(alert.ts) : ''}</Text>
      </Row>
    </PressableScale>
  );
}

function tsOf(iso?: string | null): number {
  if (!iso) return 0;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) ? t : 0;
}
