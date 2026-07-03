/**
 * Theme builder. A theme is defined by ~18 raw tokens (RawPalette); washes and a
 * few derived tokens are computed here so each palette stays terse. The output
 * shape is the `Colors` object every screen consumes via `@/theme`.
 */

function hexToRgba(hex: string, a: number): string {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}

export interface RawPalette {
  bg: string;
  bgInset: string;
  surface: string;
  surfaceRaised: string;
  scrim: string;
  text: string;
  textMuted: string;
  textFaint: string;
  onAccent: string;
  border: string;
  borderStrong: string;
  accent: string;
  warm: string;
  positive: string;
  negative: string;
  warning: string;
  info: string;
  chart: readonly [string, string, string, string, string];
}

export function makeColors(p: RawPalette) {
  return {
    // Surfaces
    bg: p.bg,
    bgInset: p.bgInset,
    surface: p.surface,
    surfaceRaised: p.surfaceRaised,
    surfaceHover: p.surfaceRaised,
    scrim: p.scrim,
    // Barely-there row tint for zebra striping (faint over the base bg).
    stripe: hexToRgba(p.text, 0.022),
    // Text
    text: p.text,
    textMuted: p.textMuted,
    textFaint: p.textFaint,
    onAccent: p.onAccent,
    // Lines
    border: p.border,
    borderStrong: p.borderStrong,
    // Brand / interactive
    accent: p.accent,
    accentWash: hexToRgba(p.accent, 0.13),
    focus: p.accent,
    // Secondary warm accent
    warm: p.warm,
    warmWash: hexToRgba(p.warm, 0.22),
    // Financial semantics
    positive: p.positive,
    negative: p.negative,
    positiveWash: hexToRgba(p.positive, 0.14),
    negativeWash: hexToRgba(p.negative, 0.14),
    // Status
    warning: p.warning,
    info: p.info,
    danger: p.negative,
    // Data-viz series
    chart: [p.chart[0], p.chart[1], p.chart[2], p.chart[3], p.chart[4]] as [
      string,
      string,
      string,
      string,
      string,
    ],
  };
}

export type Colors = ReturnType<typeof makeColors>;
