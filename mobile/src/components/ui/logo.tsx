/**
 * Logo — the `stand_in` mark: two vertical bars, identical 20×54 footprint,
 * offset upward left→right. The hollow bar is the user (stepped away); the
 * solid bar is the agent standing in, one step higher because it kept working.
 * Tinted from the theme's NEUTRALS (text/textMuted), not accent/warm — the
 * mark is greyscale by design. Spec + masters: repo `brand/README.md`.
 * The hollow is carved inward (evenodd), never an outer stroke, so both bars
 * keep the exact same silhouette. For tiny cuts (≤16px favicons) use the flat
 * two-grey fallback in brand/ — in-app sizes (≥22) always render the true hollow.
 */
import Svg, { Path, Rect } from 'react-native-svg';

import { useColors } from '@/theme';

export function Logo({ size = 22 }: { size?: number }) {
  const colors = useColors();
  return (
    <Svg width={size} height={size} viewBox="0 0 120 120">
      <Path
        d="M34 38 h20 v54 h-20 Z M39.5 43.5 h9 v43 h-9 Z"
        fill={colors.textMuted}
        fillRule="evenodd"
      />
      <Rect x={66} y={26} width={20} height={54} fill={colors.text} />
    </Svg>
  );
}
