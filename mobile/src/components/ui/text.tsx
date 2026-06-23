/**
 * Text — the single text primitive. Drives all hierarchy through `variant`
 * (the type ramp) and `color` (semantic token), so screens never reach for raw
 * fontSize/fontWeight/hex. Financial figures should use <Money>/<Pct> (figure.tsx).
 */
import { Text as RNText, type TextProps as RNTextProps } from 'react-native';

import { colors, type, type TypeVariant } from '@/theme';

type ColorToken = 'text' | 'textMuted' | 'textFaint' | 'accent' | 'positive' | 'negative' | 'warning' | 'onAccent';

export interface TextProps extends RNTextProps {
  variant?: TypeVariant;
  color?: ColorToken | string;
  /** Uppercase eyebrow styling (pairs with variant="label"). */
  upper?: boolean;
  center?: boolean;
}

const colorTokens: Record<ColorToken, string> = {
  text: colors.text,
  textMuted: colors.textMuted,
  textFaint: colors.textFaint,
  accent: colors.accent,
  positive: colors.positive,
  negative: colors.negative,
  warning: colors.warning,
  onAccent: colors.onAccent,
};

function resolveColor(c: ColorToken | string): string {
  return (colorTokens as Record<string, string>)[c] ?? c;
}

export function Text({
  variant = 'body',
  color = 'text',
  upper,
  center,
  style,
  ...rest
}: TextProps) {
  return (
    <RNText
      style={[
        type[variant],
        { color: resolveColor(color) },
        upper && { textTransform: 'uppercase' },
        center && { textAlign: 'center' },
        style,
      ]}
      // Cap dynamic-type scaling so the dense cockpit layouts don't break.
      maxFontSizeMultiplier={1.3}
      {...rest}
    />
  );
}
