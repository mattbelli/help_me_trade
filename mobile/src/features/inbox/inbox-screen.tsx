/**
 * Inbox — one feed of everything the agent does: pending approvals (actionable),
 * order proposals, fills/exits, and triggered price alerts. Filter chips up top,
 * newest first, unread floated up + marked with a dot. Pending approvals are
 * always actionable (the full Face-ID-gated card) and sort to the top.
 *
 * Replaces the old Approvals + Activity pages. Away-from-app approvals still
 * arrive via the lock-screen push.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { useRouter, type Href } from 'expo-router';

import { getRecentAlerts, getRecentNotifications } from '@/api/endpoints';
import { usePendingProposals } from '@/api/queries';
import type { FeedNotification, MarketAlert, PendingProposal } from '@/api/types';
import { Badge, formatUsd, PageHeading, PressableScale, Row, Screen, Stack, Text } from '@/components/ui';
import { ProposalCard } from '@/features/approvals/proposal-card';
import { notificationRoute } from '@/features/inbox/notification-route';
import { usePullRefresh } from '@/hooks/use-pull-refresh';
import { useAlerts } from '@/state/alerts';
import { colors, hairline, spacing } from '@/theme';

type Filter = 'all' | 'approvals' | 'orders' | 'reports' | 'alerts';
const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'approvals', label: 'Approvals' },
  { key: 'orders', label: 'Orders' },
  { key: 'reports', label: 'Reports' },
  { key: 'alerts', label: 'Alerts' },
];

type Row_ =
  | { kind: 'approval'; id: string; ts: number; unread: true; proposal: PendingProposal }
  | { kind: 'event'; id: string; ts: number; unread: boolean; alert: MarketAlert; category: Filter }
  | { kind: 'notification'; id: string; ts: number; unread: boolean; notification: FeedNotification; category: Filter };

function categorize(a: MarketAlert): Filter {
  const at = a.action?.type;
  const src = a.source ?? '';
  if (at === 'order_proposal' || src === 'proposal') return 'approvals';
  if (src === 'execution' || src === 'fill' || src === 'exit_engine' || at === 'trade') return 'orders';
  return 'alerts'; // rule / signal_scan / price_alert / other
}

function categorizeNotification(n: FeedNotification): Filter {
  switch (n.kind) {
    case 'proposal_missed':
      return 'approvals';
    case 'fill_report':
    case 'risk_breaker':
    case 'risk_block':
      return 'orders';
    case 'report_ready':
    case 'research_ready':
      return 'reports';
    default:
      return 'alerts';
  }
}

/** Serv mirrors notifications into /api/alerts/recent; the notification stream renders them. */
function isGenuineAlert(raw: unknown): raw is MarketAlert {
  if (typeof raw !== 'object' || raw === null) return false;
  return typeof (raw as { kind?: unknown }).kind !== 'string';
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

function severityTone(severity: string): 'accent' | 'negative' | 'warning' | 'neutral' {
  if (severity === 'critical') return 'negative';
  if (severity === 'warning') return 'warning';
  if (severity === 'info') return 'accent';
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
  const { alerts, notifications, seed, seedNotifications, readIds, markRead, markAllRead, clearRead } = useAlerts();
  const router = useRouter();
  const pending = usePendingProposals();
  const [filter, setFilter] = useState<Filter>('all');
  const [loadingSeed, setLoadingSeed] = useState(true);
  const seeded = useRef(false);

  const { refreshing, onRefresh } = usePullRefresh(() =>
    Promise.all([
      pending.refetch(),
      getRecentAlerts().then((r) => seed((r.alerts ?? []).filter(isGenuineAlert))).catch(() => undefined),
      getRecentNotifications().then((r) => seedNotifications(r.notifications ?? [])).catch(() => undefined),
    ]),
  );

  // Seed once from REST; live updates arrive via the store (WS).
  useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    let cancelled = false;
    void Promise.all([
      getRecentAlerts()
        .then((r) => { if (!cancelled) seed((r.alerts ?? []).filter(isGenuineAlert)); })
        .catch(() => {}),
      getRecentNotifications()
        .then((r) => { if (!cancelled) seedNotifications(r.notifications ?? []); })
        .catch(() => {}),
    ]).finally(() => { if (!cancelled) setLoadingSeed(false); });
    return () => { cancelled = true; };
  }, [seed, seedNotifications]);

  // Build rows with a STABLE identity — FlashList v2 re-anchors scroll on every
  // data-array change, so rebuilding this each render made the list jump/hide on
  // load and after marking read. Memoize so it only changes when inputs do.
  const { filtered, unreadCount, readCount } = useMemo(() => {
    const rows: Row_[] = [];
    for (const p of pending.data?.proposals ?? []) {
      rows.push({ kind: 'approval', id: `p-${p.id}`, ts: tsOf(p.created_at), unread: true, proposal: p });
    }
    for (const a of alerts) {
      rows.push({ kind: 'event', id: `a-${a.id}`, ts: tsOf(a.ts), unread: !readIds.has(a.id), alert: a, category: categorize(a) });
    }
    for (const n of notifications) {
      rows.push({
        kind: 'notification',
        id: `n-${n.id}`,
        ts: tsOf(n.created_at),
        unread: !readIds.has(n.id),
        notification: n,
        category: categorizeNotification(n),
      });
    }
    const next = rows.filter((r) => {
      if (filter === 'all') return true;
      return r.kind === 'approval' ? filter === 'approvals' : r.category === filter;
    });
    // Actionable approvals pinned on top; everything else is chronological
    // (newest first) regardless of read state — so a read item stays in place
    // (greyed, no dot) like an email inbox rather than sinking out of view.
    next.sort((x, y) => Number(y.kind === 'approval') - Number(x.kind === 'approval') || y.ts - x.ts);
    return {
      filtered: next,
      unreadCount: rows.filter((r) => r.unread).length,
      readCount: rows.filter((r) => (r.kind === 'event' || r.kind === 'notification') && !r.unread).length,
    };
  }, [pending.data?.proposals, alerts, notifications, readIds, filter]);

  return (
    <Screen>
      <FlashList
        data={filtered}
        keyExtractor={(r) => r.id}
        // v2 re-anchors scroll when data changes; this inbox is top-anchored, so
        // keep it put instead of jumping on seed / mark-read.
        maintainVisibleContentPosition={{ disabled: true }}
        contentContainerStyle={{ paddingHorizontal: spacing.xl, paddingBottom: 110 }}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />
        }
        ListHeaderComponent={
          <Stack gap={spacing.md} style={{ paddingTop: spacing.sm, paddingBottom: spacing.sm }}>
            <Row justify="space-between" align="center">
              <PageHeading title="inbox" />
              <Row gap={spacing.lg} align="center">
                {unreadCount > 0 ? (
                  <PressableScale onPress={markAllRead} haptic={false}>
                    <Text variant="caption" color="textMuted">Mark all read</Text>
                  </PressableScale>
                ) : null}
                {readCount > 0 ? (
                  <PressableScale onPress={clearRead} haptic>
                    <Text variant="caption" color="textMuted">Clear read</Text>
                  </PressableScale>
                ) : null}
              </Row>
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
        renderItem={({ item }) => {
          if (item.kind === 'approval') {
            return (
              <View style={{ paddingVertical: spacing.sm }}>
                <ProposalCard proposal={item.proposal} />
              </View>
            );
          }
          if (item.kind === 'notification') {
            return (
              <NotificationRow
                item={item}
                onPress={() => {
                  markRead(item.notification.id);
                  const route = notificationRoute(item.notification);
                  if (route) router.push(route as Href);
                }}
              />
            );
          }
          return (
            <EventRow
              item={item}
              onPress={() => {
                markRead(item.alert.id);
                if (item.alert.symbol) router.push(`/symbol/${item.alert.symbol}`);
              }}
            />
          );
        }}
        ListEmptyComponent={
          loadingSeed && filtered.length === 0 ? null : (
            <View style={{ alignItems: 'center', paddingVertical: spacing['6xl'], gap: spacing.sm }}>
              <Text variant="heading" center>Quiet for now</Text>
              <Text variant="callout" color="textMuted" center>The agent&apos;s actions and approvals will stream here.</Text>
            </View>
          )
        }
      />
    </Screen>
  );
}

function NotificationRow({ item, onPress }: { item: Extract<Row_, { kind: 'notification' }>; onPress: () => void }) {
  const { notification, category, unread } = item;
  return (
    <PressableScale onPress={onPress} haptic={false}>
      <Row gap={spacing.md} align="center" style={{ paddingVertical: spacing.md, borderBottomWidth: hairline, borderBottomColor: colors.border }}>
        <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: unread ? colors.accent : 'transparent' }} />
        <Stack gap={3} style={{ flex: 1 }}>
          <Text variant={unread ? 'bodyStrong' : 'body'} numberOfLines={2}>{notification.title}</Text>
          <Row gap={spacing.sm}>
            <Badge label={category} tone={severityTone(notification.severity)} />
            {notification.body ? (
              <Text variant="caption" color="textFaint" numberOfLines={1} style={{ flex: 1 }}>
                {notification.body}
              </Text>
            ) : null}
          </Row>
        </Stack>
        <Text variant="caption" color="textFaint">{notification.created_at ? timeAgo(notification.created_at) : ''}</Text>
      </Row>
    </PressableScale>
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
