/**
 * Financial figures — Money, Pct, Delta. Always mono + tabular-nums so digits
 * don't jitter when values tick. P&L figures are color-coded green/red, with an
 * optional leading sign, which is the single most important readout in the app.
 */
import { type TextStyle } from 'react-native';

import { pnlColor, type as typeScale, useColors } from '@/theme';

import { Text, type TextProps } from './text';

type FigureSize = 'sm' | 'md' | 'lg';
const variantFor: Record<FigureSize, keyof typeof typeScale> = {
  sm: 'figureSm',
  md: 'figure',
  lg: 'figureLg',
};

function formatUsd(value: number, opts?: { compact?: boolean }): string {
  const abs = Math.abs(value);
  if (opts?.compact && abs >= 1000) {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      notation: 'compact',
      maximumFractionDigits: 1,
    }).format(value);
  }
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function Money({
  value,
  size = 'md',
  compact,
  color = 'text',
  style,
  ...rest
}: Omit<TextProps, 'children' | 'variant'> & {
  value: number;
  size?: FigureSize;
  compact?: boolean;
}) {
  return (
    <Text variant={variantFor[size]} color={color} style={style} {...rest}>
      {formatUsd(value, { compact })}
    </Text>
  );
}

/** A percentage. `signed` adds an explicit +/−; `pnl` colors it green/red. */
export function Pct({
  value,
  size = 'md',
  signed,
  pnl,
  style,
  ...rest
}: Omit<TextProps, 'children' | 'variant' | 'color'> & {
  value: number;
  size?: FigureSize;
  signed?: boolean;
  pnl?: boolean;
}) {
  const colors = useColors();
  const sign = signed && value > 0 ? '+' : '';
  const color = pnl ? pnlColor(value) : colors.text;
  return (
    <Text variant={variantFor[size]} style={[{ color } as TextStyle, style]} {...rest}>
      {`${sign}${value.toFixed(2)}%`}
    </Text>
  );
}

/** A signed, color-coded P&L dollar delta (e.g. "+$1,240.50" in green). */
export function Delta({
  value,
  size = 'md',
  compact,
  style,
  ...rest
}: Omit<TextProps, 'children' | 'variant' | 'color'> & {
  value: number;
  size?: FigureSize;
  compact?: boolean;
}) {
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return (
    <Text
      variant={variantFor[size]}
      style={[{ color: pnlColor(value) } as TextStyle, style]}
      {...rest}
    >
      {`${sign}${formatUsd(Math.abs(value), { compact })}`}
    </Text>
  );
}

export { formatUsd };
