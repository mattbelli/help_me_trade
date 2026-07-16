/**
 * Layout primitives — Screen, Card, Row, Stack, Divider, Spacer.
 * Full-bleed by default (the brief: "widgets shouldn't sit in the page — they
 * should BE the page"). Cards are used sparingly, for genuinely raised content.
 */
import { type ReactNode } from 'react';
import { View, type ViewProps, type ViewStyle } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { elevation, hairline, radius, spacing, useColors } from '@/theme';

/** A full-bleed surface that respects the status bar / home indicator. */
export function Screen({
  children,
  style,
  edges = { top: true, bottom: false },
  padded = false,
}: {
  children: ReactNode;
  style?: ViewStyle;
  edges?: { top?: boolean; bottom?: boolean };
  padded?: boolean;
}) {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  return (
    <View
      style={[
        { flex: 1, backgroundColor: colors.bg },
        edges.top && { paddingTop: insets.top },
        edges.bottom && { paddingBottom: insets.bottom },
        padded && { paddingHorizontal: spacing.xl },
        style,
      ]}
    >
      {children}
    </View>
  );
}

export function Card({
  children,
  style,
  raised,
  ...rest
}: ViewProps & { children: ReactNode; raised?: boolean }) {
  const colors = useColors();
  return (
    <View
      style={[
        {
          backgroundColor: colors.surface,
          borderRadius: radius.xl,
          borderWidth: hairline,
          borderColor: colors.border,
          padding: spacing.xl,
        },
        raised && elevation.card,
        style,
      ]}
      {...rest}
    >
      {children}
    </View>
  );
}

export function Row({
  children,
  style,
  gap = spacing.md,
  align = 'center',
  justify,
  ...rest
}: ViewProps & {
  children: ReactNode;
  gap?: number;
  align?: ViewStyle['alignItems'];
  justify?: ViewStyle['justifyContent'];
}) {
  return (
    <View
      style={[{ flexDirection: 'row', alignItems: align, justifyContent: justify, gap }, style]}
      {...rest}
    >
      {children}
    </View>
  );
}

export function Stack({
  children,
  style,
  gap = spacing.md,
  ...rest
}: ViewProps & { children: ReactNode; gap?: number }) {
  return (
    <View style={[{ gap }, style]} {...rest}>
      {children}
    </View>
  );
}

export function Divider({ style }: { style?: ViewStyle }) {
  const colors = useColors();
  return <View style={[{ height: hairline, backgroundColor: colors.border }, style]} />;
}

export function Spacer({ size = spacing.lg }: { size?: number }) {
  return <View style={{ height: size }} />;
}
