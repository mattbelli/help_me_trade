/**
 * Chat message rendering: the user / assistant bubbles, the streaming "typing"
 * and tool-hint affordances, and the proposal hand-off card.
 *
 * MISSION RULE (References/MOBILE_CHAT_BRIEF.md §"proposals"): chat NEVER shows
 * an approve/reject UI. When the copilot drafts a trade it emits a `proposal`
 * event; we render a compact hand-off card that deep-links to the existing,
 * Face-ID-gated Approvals flow (the Inbox tab). The proposal is already in
 * GET /api/proposals/pending, so it's waiting there when the user lands.
 */
import { useEffect } from 'react';
import { ScrollView, View } from 'react-native';
import Markdown from 'react-native-markdown-display';
import Animated, {
  FadeIn,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated';

import { Icon } from '@/components/ui/icon';
import { Eyebrow, PressableScale, Row, Stack, Text } from '@/components/ui';
import { useNav } from '@/state/nav';
import { hairline, haptics, radius, spacing, useColors, useThemeMode } from '@/theme';

import { useChatMarkdownStyles } from './chat-markdown-styles';
import { stripToolMarkup } from './strip-tool-markup';
import { ToolTimeline } from './tool-timeline';
import type { ChatTurn, ProposalRef } from './use-chat';

// Wide tables scroll sideways WITHIN the bubble so the page never scrolls with
// them; normal prose still wraps (only the table rule is overridden).
const markdownRules = {
  table: (node: { key: string }, children: React.ReactNode, _parent: unknown, styles: { table?: object }) => (
    <ScrollView key={node.key} horizontal showsHorizontalScrollIndicator={false}>
      <View style={styles.table}>{children}</View>
    </ScrollView>
  ),
};

export function MessageBubble({ turn }: { turn: ChatTurn }) {
  const colors = useColors();
  const isUser = turn.role === 'user';
  // Scrub any leaked tool-call XML from assistant text before it renders.
  const body = isUser ? turn.content : stripToolMarkup(turn.content);
  const showTyping = !isUser && turn.streaming && body.length === 0;

  return (
    <Animated.View
      entering={FadeIn.duration(160)}
      style={{ alignItems: isUser ? 'flex-end' : 'flex-start', marginBottom: spacing.md }}
    >
      <View
        style={{
          maxWidth: '88%',
          backgroundColor: isUser ? colors.accentWash : colors.surface,
          borderWidth: hairline,
          borderColor: isUser ? 'transparent' : colors.border,
          borderRadius: radius.xl,
          // A subtle tail: square off the bottom corner on the sender's side.
          borderBottomRightRadius: isUser ? radius.sm : radius.xl,
          borderBottomLeftRadius: isUser ? radius.xl : radius.sm,
          paddingHorizontal: spacing.lg,
          paddingVertical: spacing.md,
          overflow: 'hidden',
        }}
      >
        {!isUser ? (
          // The tool-call trail sits above the answer; it de-emphasises once the
          // answer text starts landing so the answer stays the focus.
          <ToolTimeline steps={turn.toolSteps} dimmed={body.length > 0} />
        ) : null}
        {showTyping ? (
          <TypingDots />
        ) : body.length > 0 ? (
          isUser ? (
            // User input is plain text — never markdown.
            <Text variant="body" color="text">
              {body}
            </Text>
          ) : (
            <AssistantMarkdown body={body} failed={!!turn.failed} />
          )
        ) : null}
      </View>

      {/* Proposal hand-offs sit just under the assistant bubble. */}
      {turn.proposals.length > 0 ? (
        <Stack gap={spacing.sm} style={{ alignSelf: 'stretch', marginTop: spacing.sm }}>
          {turn.proposals.map((p) => (
            <ProposalHandoff key={p.proposalId} proposal={p} />
          ))}
        </Stack>
      ) : null}
    </Animated.View>
  );
}

/**
 * Themed markdown for assistant turns (tables, headings, lists, code). Keyed on
 * the resolved theme so react-native-markdown-display — which memoises on
 * content — re-renders with the right palette after a theme switch, and on the
 * growing `body` so partial/streaming markdown re-renders each token. Incomplete
 * syntax mid-stream renders as plain text rather than crashing.
 */
function AssistantMarkdown({ body, failed }: { body: string; failed: boolean }) {
  const { resolved } = useThemeMode();
  const styles = useChatMarkdownStyles(failed);
  return (
    <Markdown key={`${resolved}:${body.length}`} style={styles} rules={markdownRules}>
      {body}
    </Markdown>
  );
}

/** Compact, tappable hand-off → jumps to Approvals (Inbox tab). NOT an approve. */
export function ProposalHandoff({ proposal }: { proposal: ProposalRef }) {
  const colors = useColors();
  const { openInbox } = useNav();
  return (
    <PressableScale
      onPress={() => {
        haptics.tap();
        openInbox();
      }}
      style={{
        borderRadius: radius.lg,
        borderWidth: hairline,
        borderColor: colors.accent,
        backgroundColor: colors.accentWash,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.md,
      }}
    >
      <Row gap={spacing.md} align="center">
        <View
          style={{
            width: 34,
            height: 34,
            borderRadius: radius.md,
            backgroundColor: colors.surface,
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Icon name="inbox" size={18} color={colors.accent} />
        </View>
        <Stack gap={2} style={{ flex: 1 }}>
          <Eyebrow color="accent">proposal ready</Eyebrow>
          <Text variant="bodyStrong" numberOfLines={2}>
            {proposal.summary}
          </Text>
          <Text variant="caption" color="textMuted">
            Review &amp; approve in Approvals
          </Text>
        </Stack>
        <Icon name="chevron-right" size={18} color={colors.accent} />
      </Row>
    </PressableScale>
  );
}

/** Three dots that breathe — the assistant is thinking before the first token. */
function TypingDots() {
  return (
    <Row gap={5} style={{ paddingVertical: 3 }}>
      <Dot delay={0} />
      <Dot delay={160} />
      <Dot delay={320} />
    </Row>
  );
}

function Dot({ delay }: { delay: number }) {
  const colors = useColors();
  const o = useSharedValue(0.3);
  useEffect(() => {
    o.value = withRepeat(
      withSequence(
        withTiming(0.3, { duration: delay }),
        withTiming(1, { duration: 380 }),
        withTiming(0.3, { duration: 380 }),
      ),
      -1,
      false,
    );
  }, [o, delay]);
  const style = useAnimatedStyle(() => ({ opacity: o.value }));
  return (
    <Animated.View
      style={[style, { width: 7, height: 7, borderRadius: 4, backgroundColor: colors.textMuted }]}
    />
  );
}
