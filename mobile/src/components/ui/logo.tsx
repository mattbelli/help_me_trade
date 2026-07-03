/**
 * Logo — the hmt mark: three stacked horizontal candlestick bars (flat edges,
 * scattered lengths, wick out both ends). Bars use the theme accent + warm
 * tokens, so it re-tints with the palette (teal/amber on Carbon, orange-led on
 * Warm, darker on Light) and reads at any size. The chunky app-icon weight is a
 * separate PNG asset.
 */
import Svg, { Line, Rect } from 'react-native-svg';

import { useColors } from '@/theme';

export function Logo({ size = 22 }: { size?: number }) {
  const colors = useColors();
  const teal = colors.accent;
  const amber = colors.warm;
  return (
    <Svg width={size} height={size} viewBox="0 0 120 120">
      <Line x1={20} y1={40} x2={74} y2={40} stroke={teal} strokeWidth={3.5} />
      <Rect x={28} y={33} width={38} height={14} fill={teal} />
      <Line x1={38} y1={60} x2={106} y2={60} stroke={amber} strokeWidth={3.5} />
      <Rect x={48} y={53} width={50} height={14} fill={amber} />
      <Line x1={18} y1={80} x2={70} y2={80} stroke={teal} strokeWidth={3.5} />
      <Rect x={26} y={73} width={34} height={14} fill={teal} />
    </Svg>
  );
}
