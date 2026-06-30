/**
 * The selectable palettes. `carbon` is the default and matches the original
 * "Carbon" dark system (lifted from the web app). The rest are user-pickable in
 * Settings; `system` (in theme-context) maps to light/carbon by the OS scheme.
 */
import { makeColors, type RawPalette } from './make-colors';

const carbon: RawPalette = {
  bg: '#131519',
  bgInset: '#0e1013',
  surface: '#181b21',
  surfaceRaised: '#1e2228',
  scrim: 'rgba(8, 10, 13, 0.66)',
  text: '#e9e7e3',
  textMuted: '#9aa1ab',
  textFaint: '#616873',
  onAccent: '#0b0d10',
  border: '#262b33',
  borderStrong: '#333a44',
  accent: '#5ad1be',
  warm: '#e0975b',
  positive: '#5bc98a',
  negative: '#ef6f6c',
  warning: '#e3b341',
  info: '#6cb0ff',
  chart: ['#5ad1be', '#6cb0ff', '#b08cff', '#e3b341', '#e0975b'],
};

const warmCarbon: RawPalette = {
  bg: '#141311',
  bgInset: '#0f0e0c',
  surface: '#1c1a16',
  surfaceRaised: '#232019',
  scrim: 'rgba(10, 8, 6, 0.66)',
  text: '#e9e6e0',
  textMuted: '#a09a8d',
  textFaint: '#6b6457',
  onAccent: '#0d0b08',
  border: '#2b2820',
  borderStrong: '#3a352a',
  // Warm leads with orange (teal becomes the secondary) so it reads distinct
  // from Carbon, which leads with teal.
  accent: '#e0975b',
  warm: '#5ad1be',
  positive: '#5bc98a',
  negative: '#ef6f6c',
  warning: '#e3b341',
  info: '#6cb0ff',
  chart: ['#e0975b', '#5ad1be', '#b08cff', '#e3b341', '#6cb0ff'],
};

const oled: RawPalette = {
  bg: '#000000',
  bgInset: '#000000',
  surface: '#0c0e11',
  surfaceRaised: '#14171c',
  scrim: 'rgba(0, 0, 0, 0.72)',
  text: '#f4f3f0',
  textMuted: '#868d98',
  textFaint: '#555c66',
  onAccent: '#000000',
  border: '#1e232b',
  borderStrong: '#2b323c',
  accent: '#5ad1be',
  warm: '#e0975b',
  positive: '#5bc98a',
  negative: '#ef6f6c',
  warning: '#e3b341',
  info: '#6cb0ff',
  chart: ['#5ad1be', '#6cb0ff', '#b08cff', '#e3b341', '#e0975b'],
};

const light: RawPalette = {
  bg: '#f4f2ec',
  bgInset: '#eae6dd',
  surface: '#ffffff',
  surfaceRaised: '#faf8f3',
  scrim: 'rgba(20, 18, 14, 0.35)',
  text: '#1b1e23',
  textMuted: '#5f6873',
  textFaint: '#8a8f99',
  onAccent: '#ffffff',
  border: '#e4ded3',
  borderStrong: '#d3ccbe',
  accent: '#2f9e8c',
  warm: '#bd7f43',
  positive: '#2f9e63',
  negative: '#d4544f',
  warning: '#b8862a',
  info: '#3a7fd0',
  chart: ['#2f9e8c', '#3a7fd0', '#7a5cc0', '#b8862a', '#bd7f43'],
};

export const themes = {
  carbon: makeColors(carbon),
  warmCarbon: makeColors(warmCarbon),
  oled: makeColors(oled),
  light: makeColors(light),
} as const;

/** Explicit palette keys (not including `system`, which resolves to one of these). */
export type ThemeKey = keyof typeof themes;

/** What the user picks in Settings — a fixed palette or follow-the-OS. */
export type ThemeMode = ThemeKey | 'system';

/** Whether a palette is light, used for status-bar style + system mapping. */
export const isLightTheme = (key: ThemeKey): boolean => key === 'light';

/** Display labels + order for the Settings picker. */
export const THEME_OPTIONS: { mode: ThemeMode; label: string }[] = [
  { mode: 'system', label: 'Auto' },
  { mode: 'carbon', label: 'Carbon' },
  { mode: 'warmCarbon', label: 'Warm' },
  { mode: 'oled', label: 'OLED' },
  { mode: 'light', label: 'Light' },
];
