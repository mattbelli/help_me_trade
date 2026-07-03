import type { WebCustomTheme } from '@/api/types';

import { type RawPalette } from './make-colors';
import { BASE_PALETTES } from './themes';

export function webThemeToPalette(t: WebCustomTheme): RawPalette {
  const base =
    BASE_PALETTES[t.base] ??
    (t.colorScheme === 'light' ? BASE_PALETTES.light : BASE_PALETTES.carbon);
  const tk = t.tokens ?? {};
  return {
    ...base,
    bg: tk.bg ?? base.bg,
    surface: tk.surface ?? base.surface,
    border: tk.border ?? base.border,
    text: tk.fg ?? base.text,
    textMuted: tk.fgDim ?? base.textMuted,
    accent: tk.accent ?? base.accent,
    positive: tk.green ?? base.positive,
    negative: tk.red ?? base.negative,
    chart: [
      tk.chartAccent ?? base.chart[0],
      base.chart[1],
      base.chart[2],
      base.chart[3],
      base.chart[4],
    ],
  };
}

export function webThemeIsLight(t: WebCustomTheme): boolean {
  return t.colorScheme === 'light';
}
