/**
 * Color tokens — the "Carbon" dark system, lifted verbatim from the web app
 * (frontend/app/globals.css) and extended for a native, full-bleed feel.
 *
 * Dark, premium, calm-but-alive. One palette; no light mode in v1.
 * Semantic names (bg/surface/text/...) are what screens consume — never hardcode hex.
 */

/** Raw palette — the ground-truth hex values shared with the web cockpit. */
const palette = {
  // Backgrounds, darkest → raised
  ink900: '#0b0d10', // deepest (on-accent text, behind everything)
  ink850: '#0e1013', // inset / sunken
  ink800: '#131519', // app background
  ink750: '#181b21', // panel / card surface
  ink700: '#1e2228', // secondary surface / hover
  // Hairlines
  line700: '#262b33', // default border
  line600: '#333a44', // strong border
  // Text, brightest → faintest
  fg50: '#e9e7e3', // primary text
  fg300: '#9aa1ab', // muted / secondary text
  fg500: '#616873', // faint / tertiary text
  // Brand + signal
  teal: '#5ad1be', // accent (primary action, focus, chart-1)
  tealDim: 'rgba(90, 209, 190, 0.12)', // accent wash
  green: '#5bc98a', // positive P&L / bullish / win
  red: '#ef6f6c', // negative P&L / bearish / destructive
  blue: '#6cb0ff', // chart-2 / info
  violet: '#b08cff', // chart-3
  yellow: '#e3b341', // chart-4 / caution
  amber: '#e0975b', // chart-5
  black: '#000000',
  white: '#ffffff',
} as const;

export const colors = {
  // Surfaces (back-to-front elevation ramp)
  bg: palette.ink800,
  bgInset: palette.ink850,
  surface: palette.ink750,
  surfaceRaised: palette.ink700,
  surfaceHover: palette.ink700,
  scrim: 'rgba(8, 10, 13, 0.66)', // modal/sheet backdrop

  // Text
  text: palette.fg50,
  textMuted: palette.fg300,
  textFaint: palette.fg500,
  onAccent: palette.ink900, // text/icon on a teal/green/red fill

  // Lines
  border: palette.line700,
  borderStrong: palette.line600,

  // Brand / interactive
  accent: palette.teal,
  accentWash: palette.tealDim,
  focus: palette.teal,

  // Secondary accent — warm bronze/gold (from the web palette). Used for the active
  // tab, extended-hours badges, and other warm highlights against the teal primary.
  warm: palette.amber,
  warmWash: 'rgba(224, 151, 91, 0.22)',

  // Financial semantics — the two colors that carry the most meaning in this app
  positive: palette.green,
  negative: palette.red,
  positiveWash: 'rgba(91, 201, 138, 0.14)',
  negativeWash: 'rgba(239, 111, 108, 0.14)',

  // Status
  warning: palette.yellow,
  info: palette.blue,
  danger: palette.red,

  // Data-viz series
  chart: [palette.teal, palette.blue, palette.violet, palette.yellow, palette.amber] as const,

  // Raw passthrough for the rare case a component needs the literal
  palette,
} as const;

/** Directional P&L color: green for ≥0, red for <0. The app's most-used helper. */
export function pnlColor(value: number): string {
  return value >= 0 ? colors.positive : colors.negative;
}

export function pnlWash(value: number): string {
  return value >= 0 ? colors.positiveWash : colors.negativeWash;
}

export type Colors = typeof colors;
