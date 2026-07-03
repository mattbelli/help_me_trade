/**
 * Chat — the trading copilot. A streaming conversation: the assistant bubble
 * grows as tokens arrive, tool work shows a faint "thinking" hint, and any trade
 * the agent drafts appears as a hand-off card that deep-links to the Face-ID
 * Approvals flow (chat never approves — that's the mission rule).
 *
 * Streaming runs through `streamChat()` (expo/fetch SSE), behind useChat().
 * Until serv ships /api/chat/stream it streams from a local stub (config.CHAT_STUB).
 */
import { useEffect, useRef, useState } from 'react';
import {
  Keyboard,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Icon } from '@/components/ui/icon';
import { PageHeading, PressableScale, Row, Screen, Stack, Text } from '@/components/ui';
import { colors, hairline, haptics, radius, spacing } from '@/theme';

import { MessageBubble } from './chat-bubbles';
import { useChat } from './use-chat';

const SUGGESTIONS = [
  'What was my unrealized P&L today?',
  'What trades were made today?',
  "What's my year-to-date performance?",
];

// The floating tab pill (~52pt) sits at the bottom; lift the composer above it
// when the keyboard is closed so the two clear each other with breathing room.
const TAB_PILL_CLEARANCE = 78;

export function ChatScreen() {
  const insets = useSafeAreaInsets();
  const { turns, status, error, send, stop, reset } = useChat();
  const [draft, setDraft] = useState('');
  const [keyboardUp, setKeyboardUp] = useState(false);
  const scrollRef = useRef<ScrollView>(null);

  const streaming = status === 'streaming';
  const canSend = draft.trim().length > 0 && !streaming;

  useEffect(() => {
    const show = Keyboard.addListener('keyboardWillShow', () => setKeyboardUp(true));
    const hide = Keyboard.addListener('keyboardWillHide', () => setKeyboardUp(false));
    return () => {
      show.remove();
      hide.remove();
    };
  }, []);

  function onSend() {
    const text = draft.trim();
    if (!text || streaming) return;
    haptics.tap();
    send(text);
    setDraft('');
  }

  return (
    <Screen edges={{ top: true, bottom: false }}>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        {/* Header */}
        <Row
          justify="space-between"
          align="center"
          style={{ paddingHorizontal: spacing.xl, paddingTop: spacing.sm, paddingBottom: spacing.sm }}
        >
          <PageHeading title="chat" />
          {turns.length > 0 ? (
            <PressableScale onPress={reset} haptic>
              <Text variant="caption" color="textMuted">
                New chat
              </Text>
            </PressableScale>
          ) : null}
        </Row>

        <ScrollView
          ref={scrollRef}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="interactive"
          contentContainerStyle={{
            flexGrow: 1,
            paddingHorizontal: spacing.xl,
            paddingTop: spacing.sm,
            paddingBottom: spacing.lg,
            justifyContent: turns.length === 0 ? 'center' : 'flex-start',
          }}
          onContentSizeChange={() => scrollRef.current?.scrollToEnd({ animated: true })}
        >
          {turns.length === 0 ? (
            <EmptyState onPick={(s) => send(s)} />
          ) : (
            turns.map((t) => <MessageBubble key={t.id} turn={t} />)
          )}
        </ScrollView>

        {/* Error banner */}
        {error ? (
          <View style={{ paddingHorizontal: spacing.xl, paddingBottom: spacing.sm }}>
            <View
              style={{
                backgroundColor: colors.negativeWash,
                borderRadius: radius.lg,
                paddingHorizontal: spacing.lg,
                paddingVertical: spacing.md,
              }}
            >
              <Text variant="caption" color="negative">
                {error}
              </Text>
            </View>
          </View>
        ) : null}

        {/* Composer */}
        <View
          style={{
            paddingHorizontal: spacing.xl,
            paddingTop: spacing.sm,
            paddingBottom: keyboardUp ? spacing.sm : insets.bottom + TAB_PILL_CLEARANCE,
          }}
        >
          <Row gap={spacing.sm} align="flex-end">
            <View
              style={{
                flex: 1,
                backgroundColor: colors.surface,
                borderWidth: hairline,
                borderColor: colors.border,
                borderRadius: radius.xl,
                paddingHorizontal: spacing.lg,
                paddingVertical: Platform.OS === 'ios' ? spacing.md : spacing.sm,
              }}
            >
              <TextInput
                value={draft}
                onChangeText={setDraft}
                placeholder="Ask your copilot…"
                placeholderTextColor={colors.textFaint}
                multiline
                style={{
                  color: colors.text,
                  fontSize: 16,
                  lineHeight: 21,
                  maxHeight: 120,
                  padding: 0,
                }}
                onSubmitEditing={onSend}
                returnKeyType="default"
              />
            </View>

            <PressableScale
              onPress={streaming ? stop : onSend}
              disabled={!streaming && !canSend}
              haptic={false}
              style={{
                width: 44,
                height: 44,
                borderRadius: radius.pill,
                backgroundColor: streaming ? colors.surfaceRaised : colors.accent,
                alignItems: 'center',
                justifyContent: 'center',
                opacity: !streaming && !canSend ? 0.4 : 1,
              }}
            >
              <Icon
                name={streaming ? 'close' : 'chevron-up'}
                size={22}
                color={streaming ? colors.textMuted : colors.onAccent}
              />
            </PressableScale>
          </Row>
        </View>
      </KeyboardAvoidingView>
    </Screen>
  );
}

function EmptyState({ onPick }: { onPick: (s: string) => void }) {
  return (
    <Stack gap={spacing.xl} style={{ alignItems: 'center' }}>
      <View
        style={{
          width: 64,
          height: 64,
          borderRadius: 999,
          backgroundColor: colors.accentWash,
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Icon name="chat" size={30} color={colors.accent} />
      </View>
      <Stack gap={spacing.sm} style={{ alignItems: 'center' }}>
        <Text variant="title" center>
          Ask the agent
        </Text>
        <Text variant="callout" color="textMuted" center>
          Its thesis, your portfolio, what it&apos;s watching — and when it&apos;s worth a trade.
        </Text>
      </Stack>
      <Stack gap={spacing.sm} style={{ alignSelf: 'stretch', marginTop: spacing.sm }}>
        {SUGGESTIONS.map((s) => (
          <PressableScale
            key={s}
            onPress={() => {
              haptics.tap();
              onPick(s);
            }}
            style={{
              borderWidth: hairline,
              borderColor: colors.border,
              backgroundColor: colors.surface,
              borderRadius: radius.lg,
              paddingHorizontal: spacing.lg,
              paddingVertical: spacing.md,
            }}
          >
            <Row justify="space-between" align="center">
              <Text variant="body" color="textMuted">
                {s}
              </Text>
              <Icon name="chevron-right" size={16} color={colors.textFaint} />
            </Row>
          </PressableScale>
        ))}
      </Stack>
    </Stack>
  );
}
