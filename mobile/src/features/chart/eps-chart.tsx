/**
 * EpsChart — quarterly EPS "lollipop" chart: per quarter a hollow ESTIMATE dot
 * and a filled ACTUAL dot on a shared y-scale, joined by a thin stem. Beat
 * (actual ≥ estimate) renders positive, miss renders negative; the small
 * surprise% sits above each quarter. Static (no gestures) — react-native-svg,
 * same as the sparklines. Theme-reactive via useColors.
 */
import { Fragment, useMemo, useState } from 'react';
import { View } from 'react-native';
import Svg, { Circle, Line as SvgLine, Text as SvgText } from 'react-native-svg';

import type { EarningsEvent } from '@/api/types';
import { Text } from '@/components/ui';
import { fonts, useColors } from '@/theme';

const TOP = 22; // surprise% row
const BOTTOM = 20; // quarter labels
const EST_R = 4;
const ACT_R = 4.5;
const MIN_OFF = 13; // min px the actual dot sits off the estimate — a legible stem even for a ~1% surprise
const PX_PER_PCT = 1.6; // additional px per 1% surprise beyond the floor (stem length ≈ surprise magnitude)

export function EpsChart({
  events,
  height = 170,
}: {
  /** Most-recent-first (the /api/fundamentals/earnings-history order) — reversed here. */
  events: EarningsEvent[];
  height?: number;
}) {
  const colors = useColors();
  const [width, setWidth] = useState(0);

  // Chronological L→R, oldest → newest.
  const quarters = useMemo(() => [...events].reverse(), [events]);

  let mn = Infinity;
  let mx = -Infinity;
  for (const q of quarters) {
    for (const v of [q.eps_actual, q.eps_estimate]) {
      if (v == null) continue;
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
  }
  const hasData = mn !== Infinity;

  if (!hasData) {
    return (
      <Text variant="body" color="textMuted">
        No EPS data
      </Text>
    );
  }

  if (mn === mx) {
    // Flat series — synthesize a range so dots don't sit on the edges.
    const bump = Math.abs(mn) * 0.1 || 0.5;
    mn -= bump;
    mx += bump;
  }
  const pad = (mx - mn) * 0.12;
  const lo = mn - pad;
  const hi = mx + pad;
  const plotH = height - TOP - BOTTOM;
  const yFor = (v: number) => TOP + ((hi - v) / (hi - lo)) * plotH;

  return (
    <View onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
      {width > 0 ? (
        <Svg width={width} height={height}>
          {lo < 0 && hi > 0 ? (
            <SvgLine
              x1={0}
              y1={yFor(0)}
              x2={width}
              y2={yFor(0)}
              stroke={colors.textFaint}
              strokeWidth={1}
              strokeDasharray="3 4"
              opacity={0.4}
            />
          ) : null}
          {quarters.map((q, i) => {
            const x = ((i + 0.5) / quarters.length) * width;
            const est = q.eps_estimate;
            const act = q.eps_actual;
            const beat = act != null && est != null ? act >= est : (q.surprise_pct ?? 0) >= 0;
            const tone = act == null ? colors.textMuted : beat ? colors.positive : colors.negative;
            const estY = est != null ? yFor(est) : null;
            // Absolute EPS gaps are tiny next to the cross-quarter range, so a literal
            // shared scale collapses the two dots. Offset the actual off the estimate by
            // a surprise%-scaled amount with a legible floor + a cap that stays in-plot.
            let actY = act != null ? yFor(act) : null;
            if (estY != null && est != null && act != null) {
              const s = q.surprise_pct ?? ((act - est) / (Math.abs(est) || 1)) * 100;
              const mag = Math.min(plotH * 0.42, MIN_OFF + Math.abs(s) * PX_PER_PCT);
              const raw = estY - (beat ? 1 : -1) * mag;
              actY = Math.max(TOP + ACT_R, Math.min(TOP + plotH - ACT_R, raw));
            }
            return (
              <Fragment key={q.period}>
                {estY != null && actY != null ? (
                  <SvgLine
                    x1={x}
                    y1={estY}
                    x2={x}
                    y2={actY}
                    stroke={tone}
                    strokeWidth={1.5}
                    opacity={0.55}
                  />
                ) : null}
                {estY != null ? (
                  // Hollow estimate dot — bg fill masks the stem passing through it.
                  <Circle
                    cx={x}
                    cy={estY}
                    r={EST_R}
                    fill={colors.bg}
                    stroke={colors.textFaint}
                    strokeWidth={1.5}
                  />
                ) : null}
                {actY != null ? <Circle cx={x} cy={actY} r={ACT_R} fill={tone} /> : null}
                {q.surprise_pct != null ? (
                  <SvgText
                    x={x}
                    y={12}
                    textAnchor="middle"
                    fontFamily={fonts.mono}
                    fontSize={9}
                    fill={tone}
                  >
                    {`${q.surprise_pct >= 0 ? '+' : ''}${q.surprise_pct.toFixed(1)}%`}
                  </SvgText>
                ) : null}
                <SvgText
                  x={x}
                  y={height - 6}
                  textAnchor="middle"
                  fontFamily={fonts.mono}
                  fontSize={10}
                  fill={colors.textMuted}
                >
                  {q.label}
                </SvgText>
              </Fragment>
            );
          })}
        </Svg>
      ) : (
        <View style={{ height }} />
      )}
    </View>
  );
}
