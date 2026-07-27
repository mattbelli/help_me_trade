/**
 * SkiaChart — native candlestick / line chart (replaces the WebView chart on
 * the symbol screen).
 *
 * Feel is the point: pan (drag history), pinch (candle density) and the
 * long-press crosshair run entirely on the UI thread — gestures write
 * Reanimated shared values, geometry is rebuilt per frame in worklets
 * (usePathValue / useDerivedValue) and handed straight to Skia. React never
 * re-renders during a gesture.
 *
 * Seams left for later (deliberately unbuilt — keep them cheap to fill):
 *  - volume pane: bump VOLUME_H — the price-pane math already leaves that
 *    gutter between the price pane and the time axis; render volume bars from
 *    the same `vp` viewport.
 *  - overlay series (SMA, alert price-levels, drawings): extra usePathValue /
 *    Line elements inside the clipped pane <Group> below, mapped through the
 *    same xFor/yFor helpers (see the live-price line for the pattern).
 */
/* eslint-disable react-hooks/immutability -- Reanimated shared-value writes inside
   RNGH gesture callbacks (Gesture.Pan().onStart(...)): the documented Reanimated
   pattern, but the React Compiler lint doesn't recognize those as event handlers. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { View } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import {
  cancelAnimation,
  runOnJS,
  useDerivedValue,
  useSharedValue,
  withDecay,
  withTiming,
  type SharedValue,
} from 'react-native-reanimated';
import {
  Canvas,
  DashPathEffect,
  Group,
  Line,
  LinearGradient,
  Path,
  RoundedRect,
  Text as SkText,
  matchFont,
  usePathValue,
  type SkPathBuilder,
} from '@shopify/react-native-skia';

import type { Bar } from '@/api/types';
import { PressableScale, Skeleton, Text } from '@/components/ui';
import { fonts, hairline, haptics, radius, spacing, useColors } from '@/theme';

export type ChartMode = 'candle' | 'line';

/** A price-alert level drawn on the chart (dashed line + axis tag). */
export interface ChartAlertLevel {
  id: number;
  price: number;
  direction: string; // "above" | "below" | "cross"
}

interface SkiaChartProps {
  bars: Bar[];
  mode: ChartMode;
  /** Live price — keeps the rightmost candle + dashed price line in sync with the header quote. */
  livePrice?: number | null;
  height?: number;
  loading?: boolean;
  /** Viewport re-fits when this changes (pass `${symbol}:${interval}`); same-key data growth stays pinned to the right edge. */
  resetKey?: string;
  /** Price-alert levels (phone + web — shared backend) rendered as dashed lines. */
  alerts?: ChartAlertLevel[];
  /** When set, releasing the long-press crosshair surfaces a transient
   *  "＋ alert @ price" pill that calls this with the crosshair price. */
  onCreateAlertAtPrice?: (price: number) => void;
}

// ── layout ──
const AXIS_W = 56; // right price axis + tags
const AXIS_H = 20; // bottom time axis
const PANE_TOP = 34; // legend gutter — the price range fits below the crosshair OHLC readout
const PANE_BOTTOM_PAD = 6;
/** Future volume pane slots between the price pane and the time axis. */
const VOLUME_H = 0;
const TAG_H = 16;

// ── viewport ──
const MIN_VISIBLE = 12;
const MAX_VISIBLE = 300;
const DEFAULT_VISIBLE = 90;
const Y_PAD_FRAC = 0.05;

const axisFont = matchFont({ fontFamily: fonts.mono, fontSize: 10 });
const legendFont = matchFont({ fontFamily: fonts.mono, fontSize: 11 });

/** The visible window, recomputed per gesture frame on the UI thread. */
interface Viewport {
  end: number; // rightmost visible index (fractional while panning)
  vis: number; // candles visible
  cW: number; // px per candle slot
  min: number; // fitted price range (padded)
  max: number;
}

interface Series {
  opens: number[];
  highs: number[];
  lows: number[];
  closes: number[];
  axisLbl: string[]; // short, for the time axis
  fullLbl: string[]; // full, for the crosshair legend
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function buildSeries(bars: Bar[]): Series {
  const n = bars.length;
  const opens = new Array<number>(n);
  const highs = new Array<number>(n);
  const lows = new Array<number>(n);
  const closes = new Array<number>(n);
  const axisLbl = new Array<string>(n);
  const fullLbl = new Array<string>(n);
  // Bar spacing decides the label style (clock time for tight intraday candles).
  const spanMs =
    n > 1 ? new Date(bars[1].time).getTime() - new Date(bars[0].time).getTime() : 86_400_000;
  const intraday = spanMs < 20 * 3_600_000;
  const clock = spanMs <= 30 * 60_000;
  const yearly = spanMs > 6 * 86_400_000;
  for (let i = 0; i < n; i++) {
    const b = bars[i];
    opens[i] = b.open;
    highs[i] = b.high;
    lows[i] = b.low;
    closes[i] = b.close;
    const d = new Date(b.time);
    const mon = MONTHS[d.getMonth()];
    const md = `${mon} ${d.getDate()}`;
    const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    axisLbl[i] = clock ? hm : yearly ? `${mon} ${d.getFullYear()}` : md;
    fullLbl[i] = intraday ? `${md}  ${hm}` : `${md}, ${d.getFullYear()}`;
  }
  return { opens, highs, lows, closes, axisLbl, fullLbl };
}

function clamp(v: number, lo: number, hi: number): number {
  'worklet';
  return v < lo ? lo : v > hi ? hi : v;
}

function fmtPrice(p: number): string {
  'worklet';
  return p >= 10_000 ? p.toFixed(0) : p.toFixed(2);
}

function xFor(i: number, v: Viewport, paneW: number): number {
  'worklet';
  return paneW - (v.end - i) * v.cW - v.cW / 2;
}

function yFor(p: number, v: Viewport, top: number, bottom: number): number {
  'worklet';
  return top + ((v.max - p) / (v.max - v.min)) * (bottom - top);
}

/** One pass over the visible candles, appending either bodies or wicks for one direction. */
function addCandles(
  p: SkPathBuilder,
  s: Series,
  v: Viewport,
  live: number | null,
  up: boolean,
  part: 'body' | 'wick',
  paneW: number,
  top: number,
  bottom: number,
) {
  'worklet';
  const { opens, highs, lows, closes } = s;
  const count = closes.length;
  const bodyW = Math.max(1, Math.min(v.cW * 0.72, v.cW - 1));
  const iLo = Math.max(0, Math.floor(v.end - v.vis));
  const iHi = Math.min(count - 1, Math.ceil(v.end));
  for (let i = iLo; i <= iHi; i++) {
    let c = closes[i];
    let hi = highs[i];
    let lo = lows[i];
    if (i === count - 1 && live != null) {
      c = live;
      hi = Math.max(hi, live);
      lo = Math.min(lo, live);
    }
    if (c >= opens[i] !== up) continue;
    const x = xFor(i, v, paneW);
    if (part === 'wick') {
      p.moveTo(x, yFor(hi, v, top, bottom));
      p.lineTo(x, yFor(lo, v, top, bottom));
    } else {
      const yO = yFor(opens[i], v, top, bottom);
      const yC = yFor(c, v, top, bottom);
      p.addRect({
        x: x - bodyW / 2,
        y: Math.min(yO, yC),
        width: bodyW,
        height: Math.max(Math.abs(yO - yC), 1),
      });
    }
  }
}

function usePriceLabel(vp: SharedValue<Viewport>, frac: number): SharedValue<string> {
  return useDerivedValue(() => {
    const v = vp.value;
    return fmtPrice(v.max - frac * (v.max - v.min));
  });
}

function useTimeLabel(vp: SharedValue<Viewport>, labels: string[], gx: number, paneW: number) {
  const text = useDerivedValue(() => {
    const v = vp.value;
    if (labels.length === 0 || v.cW <= 0) return '';
    const i = Math.round(v.end - (paneW - gx) / v.cW);
    return i >= 0 && i < labels.length ? labels[i] : '';
  });
  // ~center over the gridline (mono 10px ≈ 6px/char).
  const x = useDerivedValue(() => gx - text.value.length * 3);
  return { text, x };
}

/**
 * One alert level: dashed horizontal line + right-axis price tag (the
 * live-price-line pattern — hidden by moving offscreen when out of range).
 * A component so each alert gets its own derived values (hook rules).
 */
function AlertOverlay({
  price,
  lineColor,
  tagBg,
  vp,
  paneW,
  priceTop,
  priceBottom,
}: {
  price: number;
  lineColor: string;
  tagBg: string;
  vp: SharedValue<Viewport>;
  paneW: number;
  priceTop: number;
  priceBottom: number;
}) {
  // Same compiler opt-out rationale as SkiaChart: per-frame shared-value work.
  'use no memo';
  const y = useDerivedValue(() => {
    const yy = yFor(price, vp.value, priceTop, priceBottom);
    return yy < priceTop - 2 || yy > priceBottom + 2 ? -1000 : yy;
  });
  const p1 = useDerivedValue(() => ({ x: 0, y: y.value }));
  const p2 = useDerivedValue(() => ({ x: paneW, y: y.value }));
  const tagY = useDerivedValue(() => y.value - TAG_H / 2);
  const textY = useDerivedValue(() => y.value + 3.5);
  return (
    <>
      <Line p1={p1} p2={p2} color={lineColor} strokeWidth={1} opacity={0.55}>
        <DashPathEffect intervals={[6, 4]} />
      </Line>
      <RoundedRect x={paneW + 2} y={tagY} width={AXIS_W - 6} height={TAG_H} r={4} color={tagBg} />
      <SkText x={paneW + 8} y={textY} text={fmtPrice(price)} font={axisFont} color={lineColor} />
    </>
  );
}

export function SkiaChart({
  bars,
  mode,
  livePrice,
  height = 340,
  loading,
  resetKey,
  alerts,
  onCreateAlertAtPrice,
}: SkiaChartProps) {
  // React Compiler opt-out: gesture callbacks (Gesture.Pan().onStart(...)) are
  // not recognized as event handlers, so the documented Reanimated pattern of
  // writing shared values there trips the compiler's immutability rule. All
  // per-frame work here is UI-thread anyway — compiler memoization buys nothing.
  'use no memo';
  const colors = useColors();
  const [width, setWidth] = useState(0);

  // Create-from-chart: releasing the long-press crosshair surfaces a transient
  // "＋ alert" pill at the release point (tap to create). Tagged with resetKey so
  // a symbol/interval switch orphans a stale pill instead of lying about price.
  const [pendingAlert, setPendingAlert] = useState<{
    key: string | undefined;
    price: number;
    y: number;
  } | null>(null);
  const showPendingAlert = (price: number, y: number) =>
    setPendingAlert({ key: resetKey, price, y });
  useEffect(() => {
    if (!pendingAlert) return;
    const t = setTimeout(() => setPendingAlert(null), 4000);
    return () => clearTimeout(t);
  }, [pendingAlert]);

  const s = useMemo(() => buildSeries(bars), [bars]);
  const n = bars.length;

  const paneW = Math.max(0, width - AXIS_W);
  const paneH = Math.max(0, height - AXIS_H - VOLUME_H);
  const priceTop = PANE_TOP;
  const priceBottom = paneH - PANE_BOTTOM_PAD;

  // ── gesture state (UI thread) ──
  const endIdx = useSharedValue(Math.max(0, n - 1));
  const visCount = useSharedValue(Math.min(DEFAULT_VISIBLE, Math.max(n, MIN_VISIBLE)));
  const panStart = useSharedValue(0);
  const pinchStart = useSharedValue({ vis: 0, end: 0 });
  const liveSv = useSharedValue<number | null>(livePrice ?? null);
  const crossX = useSharedValue(0);
  const crossY = useSharedValue(0);
  const crossOn = useSharedValue(0);

  useEffect(() => {
    liveSv.value = livePrice ?? null;
  }, [livePrice, liveSv]);

  // Fit on a new series; when the same series grows (a fresh bar), stay pinned
  // to the right edge if the user was already there.
  const prev = useRef<{ key: string | undefined; n: number }>({ key: undefined, n: 0 });
  useEffect(() => {
    if (n === 0) return;
    if (resetKey !== prev.current.key) {
      endIdx.value = n - 1;
      visCount.value = Math.min(DEFAULT_VISIBLE, Math.max(n, MIN_VISIBLE));
    } else if (n !== prev.current.n && endIdx.value >= prev.current.n - 2) {
      endIdx.value = n - 1;
    }
    prev.current = { key: resetKey, n };
  }, [resetKey, n, endIdx, visCount]);

  // ── viewport: clamp pan/zoom + auto-fit the visible price range ──
  const vp = useDerivedValue<Viewport>(() => {
    const { highs, lows, closes } = s;
    const count = closes.length;
    if (count === 0 || paneW <= 0) return { end: 0, vis: 1, cW: 1, min: 0, max: 1 };
    const minVis = Math.min(MIN_VISIBLE, count);
    const vis = clamp(visCount.value, minVis, Math.min(Math.max(count, minVis), MAX_VISIBLE));
    const end = clamp(endIdx.value, Math.min(vis - 1, count - 1), count - 1);
    const live = liveSv.value;
    let mn = Infinity;
    let mx = -Infinity;
    const iLo = Math.max(0, Math.floor(end - vis));
    const iHi = Math.min(count - 1, Math.ceil(end));
    for (let i = iLo; i <= iHi; i++) {
      let hi = highs[i];
      let lo = lows[i];
      if (i === count - 1 && live != null) {
        hi = Math.max(hi, live);
        lo = Math.min(lo, live);
      }
      if (mode === 'line') {
        const c = i === count - 1 && live != null ? live : closes[i];
        hi = c;
        lo = c;
      }
      if (hi > mx) mx = hi;
      if (lo < mn) mn = lo;
    }
    if (mn === Infinity) {
      mn = 0;
      mx = 1;
    }
    let pad = (mx - mn) * Y_PAD_FRAC;
    if (pad === 0) pad = mx !== 0 ? Math.abs(mx) * 0.01 : 1;
    return { end, vis, cW: paneW / vis, min: mn - pad, max: mx + pad };
  });

  // ── series geometry (rebuilt per frame in worklets — no React involved) ──
  const upBodies = usePathValue((p) => {
    'worklet';
    if (mode !== 'candle') return;
    addCandles(p, s, vp.value, liveSv.value, true, 'body', paneW, priceTop, priceBottom);
  });
  const dnBodies = usePathValue((p) => {
    'worklet';
    if (mode !== 'candle') return;
    addCandles(p, s, vp.value, liveSv.value, false, 'body', paneW, priceTop, priceBottom);
  });
  const upWicks = usePathValue((p) => {
    'worklet';
    if (mode !== 'candle') return;
    addCandles(p, s, vp.value, liveSv.value, true, 'wick', paneW, priceTop, priceBottom);
  });
  const dnWicks = usePathValue((p) => {
    'worklet';
    if (mode !== 'candle') return;
    addCandles(p, s, vp.value, liveSv.value, false, 'wick', paneW, priceTop, priceBottom);
  });
  const linePath = usePathValue((p) => {
    'worklet';
    if (mode !== 'line') return;
    const v = vp.value;
    const { closes } = s;
    const count = closes.length;
    const live = liveSv.value;
    const iLo = Math.max(0, Math.floor(v.end - v.vis));
    const iHi = Math.min(count - 1, Math.ceil(v.end));
    for (let i = iLo; i <= iHi; i++) {
      const c = i === count - 1 && live != null ? live : closes[i];
      const x = xFor(i, v, paneW);
      const y = yFor(c, v, priceTop, priceBottom);
      if (i === iLo) p.moveTo(x, y);
      else p.lineTo(x, y);
    }
  });
  const areaPath = usePathValue((p) => {
    'worklet';
    if (mode !== 'line') return;
    const v = vp.value;
    const { closes } = s;
    const count = closes.length;
    const live = liveSv.value;
    const iLo = Math.max(0, Math.floor(v.end - v.vis));
    const iHi = Math.min(count - 1, Math.ceil(v.end));
    if (iHi < iLo) return;
    for (let i = iLo; i <= iHi; i++) {
      const c = i === count - 1 && live != null ? live : closes[i];
      const x = xFor(i, v, paneW);
      const y = yFor(c, v, priceTop, priceBottom);
      if (i === iLo) p.moveTo(x, y);
      else p.lineTo(x, y);
    }
    p.lineTo(xFor(iHi, v, paneW), paneH);
    p.lineTo(xFor(iLo, v, paneW), paneH);
    p.close();
  });

  // ── axes ──
  const gridYs = [0, 1 / 3, 2 / 3, 1].map((t) => priceTop + t * (priceBottom - priceTop));
  const pl0 = usePriceLabel(vp, 0);
  const pl1 = usePriceLabel(vp, 1 / 3);
  const pl2 = usePriceLabel(vp, 2 / 3);
  const pl3 = usePriceLabel(vp, 1);
  const gxs = [0.18, 0.5, 0.82].map((f) => Math.round(paneW * f));
  const tl0 = useTimeLabel(vp, s.axisLbl, gxs[0], paneW);
  const tl1 = useTimeLabel(vp, s.axisLbl, gxs[1], paneW);
  const tl2 = useTimeLabel(vp, s.axisLbl, gxs[2], paneW);

  // ── live price line + right-edge tag (hidden by moving offscreen — no churn) ──
  const liveY = useDerivedValue(() => {
    const l = liveSv.value;
    if (l == null || n === 0) return -1000;
    const y = yFor(l, vp.value, priceTop, priceBottom);
    return y < priceTop - 2 || y > priceBottom + 2 ? -1000 : y;
  });
  const liveP1 = useDerivedValue(() => ({ x: 0, y: liveY.value }));
  const liveP2 = useDerivedValue(() => ({ x: paneW, y: liveY.value }));
  const liveTagY = useDerivedValue(() => liveY.value - TAG_H / 2);
  const liveTextY = useDerivedValue(() => liveY.value + 3.5);
  const liveText = useDerivedValue(() => (liveSv.value == null ? '' : fmtPrice(liveSv.value)));

  // ── crosshair (long-press scrub) + OHLC legend ──
  const crossOpacity = useDerivedValue(() => withTiming(crossOn.value, { duration: 90 }));
  const crossIdx = useDerivedValue(() => {
    if (n === 0) return 0;
    const v = vp.value;
    return Math.round(clamp(v.end - (paneW - crossX.value) / v.cW, 0, n - 1));
  });
  const crossVx = useDerivedValue(() => xFor(crossIdx.value, vp.value, paneW));
  const crossV1 = useDerivedValue(() => ({ x: crossVx.value, y: 0 }));
  const crossV2 = useDerivedValue(() => ({ x: crossVx.value, y: paneH }));
  const crossHy = useDerivedValue(() => clamp(crossY.value, priceTop, priceBottom));
  const crossH1 = useDerivedValue(() => ({ x: 0, y: crossHy.value }));
  const crossH2 = useDerivedValue(() => ({ x: paneW, y: crossHy.value }));
  const crossTagY = useDerivedValue(() => crossHy.value - TAG_H / 2);
  const crossTagTextY = useDerivedValue(() => crossHy.value + 3.5);
  const crossPriceText = useDerivedValue(() => {
    const v = vp.value;
    const t = (crossHy.value - priceTop) / Math.max(priceBottom - priceTop, 1);
    return fmtPrice(v.max - t * (v.max - v.min));
  });
  const legendDate = useDerivedValue(() =>
    crossOn.value ? (s.fullLbl[crossIdx.value] ?? '') : '',
  );
  const legendOhlc = useDerivedValue(() => {
    if (!crossOn.value || n === 0) return '';
    const { opens, highs, lows, closes } = s;
    const i = crossIdx.value;
    const live = liveSv.value;
    let c = closes[i];
    let hi = highs[i];
    let lo = lows[i];
    if (i === n - 1 && live != null) {
      c = live;
      hi = Math.max(hi, live);
      lo = Math.min(lo, live);
    }
    if (mode === 'line') return fmtPrice(c);
    return `O ${fmtPrice(opens[i])}  H ${fmtPrice(hi)}  L ${fmtPrice(lo)}  C ${fmtPrice(c)}`;
  });

  // ── gestures ──
  // Horizontal-only activation so vertical drags still scroll the page.
  const pan = Gesture.Pan()
    .maxPointers(1)
    .activeOffsetX([-12, 12])
    .failOffsetY([-16, 16])
    .onBegin(() => {
      cancelAnimation(endIdx);
    })
    .onStart(() => {
      panStart.value = endIdx.value;
    })
    .onUpdate((e) => {
      if (n === 0) return;
      const v = vp.value;
      endIdx.value = clamp(
        panStart.value - e.translationX / v.cW,
        Math.min(v.vis - 1, n - 1),
        n - 1,
      );
    })
    .onEnd((e) => {
      if (n === 0) return;
      const v = vp.value;
      endIdx.value = withDecay({
        velocity: -e.velocityX / v.cW,
        clamp: [Math.min(v.vis - 1, n - 1), n - 1],
      });
    });

  const pinch = Gesture.Pinch()
    .onStart(() => {
      cancelAnimation(endIdx);
      const v = vp.value;
      pinchStart.value = { vis: v.vis, end: v.end };
    })
    .onUpdate((e) => {
      if (n === 0 || paneW <= 0) return;
      const st = pinchStart.value;
      const minVis = Math.min(MIN_VISIBLE, n);
      const maxVis = Math.min(Math.max(n, minVis), MAX_VISIBLE);
      const newVis = clamp(st.vis / e.scale, minVis, maxVis);
      // Keep the candle under the focal point stationary.
      const offR = (paneW - e.focalX) / paneW;
      const anchor = st.end - offR * st.vis;
      visCount.value = newVis;
      endIdx.value = clamp(anchor + offR * newVis, Math.min(newVis - 1, n - 1), n - 1);
    });

  const canCreateAlert = Boolean(onCreateAlertAtPrice) && n > 0;
  const crosshair = Gesture.Pan()
    .maxPointers(1)
    .activateAfterLongPress(220)
    .onStart((e) => {
      crossOn.value = 1;
      crossX.value = clamp(e.x, 0, paneW);
      crossY.value = e.y;
      runOnJS(haptics.selection)();
    })
    .onUpdate((e) => {
      crossX.value = clamp(e.x, 0, paneW);
      crossY.value = e.y;
    })
    .onEnd(() => {
      // onEnd only fires when the long-press actually activated (failed gestures
      // — plain scrolls — skip straight to onFinalize), so no activation check.
      if (canCreateAlert) {
        const v = vp.value;
        const yy = clamp(crossY.value, priceTop, priceBottom);
        const t = (yy - priceTop) / Math.max(priceBottom - priceTop, 1);
        runOnJS(showPendingAlert)(v.max - t * (v.max - v.min), yy);
      }
    })
    .onFinalize(() => {
      crossOn.value = 0;
    });

  const gesture = Gesture.Simultaneous(pinch, Gesture.Exclusive(crosshair, pan));

  return (
    <View style={{ height }} onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
      {n > 0 && width > 0 ? (
        <>
          <GestureDetector gesture={gesture}>
            <Canvas style={{ flex: 1 }}>
            {/* grid */}
            {gridYs.map((y) => (
              <Line
                key={`h${y}`}
                p1={{ x: 0, y }}
                p2={{ x: paneW, y }}
                color={colors.textFaint}
                opacity={0.28}
                strokeWidth={1}
              />
            ))}
            {gxs.map((x) => (
              <Line
                key={`v${x}`}
                p1={{ x, y: 0 }}
                p2={{ x, y: paneH }}
                color={colors.textFaint}
                opacity={0.16}
                strokeWidth={1}
              />
            ))}

            {/* series, clipped to the pane (overlay series slot in here) */}
            <Group clip={{ x: 0, y: 0, width: paneW, height: paneH }}>
              {mode === 'candle' ? (
                <>
                  <Path path={upWicks} style="stroke" strokeWidth={1} color={colors.positive} />
                  <Path path={dnWicks} style="stroke" strokeWidth={1} color={colors.negative} />
                  <Path path={upBodies} color={colors.positive} />
                  <Path path={dnBodies} color={colors.negative} />
                </>
              ) : (
                <>
                  <Path path={areaPath}>
                    <LinearGradient
                      start={{ x: 0, y: priceTop }}
                      end={{ x: 0, y: paneH }}
                      colors={[colors.accentWash, 'transparent']}
                    />
                  </Path>
                  <Path
                    path={linePath}
                    style="stroke"
                    strokeWidth={2}
                    strokeJoin="round"
                    strokeCap="round"
                    color={colors.accent}
                  />
                </>
              )}
              <Line p1={liveP1} p2={liveP2} color={colors.accent} strokeWidth={1} opacity={0.85}>
                <DashPathEffect intervals={[4, 4]} />
              </Line>
            </Group>

            {/* alert levels (above=positive, below=negative, cross=neutral) */}
            {(alerts ?? []).map((a) => (
              <AlertOverlay
                key={a.id}
                price={a.price}
                lineColor={
                  a.direction === 'above'
                    ? colors.positive
                    : a.direction === 'below'
                      ? colors.negative
                      : colors.textMuted
                }
                tagBg={colors.surfaceRaised}
                vp={vp}
                paneW={paneW}
                priceTop={priceTop}
                priceBottom={priceBottom}
              />
            ))}

            {/* right price axis */}
            <SkText x={paneW + 8} y={gridYs[0] + 3.5} text={pl0} font={axisFont} color={colors.textMuted} />
            <SkText x={paneW + 8} y={gridYs[1] + 3.5} text={pl1} font={axisFont} color={colors.textMuted} />
            <SkText x={paneW + 8} y={gridYs[2] + 3.5} text={pl2} font={axisFont} color={colors.textMuted} />
            <SkText x={paneW + 8} y={gridYs[3] + 3.5} text={pl3} font={axisFont} color={colors.textMuted} />
            <RoundedRect x={paneW + 2} y={liveTagY} width={AXIS_W - 6} height={TAG_H} r={4} color={colors.accent} />
            <SkText x={paneW + 8} y={liveTextY} text={liveText} font={axisFont} color={colors.onAccent} />

            {/* bottom time axis */}
            <SkText x={tl0.x} y={height - 6} text={tl0.text} font={axisFont} color={colors.textMuted} />
            <SkText x={tl1.x} y={height - 6} text={tl1.text} font={axisFont} color={colors.textMuted} />
            <SkText x={tl2.x} y={height - 6} text={tl2.text} font={axisFont} color={colors.textMuted} />

            {/* crosshair + OHLC legend */}
            <Group opacity={crossOpacity}>
              <Line p1={crossV1} p2={crossV2} color={colors.textFaint} strokeWidth={1} />
              <Line p1={crossH1} p2={crossH2} color={colors.textFaint} strokeWidth={1} />
              <RoundedRect x={paneW + 2} y={crossTagY} width={AXIS_W - 6} height={TAG_H} r={4} color={colors.surfaceRaised} />
              <SkText x={paneW + 8} y={crossTagTextY} text={crossPriceText} font={axisFont} color={colors.text} />
              <SkText x={10} y={14} text={legendDate} font={legendFont} color={colors.textMuted} />
              <SkText x={10} y={28} text={legendOhlc} font={legendFont} color={colors.text} />
            </Group>
            </Canvas>
          </GestureDetector>

          {/* transient create-alert affordance (crosshair release) */}
          {pendingAlert && pendingAlert.key === resetKey && onCreateAlertAtPrice ? (
            <PressableScale
              onPress={() => {
                setPendingAlert(null);
                onCreateAlertAtPrice(pendingAlert.price);
              }}
              style={{
                position: 'absolute',
                left: spacing.md,
                top: Math.min(Math.max(pendingAlert.y - 44, PANE_TOP + 2), priceBottom - 36),
              }}
            >
              <View
                style={{
                  paddingHorizontal: spacing.md,
                  paddingVertical: spacing.sm,
                  borderRadius: radius.pill,
                  backgroundColor: colors.surfaceRaised,
                  borderWidth: hairline,
                  borderColor: colors.accent,
                }}
              >
                <Text
                  variant="caption"
                  style={{ fontFamily: fonts.mono, color: colors.accent }}
                >
                  {`＋ alert @ ${fmtPrice(pendingAlert.price)}`}
                </Text>
              </View>
            </PressableScale>
          ) : null}
        </>
      ) : n === 0 && loading ? (
        <Skeleton height={height} />
      ) : n === 0 ? (
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
          <Text variant="caption" color="textFaint">
            No chart data
          </Text>
        </View>
      ) : null}
    </View>
  );
}
