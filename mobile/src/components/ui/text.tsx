/**
 * Text — the single text primitive. Drives all hierarchy through `variant`
 * (the type ramp) and `color` (semantic token), so screens never reach for raw
 * fontSize/fontWeight/hex. Financial figures should use <Money>/<Pct> (figure.tsx).
 */
import { Text as RNText, type TextProps as RNTextProps } from 'react-native';

import { fonts, type, useColors, type Colors, type TypeVariant } from '@/theme';

type ColorToken = 'text' | 'textMuted' | 'textFaint' | 'accent' | 'positive' | 'negative' | 'warning' | 'onAccent';

export interface TextProps extends RNTextProps {
  variant?: TypeVariant;
  color?: ColorToken | string;
  /** Uppercase eyebrow styling (pairs with variant="label"). */
  upper?: boolean;
  center?: boolean;
}

// Built per-call (not a module-scope const) so it reflects the active theme after
// a palette swap — see theme/colors.ts.
function resolveColor(c: ColorToken | string, palette: Colors): string {
  const tokens: Record<ColorToken, string> = {
    text: palette.text,
    textMuted: palette.textMuted,
    textFaint: palette.textFaint,
    accent: palette.accent,
    positive: palette.positive,
    negative: palette.negative,
    warning: palette.warning,
    onAccent: palette.onAccent,
  };
  return (tokens as Record<string, string>)[c] ?? c;
}

export function Text({
  variant = 'body',
  color = 'text',
  upper,
  center,
  style,
  ...rest
}: TextProps) {
  const palette = useColors();
  return (
    <RNText
      style={[
        type[variant],
        { color: resolveColor(color, palette) },
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

/**
 * ScreenTitle — the per-screen identifier: lowercase accent monospace, a touch
 * larger than a section Eyebrow. One uniform treatment across every screen (the
 * "minimal eyebrow" title system) so screens stop drifting in their headers.
 */
export function ScreenTitle({
  children,
  style,
}: {
  children: RNTextProps['children'];
  style?: RNTextProps['style'];
}) {
  const palette = useColors();
  return (
    <RNText
      maxFontSizeMultiplier={1.2}
      style={[
        { fontFamily: fonts.mono, fontSize: 14, letterSpacing: 1.2, color: resolveColor('accent', palette), textTransform: 'lowercase' },
        style,
      ]}
    >
      {children}
    </RNText>
  );
}

/**
 * Eyebrow — the lowercase, letter-spaced monospace section label that gives the
 * app its terminal cue (e.g. "portfolio value", "holdings"). Faint by default.
 */
export function Eyebrow({
  children,
  color = 'textFaint',
  style,
}: {
  children: RNTextProps['children'];
  color?: ColorToken | string;
  style?: RNTextProps['style'];
}) {
  const palette = useColors();
  return (
    <RNText
      maxFontSizeMultiplier={1.2}
      style={[
        { fontFamily: fonts.mono, fontSize: 11, letterSpacing: 1.6, color: resolveColor(color, palette), textTransform: 'lowercase' },
        style,
      ]}
    >
      {children}
    </RNText>
  );
}
