/**
 * Icon — a tiny hand-drawn line-icon set (SVG, no extra deps). Stroke-based so it
 * inherits color + reads crisp at tab-bar size. Add new glyphs here as needed.
 */
import Svg, { Circle, Line, Path, Polyline, Rect } from 'react-native-svg';

import { colors } from '@/theme';

export type IconName =
  | 'portfolio'
  | 'watchlist'
  | 'inbox'
  | 'chat'
  | 'settings'
  | 'bell'
  | 'plus'
  | 'chevron-left'
  | 'chevron-right'
  | 'check'
  | 'close';

export function Icon({
  name,
  size = 24,
  color = colors.textMuted,
  strokeWidth = 1.8,
}: {
  name: IconName;
  size?: number;
  color?: string;
  strokeWidth?: number;
}) {
  const sw = strokeWidth;
  const common = { stroke: color, strokeWidth: sw, fill: 'none', strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      {name === 'portfolio' && (
        <>
          <Polyline points="3,14 8,9 12,12 21,4" {...common} />
          <Polyline points="16,4 21,4 21,9" {...common} />
          <Line x1="3" y1="20" x2="21" y2="20" {...common} />
        </>
      )}
      {name === 'watchlist' && (
        <>
          <Path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" {...common} />
          <Circle cx="12" cy="12" r="2.6" {...common} />
        </>
      )}
      {name === 'inbox' && (
        <>
          <Path d="M3 13l2.5-7.5A2 2 0 0 1 7.4 4h9.2a2 2 0 0 1 1.9 1.5L21 13v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5Z" {...common} />
          <Path d="M3 13h5l1.5 2.2h5L16 13h5" {...common} />
        </>
      )}
      {name === 'chat' && (
        <Path d="M4 5h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4 4v-4H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z" {...common} />
      )}
      {name === 'settings' && (
        <>
          <Line x1="4" y1="7" x2="20" y2="7" {...common} />
          <Line x1="4" y1="12" x2="20" y2="12" {...common} />
          <Line x1="4" y1="17" x2="20" y2="17" {...common} />
          <Circle cx="9" cy="7" r="2.2" fill={colors.bg} stroke={color} strokeWidth={sw} />
          <Circle cx="15" cy="12" r="2.2" fill={colors.bg} stroke={color} strokeWidth={sw} />
          <Circle cx="8" cy="17" r="2.2" fill={colors.bg} stroke={color} strokeWidth={sw} />
        </>
      )}
      {name === 'bell' && (
        <>
          <Path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6Z" {...common} />
          <Path d="M10.5 19a1.8 1.8 0 0 0 3 0" {...common} />
        </>
      )}
      {name === 'plus' && (
        <>
          <Line x1="12" y1="5" x2="12" y2="19" {...common} />
          <Line x1="5" y1="12" x2="19" y2="12" {...common} />
        </>
      )}
      {name === 'chevron-left' && <Polyline points="15,5 8,12 15,19" {...common} />}
      {name === 'chevron-right' && <Polyline points="9,5 16,12 9,19" {...common} />}
      {name === 'check' && <Polyline points="4,12 10,18 20,6" {...common} />}
      {name === 'close' && (
        <>
          <Line x1="6" y1="6" x2="18" y2="18" {...common} />
          <Line x1="18" y1="6" x2="6" y2="18" {...common} />
        </>
      )}
    </Svg>
  );
}
