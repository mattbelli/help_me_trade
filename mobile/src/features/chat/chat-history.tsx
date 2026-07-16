/**
 * ChatHistory — the sessions-list overlay for the copilot. Lists persisted chats
 * (serv returns them pinned-first, then newest-first). Tap a row to load it into
 * the transcript; swipe a row left to reveal Delete; tap the pin to pin/unpin.
 *
 * Presented as a full-screen Modal over the chat page (chat is a pager surface,
 * not a router route). Re-fetches on open so it reflects turns just streamed.
 */
import { useEffect } from 'react';
import { Alert, Modal, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import Swipeable from 'react-native-gesture-handler/Swipeable';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import {
  useChatSessions,
  useDeleteChatSession,
  usePinChatSession,
} from '@/api/queries';
import type { ChatSession } from '@/api/types';
import { Icon } from '@/components/ui/icon';
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PressableScale,
  Row,
  Screen,
  Skeleton,
  Stack,
  Text,
} from '@/components/ui';
import { hairline, haptics, radius, spacing, useColors } from '@/theme';

function timeAgo(iso: string): string {
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function ChatHistory({
  visible,
  onClose,
  onOpenSession,
}: {
  visible: boolean;
  onClose: () => void;
  onOpenSession: (id: string) => void;
}) {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const sessions = useChatSessions();
  const pin = usePinChatSession();
  const del = useDeleteChatSession();

  // Refresh the list each time the sheet opens so it reflects the latest turn.
  useEffect(() => {
    if (visible) void sessions.refetch();
  }, [visible]); // eslint-disable-line react-hooks/exhaustive-deps

  const list = sessions.data?.sessions ?? [];

  const confirmDelete = (session: ChatSession) => {
    Alert.alert('Delete chat?', 'This removes the saved conversation.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: () => {
          haptics.warning();
          del.mutate(session.id);
        },
      },
    ]);
  };

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <Screen edges={{ top: true, bottom: false }}>
        <Row
          justify="space-between"
          align="center"
          style={{ paddingHorizontal: spacing.xl, paddingTop: spacing.sm, paddingBottom: spacing.sm }}
        >
          <PageHeading title="history" />
          <PressableScale onPress={onClose} haptic>
            <Icon name="close" size={22} color={colors.textMuted} />
          </PressableScale>
        </Row>

        {sessions.isError ? (
          <View style={{ flex: 1, paddingHorizontal: spacing.xl }}>
            <ErrorState onRetry={sessions.refetch} />
          </View>
        ) : sessions.isLoading ? (
          <Stack gap={spacing.md} style={{ paddingHorizontal: spacing.xl, paddingTop: spacing.sm }}>
            <Skeleton height={56} />
            <Skeleton height={56} />
            <Skeleton height={56} />
          </Stack>
        ) : list.length === 0 ? (
          <View style={{ flex: 1, justifyContent: 'center', paddingHorizontal: spacing.xl }}>
            <EmptyState title="No saved chats" subtitle="Your conversations show up here once you start chatting." />
          </View>
        ) : (
          <FlashList
            data={list}
            keyExtractor={(s) => s.id}
            contentContainerStyle={{
              paddingHorizontal: spacing.xl,
              paddingTop: spacing.sm,
              paddingBottom: insets.bottom + spacing.xl,
            }}
            renderItem={({ item }) => (
              <SessionRow
                session={item}
                onOpen={() => {
                  haptics.tap();
                  onOpenSession(item.id);
                }}
                onDelete={() => confirmDelete(item)}
                onTogglePin={() => {
                  haptics.selection();
                  pin.mutate({ id: item.id, pinned: !item.pinned });
                }}
              />
            )}
          />
        )}
      </Screen>
    </Modal>
  );
}

function SessionRow({
  session,
  onOpen,
  onDelete,
  onTogglePin,
}: {
  session: ChatSession;
  onOpen: () => void;
  onDelete: () => void;
  onTogglePin: () => void;
}) {
  const colors = useColors();

  const rightActions = () => (
    <PressableScale
      onPress={onDelete}
      haptic={false}
      style={{
        width: 80,
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: colors.negativeWash,
        borderRadius: radius.lg,
        marginLeft: spacing.sm,
      }}
    >
      <Icon name="trash" size={22} color={colors.negative} />
    </PressableScale>
  );

  return (
    <Swipeable renderRightActions={rightActions} overshootRight={false}>
      <PressableScale
        onPress={onOpen}
        style={{
          backgroundColor: colors.surface,
          borderWidth: hairline,
          borderColor: colors.border,
          borderRadius: radius.lg,
          paddingHorizontal: spacing.lg,
          paddingVertical: spacing.md,
          marginBottom: spacing.sm,
        }}
      >
        <Row justify="space-between" align="center" gap={spacing.md}>
          <Stack gap={2} style={{ flex: 1 }}>
            <Text variant="bodyStrong" numberOfLines={1}>
              {session.title || 'Untitled chat'}
            </Text>
            <Text variant="caption" color="textMuted">
              {timeAgo(session.updated_at)} ago
            </Text>
          </Stack>
          <PressableScale onPress={onTogglePin} haptic={false} hitSlop={10}>
            <Icon
              name="pin"
              size={18}
              color={session.pinned ? colors.accent : colors.textFaint}
            />
          </PressableScale>
        </Row>
      </PressableScale>
    </Swipeable>
  );
}
