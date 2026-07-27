/**
 * EquityScrub — the Home equity curve: Sparkline's look (pnl-colored line +
 * gradient fill) upgraded to Skia with a long-press scrub crosshair, the same
 * gesture pattern as SkiaChart (220ms long-press pan; plain scrolls fail
 * through to the list). No pan/zoom — the visible window IS the selected
 * range, so scrubbing maps x directly to a point index.
 *
 * Readout strings are precomputed per point on the JS side (≤ ~240 points),
 * so the per-frame worklets only index into arrays.
 */
import { useMemo, useState } from 'react';
import { View } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import { runOnJS, useDerivedValue, useSharedValue, withTiming } from 'react-native-reanimated';
import {
  Canvas,
  Circle,
  Line,
  LinearGradient,
  Path,
  Skia,
  Text as SkText,
  matchFont,
} from '@shopify/react-native-skia';

import type { PortfolioHistoryPoint } from '@/api/types';
import { fonts, haptics, pnlColor, useColors } from '@/theme';

const LEGEND_H = 34; // scrub readout gutter above the curve
const PAD_Y = 4;

const valueFont = matchFont({ fontFamily: fonts.mono, fontSize: 15 });
const dateFont = matchFont({ fontFamily: fonts.mono, fontSize: 11 });

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function usd0(v: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(v);
}

interface Readout {
  ys: number[]; // pixel y per point
  values: string[]; // "$300,123"
  subs: string[]; // "Jul 14  14:30 · +$123 (+0.41%)"
}

function clamp(v: number, lo: number, hi: number): number {
  'worklet';
  return v < lo ? lo : v > hi ? hi : v;
}

export function EquityScrub({
  points,
  height = 144,
}: {
  points: PortfolioHistoryPoint[];
  height?: number;
}) {
  'use no memo'; // React Compiler opt-out, same as SkiaChart: shared-value
  // writes in gesture callbacks trip the compiler's immutability rule.
  const colors = useColors();
  const [width, setWidth] = useState(0);

  const n = points.length;
  const curveTop = LEGEND_H + PAD_Y;
  const curveBottom = height - PAD_Y;

  const { linePath, areaPath, readout, stroke, stepX } = useMemo(() => {
    if (n < 2 || width <= 0) {
      return { linePath: null, areaPath: null, readout: null as Readout | null, stroke: colors.textFaint, stepX: 1 };
    }
    let min = Infinity;
    let max = -Infinity;
    for (const p of points) {
      if (p.equity < min) min = p.equity;
      if (p.equity > max) max = p.equity;
    }
    const span = max - min || 1;
    const dx = width / (n - 1);
    const spanMs = (points[n - 1].timestamp - points[0].timestamp) * 1000;
    const withClock = spanMs <= 35 * 86_400_000;

    const line = Skia.Path.Make();
    const ys = new Array<number>(n);
    const values = new Array<string>(n);
    const subs = new Array<string>(n);
    for (let i = 0; i < n; i++) {
      const p = points[i];
      const x = i * dx;
      const y = curveBottom - ((p.equity - min) / span) * (curveBottom - curveTop);
      ys[i] = y;
      if (i === 0) line.moveTo(x, y);
      else line.lineTo(x, y);

      const d = new Date(p.timestamp * 1000);
      const md = `${MONTHS[d.getMonth()]} ${d.getDate()}`;
      const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
      const when = withClock ? `${md}  ${hm}` : `${md}, ${d.getFullYear()}`;
      const pl = p.profit_loss;
      const sign = pl > 0 ? '+' : pl < 0 ? '−' : '';
      const pct = p.profit_loss_pct;
      values[i] = usd0(p.equity);
      subs[i] = `${when} · ${sign}${usd0(Math.abs(pl))} (${sign}${Math.abs(pct).toFixed(2)}%)`;
    }
    const area = line.copy();
    area.lineTo(width, height);
    area.lineTo(0, height);
    area.close();
    const dir = points[n - 1].equity - points[0].equity;
    return {
      linePath: line,
      areaPath: area,
      readout: { ys, values, subs } satisfies Readout,
      stroke: pnlColor(dir),
      stepX: dx,
    };
  }, [points, n, width, height, curveTop, curveBottom, colors]);

  // ── crosshair (long-press scrub) ──
  const crossOn = useSharedValue(0);
  const crossX = useSharedValue(0);
  const crossOpacity = useDerivedValue(() => withTiming(crossOn.value, { duration: 90 }));
  const idx = useDerivedValue(() =>
    n < 2 ? 0 : Math.round(clamp(crossX.value, 0, width) / stepX),
  );
  const dotX = useDerivedValue(() => idx.value * stepX);
  const dotY = useDerivedValue(() => readout?.ys[idx.value] ?? 0);
  const vLineP1 = useDerivedValue(() => ({ x: dotX.value, y: curveTop }));
  const vLineP2 = useDerivedValue(() => ({ x: dotX.value, y: curveBottom }));
  const valueText = useDerivedValue(() =>
    crossOn.value ? (readout?.values[idx.value] ?? '') : '',
  );
  const subText = useDerivedValue(() => (crossOn.value ? (readout?.subs[idx.value] ?? '') : ''));

  // Writes go through .set() — the React-Compiler-blessed SharedValue API —
  // because the immutability lint rule flags `.value =` in these callbacks.
  const scrub = Gesture.Pan()
    .maxPointers(1)
    .activateAfterLongPress(220)
    .onStart((e) => {
      crossOn.set(1);
      crossX.set(e.x);
      runOnJS(haptics.selection)();
    })
    .onUpdate((e) => {
      crossX.set(e.x);
    })
    .onFinalize(() => {
      crossOn.set(0);
    });

  if (n < 2) {
    return <View style={{ height }} onLayout={(e) => setWidth(e.nativeEvent.layout.width)} />;
  }

  return (
    <View style={{ height }} onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
      {width > 0 && linePath && areaPath && readout ? (
        <GestureDetector gesture={scrub}>
          <Canvas style={{ flex: 1 }}>
            <Path path={areaPath}>
              <LinearGradient
                start={{ x: 0, y: curveTop }}
                end={{ x: 0, y: height }}
                // Hex-alpha only works on #rrggbb tokens; custom (web-synced)
                // themes may use other formats — fall back to the raw color.
                colors={[
                  stroke.startsWith('#') && stroke.length === 7 ? `${stroke}38` : stroke,
                  'transparent',
                ]}
              />
            </Path>
            <Path
              path={linePath}
              style="stroke"
              strokeWidth={2}
              strokeJoin="round"
              strokeCap="round"
              color={stroke}
            />
            {/* scrub crosshair: vertical line + dot on the curve + readout */}
            <Line
              p1={vLineP1}
              p2={vLineP2}
              color={colors.textMuted}
              strokeWidth={1}
              opacity={crossOpacity}
            />
            <Circle cx={dotX} cy={dotY} r={4.5} color={stroke} opacity={crossOpacity} />
            <Circle cx={dotX} cy={dotY} r={2} color={colors.bg} opacity={crossOpacity} />
            <SkText x={0} y={15} text={valueText} font={valueFont} color={colors.text} opacity={crossOpacity} />
            <SkText x={0} y={30} text={subText} font={dateFont} color={colors.textMuted} opacity={crossOpacity} />
          </Canvas>
        </GestureDetector>
      ) : null}
    </View>
  );
}
