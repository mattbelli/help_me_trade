/**
 * MiniDayChart — Robinhood-style intraday line. The x-axis is the full trading
 * session (9:30–16:00 ET); the line only extends as far as the CURRENT time
 * (10am → short, 3:30pm → near the edge), with a pulsing dot at the live end.
 *
 * NOTE: the line *extent* (time-of-day) is real, from the ET clock. The line
 * *shape* currently uses the daily sparkline as a stand-in until serv returns a
 * true intraday series — at which point only the data prop changes.
 */
import { useEffect, useMemo } from 'react';
import { View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import Animated, { useAnimatedStyle, useSharedValue, withRepeat, withTiming } from 'react-native-reanimated';

import { pnlColor } from '@/theme';

/**
 * Fraction of the regular session elapsed [0..1]; 1 when closed/weekend.
 * Uses Intl.formatToParts (Hermes-safe) — do NOT `new Date(localeString)`, Hermes
 * fails to parse that and yields NaN, which crashes the SVG path.
 */
function sessionFraction(): number {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).formatToParts(new Date());
    const get = (t: string) => parts.find((p) => p.type === t)?.value;
    const wd = get('weekday');
    let hour = Number(get('hour'));
    const minute = Number(get('minute'));
    if (hour === 24) hour = 0;
    if (!Number.isFinite(hour) || !Number.isFinite(minute)) return 1;
    if (wd === 'Sat' || wd === 'Sun') return 1;
    const mins = hour * 60 + minute;
    const open = 9 * 60 + 30;
    const close = 16 * 60;
    if (mins <= open) return 0.03;
    if (mins >= close) return 1;
    return (mins - open) / (close - open);
  } catch {
    return 1;
  }
}

// Full regular session = 390 min / 5-min bars = 78 points across the chart width.
const BARS_PER_DAY = 78;

export function MiniDayChart({
  data,
  intraday = false,
  change,
  width,
  height = 44,
}: {
  data: number[];
  /** When true, `data` is real intraday 5-min closes → position by index (i/78). */
  intraday?: boolean;
  change: number;
  width: number;
  height?: number;
}) {
  const pulse = useSharedValue(1);
  useEffect(() => {
    pulse.value = withRepeat(withTiming(0.2, { duration: 850 }), -1, true);
  }, [pulse]);

  const stroke = pnlColor(change);
  const { path, dotX, dotY } = useMemo(() => {
    if (!data || data.length < 2) {
      const lineW = Math.max(2, width * sessionFraction());
      const y = height / 2;
      return { path: `M0,${y} L${lineW},${y}`, dotX: lineW, dotY: y };
    }
    const min = Math.min(...data);
    const max = Math.max(...data);
    const span = max - min || 1;
    const yOf = (v: number) => height - 3 - ((v - min) / span) * (height - 6);

    // x positions: intraday → i/78 of width (extent encodes time of day, ending
    // at the live bar); sparkline fallback → spread across the elapsed session.
    let xs: number[];
    if (intraday) {
      xs = data.map((_, i) => Math.min(width, (i / BARS_PER_DAY) * width));
    } else {
      const lineW = Math.max(2, width * sessionFraction());
      const dx = lineW / (data.length - 1);
      xs = data.map((_, i) => i * dx);
    }

    const pts = data.map((v, i) => [xs[i], yOf(v)] as const);
    const d = pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const last = pts[pts.length - 1];
    return { path: d, dotX: last[0], dotY: last[1] };
  }, [data, intraday, width, height, change]);

  const dotStyle = useAnimatedStyle(() => ({ opacity: pulse.value }));

  return (
    <View style={{ width, height }}>
      <Svg width={width} height={height}>
        <Path d={path} stroke={stroke} strokeWidth={1.6} fill="none" strokeLinejoin="round" strokeLinecap="round" />
      </Svg>
      {/* pulsing live dot at the leading edge */}
      <Animated.View
        style={[
          { position: 'absolute', left: dotX - 3.5, top: dotY - 3.5, width: 7, height: 7, borderRadius: 4, backgroundColor: stroke },
          dotStyle,
        ]}
      />
    </View>
  );
}
