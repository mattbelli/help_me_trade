/**
 * useChatMarkdownStyles — a compact, chat-bubble-sized themed style map for
 * react-native-markdown-display. This is the tighter cousin of the full-document
 * report styles in features/reports/doc-viewer.tsx: smaller headings, denser
 * paragraph/list spacing so markdown fits inside an assistant bubble.
 *
 * Built at RENDER time from useColors() so it tracks the active theme (a
 * module-scope object would capture one palette and go stale after a theme
 * switch — see the same note in doc-viewer.tsx).
 */
import type { TextStyle, ViewStyle } from 'react-native';

import { fonts, useColors } from '@/theme';

export function useChatMarkdownStyles(
  failed: boolean,
): Record<string, TextStyle | ViewStyle> {
  const colors = useColors();
  // Failed turns render their text in the negative color (matches the old
  // plain-Text behaviour). The body color cascades to inline text.
  const bodyColor = failed ? colors.negative : colors.text;

  return {
    // Chat body matches Text variant="body" (SF Pro 16/22) — conversational,
    // not the report's mono face.
    body: { color: bodyColor, fontFamily: fonts.sans, fontSize: 16, lineHeight: 22 },
    heading1: { color: bodyColor, fontFamily: fonts.sans, fontSize: 18, fontWeight: '700', marginTop: 2, marginBottom: 6 },
    heading2: { color: bodyColor, fontFamily: fonts.sans, fontSize: 16, fontWeight: '700', marginTop: 8, marginBottom: 4 },
    heading3: { color: bodyColor, fontFamily: fonts.sans, fontSize: 15, fontWeight: '700', marginTop: 6, marginBottom: 4 },
    paragraph: { marginTop: 0, marginBottom: 8 },
    strong: { color: bodyColor, fontWeight: '700' },
    em: { color: bodyColor, fontStyle: 'italic' },
    bullet_list: { marginBottom: 8 },
    ordered_list: { marginBottom: 8 },
    list_item: { marginBottom: 3 },
    blockquote: {
      borderLeftWidth: 2,
      borderLeftColor: colors.border,
      paddingLeft: 10,
      marginVertical: 6,
      backgroundColor: 'transparent',
      color: colors.textMuted,
    },
    hr: { backgroundColor: colors.border, height: 1, marginVertical: 10 },
    link: { color: colors.accent, textDecorationLine: 'underline' },
    code_inline: { fontFamily: fonts.mono, fontSize: 14, color: colors.text, backgroundColor: colors.surfaceRaised },
    fence: { fontFamily: fonts.mono, fontSize: 13, color: colors.text, backgroundColor: colors.surfaceRaised, padding: 8, borderRadius: 6 },
    code_block: { fontFamily: fonts.mono, fontSize: 13, color: colors.text, backgroundColor: colors.surfaceRaised, padding: 8, borderRadius: 6 },
    // Compact mono cells so a 5–6 column financial table fits the bubble width
    // (no horizontal scroll) and digits line up. Small font + tight padding.
    table: { borderColor: colors.border, borderWidth: 1, borderRadius: 6, marginVertical: 8 },
    tr: { borderColor: colors.border, borderBottomWidth: 1 },
    th: { paddingVertical: 4, paddingHorizontal: 3, borderColor: colors.border, backgroundColor: colors.bgInset, color: colors.text, fontFamily: fonts.mono, fontSize: 11, fontWeight: '700' },
    td: { paddingVertical: 4, paddingHorizontal: 3, borderColor: colors.border, color: colors.text, fontFamily: fonts.mono, fontSize: 11 },
  };
}
