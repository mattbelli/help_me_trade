import type { ReactElement } from 'react';
import {
  ScrollView,
  Text as RNText,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import Markdown from 'react-native-markdown-display';

import { colors, fonts, spacing } from '@/theme';

const monoTextStyle: TextStyle = {
  fontFamily: fonts.mono,
  fontSize: 13,
  lineHeight: 21,
  color: colors.textMuted,
};

function stripHtml(html: string): string {
  return html.replace(/<[^>]+>/g, '').replace(/\n{3,}/g, '\n\n');
}

const markdownStyles: Record<string, TextStyle | ViewStyle> = {
  body: { color: colors.textMuted, fontFamily: fonts.mono, fontSize: 13, lineHeight: 21 },
  heading1: {
    color: colors.text,
    fontFamily: fonts.mono,
    fontSize: 20,
    fontWeight: '600',
    marginTop: 4,
    marginBottom: 12,
  },
  heading2: {
    color: colors.text,
    fontFamily: fonts.mono,
    fontSize: 17,
    fontWeight: '600',
    marginTop: 20,
    marginBottom: 10,
  },
  heading3: {
    color: colors.text,
    fontFamily: fonts.mono,
    fontSize: 15,
    fontWeight: '600',
    marginTop: 14,
    marginBottom: 8,
  },
  paragraph: { marginTop: 0, marginBottom: 12 },
  strong: { color: colors.text, fontWeight: '600' },
  em: { fontStyle: 'italic' },
  bullet_list: { marginBottom: 12 },
  ordered_list: { marginBottom: 12 },
  list_item: { marginBottom: 4 },
  blockquote: {
    borderLeftWidth: 2,
    borderLeftColor: colors.border,
    paddingLeft: 12,
    marginVertical: 12,
    backgroundColor: 'transparent',
  },
  hr: { backgroundColor: colors.border, height: 1, marginVertical: 20 },
  link: { color: colors.accent, textDecorationLine: 'underline' },
  code_inline: {
    fontFamily: fonts.mono,
    color: colors.text,
    backgroundColor: colors.surfaceRaised,
  },
  fence: {
    fontFamily: fonts.mono,
    color: colors.textMuted,
    backgroundColor: colors.surfaceRaised,
    padding: 10,
    borderRadius: 6,
  },
  code_block: {
    fontFamily: fonts.mono,
    color: colors.textMuted,
    backgroundColor: colors.surfaceRaised,
    padding: 10,
    borderRadius: 6,
  },
  table: {
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 6,
    marginVertical: 12,
  },
  th: { padding: 6, color: colors.text, fontWeight: '600' },
  td: { padding: 6, borderColor: colors.border },
};

export function DocViewer({
  content,
  mode,
}: {
  content: string;
  mode: 'text' | 'html' | 'markdown';
}): ReactElement {
  const textContent = mode === 'html' ? stripHtml(content) : content;

  return (
    <ScrollView
      style={{ flex: 1 }}
      contentContainerStyle={{
        paddingHorizontal: spacing.xl,
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
