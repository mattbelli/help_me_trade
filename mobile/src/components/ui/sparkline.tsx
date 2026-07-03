/**
 * Sparkline — a tiny, dependency-light line chart for watchlist rows and the
 * Home glance. Colors itself by net direction (last vs first) unless a color is
 * forced. Uses react-native-svg; for the big equity curve we may move to Skia.
 */
import { useMemo } from 'react';
import { View } from 'react-native';
import Svg, { Path, Defs, LinearGradient, Stop } from 'react-native-svg';

import { pnlColor, useColors } from '@/theme';

export function Sparkline({
  data,
  width = 64,
  height = 24,
  color,
  fill = false,
  strokeWidth = 1.5,
}: {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  fill?: boolean;
  strokeWidth?: number;
}) {
  const colors = useColors();
  const { line, area, stroke, gradId } = useMemo(() => {
    const id = `spark-${Math.round(width)}-${Math.round(height)}-${data.length}`;
    if (!data || data.length < 2) {
      return { line: '', area: '', stroke: color ?? colors.textFaint, gradId: id };
    }
    const min = Math.min(...data);
    const max = Math.max(...data);
    const span = max - min || 1;
    const dx = width / (data.length - 1);
    const pts = data.map((v, i) => {
      const x = i * dx;
      const y = height - ((v - min) / span) * height;
      return [x, y] as const;
    });
    const d = pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
    const areaD = `${d} L${width.toFixed(2)},${height.toFixed(2)} L0,${height.toFixed(2)} Z`;
    const dir = data[data.length - 1] - data[0];
    return { line: d, area: areaD, stroke: color ?? pnlColor(dir), gradId: id };
  }, [data, width, height, color, colors]);

  if (!line) {
    return <View style={{ width, height }} />;
  }

  return (
    <Svg width={width} height={height}>
      {fill ? (
        <>
          <Defs>
            <LinearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <Stop offset="0" stopColor={stroke} stopOpacity={0.22} />
              <Stop offset="1" stopColor={stroke} stopOpacity={0} />
            </LinearGradient>
          </Defs>
          <Path d={area} fill={`url(#${gradId})`} />
        </>
      ) : null}
      <Path d={line} stroke={stroke} strokeWidth={strokeWidth} fill="none" strokeLinejoin="round" strokeLinecap="round" />
    </Svg>
  );
}
