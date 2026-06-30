/**
 * Type system.
 *
 * Two families:
 *  - text: the system UI face (SF Pro on iOS) for everything conversational.
 *  - mono: tabular numerals for money, prices, P&L — numbers must not jitter as
 *    they tick, so financial figures are ALWAYS mono + tabular-nums.
 *
 * Sizes follow a restrained ramp; weight + color do most of the hierarchy work.
 */

import { Platform, type TextStyle } from 'react-native';

const SANS = Platform.select({ ios: 'System', default: 'System' });
// SF Mono ships on iOS; "Menlo" is the reliable fallback face name.
const MONO = Platform.select({ ios: 'Menlo', default: 'monospace' });

/** Apply to any numeric figure so digits are fixed-width (no layout jitter on tick). */
export const tabularNums: TextStyle = {
  fontVariant: ['tabular-nums'],
};

type Variant = {
  fontSize: number;
  lineHeight: number;
  fontWeight: TextStyle['fontWeight'];
  letterSpacing?: number;
  fontFamily?: string;
};

export const type = {
  // Display — the big glanceable number on Home (portfolio value)
  display: { fontFamily: MONO, fontSize: 44, lineHeight: 48, fontWeight: '700', letterSpacing: -0.5, ...tabularNums },
  // Headline — screen/section titles
  title: { fontFamily: SANS, fontSize: 28, lineHeight: 34, fontWeight: '700', letterSpacing: -0.3 },
  heading: { fontFamily: SANS, fontSize: 20, lineHeight: 26, fontWeight: '600', letterSpacing: -0.2 },
  // Body
  body: { fontFamily: SANS, fontSize: 16, lineHeight: 22, fontWeight: '400' },
  bodyStrong: { fontFamily: SANS, fontSize: 16, lineHeight: 22, fontWeight: '600' },
  // Supporting
  callout: { fontFamily: SANS, fontSize: 14, lineHeight: 19, fontWeight: '500' },
  caption: { fontFamily: SANS, fontSize: 12, lineHeight: 16, fontWeight: '500' },
  // Labels — uppercase eyebrow / section kickers
  label: { fontFamily: SANS, fontSize: 11, lineHeight: 14, fontWeight: '700', letterSpacing: 0.8 },
  // Money / figures
  figureLg: { fontFamily: MONO, fontSize: 24, lineHeight: 28, fontWeight: '600', letterSpacing: -0.2, ...tabularNums },
  figure: { fontFamily: MONO, fontSize: 16, lineHeight: 20, fontWeight: '500', ...tabularNums },
  figureSm: { fontFamily: MONO, fontSize: 13, lineHeight: 16, fontWeight: '500', ...tabularNums },
} satisfies Record<string, Variant>;

export const fonts = { sans: SANS, mono: MONO } as const;
export type TypeVariant = keyof typeof type;
