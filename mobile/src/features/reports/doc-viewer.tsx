import type { ReactElement } from 'react';
import {
  ScrollView,
  Text as RNText,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import Markdown from 'react-native-markdown-display';

import { fonts, spacing, useColors, useThemeMode } from '@/theme';

function stripHtml(html: string): string {
  return html.replace(/<[^>]+>/g, '').replace(/\n{3,}/g, '\n\n');
}

export function DocViewer({
  content,
  mode,
}: {
  content: string;
  mode: 'text' | 'html' | 'markdown';
}): ReactElement {
  // Subscribe to the theme so we re-render on switch, and key the render below on
  // it — react-native-markdown-display memoizes on content, so without a fresh key
  // it keeps the old palette's styles after a theme change.
  const { resolved } = useThemeMode();
  const colors = useColors();
  // Build styles at RENDER time. A module-scope style object captures the palette
  // once at load and then shows stale, wrong-theme colors after a theme switch
  // (e.g. dark-theme near-white text rendered on the light theme → invisible).
  const monoTextStyle: TextStyle = {
    fontFamily: fonts.mono,
    fontSize: 13,
    lineHeight: 21,
    color: colors.text,
  };

  const markdownStyles: Record<string, TextStyle | ViewStyle> = {
    body: { color: colors.textMuted, fontFamily: fonts.mono, fontSize: 13, lineHeight: 21 },
    heading1: { color: colors.accent, fontFamily: fonts.mono, fontSize: 20, fontWeight: '700', marginTop: 4, marginBottom: 12 },
    heading2: { color: colors.accent, fontFamily: fonts.mono, fontSize: 17, fontWeight: '700', marginTop: 20, marginBottom: 10 },
    heading3: { color: colors.accent, fontFamily: fonts.mono, fontSize: 15, fontWeight: '700', marginTop: 14, marginBottom: 8 },
    paragraph: { marginTop: 0, marginBottom: 12 },
    strong: { color: colors.text, fontWeight: '700' },
    em: { color: colors.text, fontStyle: 'italic' },
    bullet_list: { marginBottom: 12 },
    ordered_list: { marginBottom: 12 },
    list_item: { marginBottom: 6 },
    blockquote: {
      borderLeftWidth: 2,
      borderLeftColor: colors.border,
      paddingLeft: 12,
      marginVertical: 12,
      backgroundColor: 'transparent',
      color: colors.textMuted,
    },
    hr: { backgroundColor: colors.border, height: 1, marginVertical: 20 },
    link: { color: colors.accent, textDecorationLine: 'underline' },
    code_inline: { fontFamily: fonts.mono, color: colors.text, backgroundColor: colors.surfaceRaised },
    fence: { fontFamily: fonts.mono, color: colors.text, backgroundColor: colors.surfaceRaised, padding: 10, borderRadius: 6 },
    code_block: { fontFamily: fonts.mono, color: colors.text, backgroundColor: colors.surfaceRaised, padding: 10, borderRadius: 6 },
    table: { borderColor: colors.textFaint, borderWidth: 1, borderRadius: 6, marginVertical: 12 },
    tr: { borderColor: colors.textFaint, borderBottomWidth: 1 },
    th: { padding: 6, borderColor: colors.textFaint, backgroundColor: colors.bgInset, color: colors.text, fontWeight: '700' },
    td: { padding: 6, borderColor: colors.textFaint, color: colors.text },
  };

  const textContent = mode === 'html' ? stripHtml(content) : content;

  return (
    <ScrollView
      key={resolved}
      style={{ flex: 1 }}
      contentContainerStyle={{
        paddingHorizontal: spacing.md,
        paddingBottom: spacing['5xl'],
      }}
    >
      {mode === 'markdown' ? (
        <Markdown style={markdownStyles}>{content}</Markdown>
      ) : (
        <RNText style={monoTextStyle}>{textContent}</RNText>
      )}
    </ScrollView>
  );
}
