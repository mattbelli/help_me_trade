/**
 * TabBar — the bottom navigation. Taps jump straight to a surface; the active
 * tab is teal. Inbox shows an unread/pending dot. Sits above the home indicator.
 * Pairs with the pager (swipe still works); this just adds direct access.
 */
import { View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { PressableScale, Text } from '@/components/ui';
import { Icon, type IconName } from '@/components/ui/icon';
import { colors, hairline, spacing } from '@/theme';

export interface TabDef {
  key: string;
  label: string;
  icon: IconName;
}

export function TabBar({
  tabs,
  index,
  onSelect,
  badges,
}: {
  tabs: TabDef[];
  index: number;
  onSelect: (i: number) => void;
  /** key → show a dot (e.g. unread inbox / pending approvals). */
  badges?: Record<string, boolean>;
}) {
  const insets = useSafeAreaInsets();
  return (
    <View
      style={{
        flexDirection: 'row',
        paddingTop: spacing.sm,
        paddingBottom: Math.max(insets.bottom, spacing.sm),
        backgroundColor: colors.bgInset,
        borderTopWidth: hairline,
        borderTopColor: colors.border,
      }}
    >
      {tabs.map((t, i) => {
        const active = i === index;
        const tint = active ? colors.accent : colors.textFaint;
        return (
          <PressableScale
            key={t.key}
            onPress={() => onSelect(i)}
            scaleTo={0.9}
            style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: 3, paddingVertical: spacing.xs }}
          >
            <View>
              <Icon name={t.icon} size={24} color={tint} strokeWidth={active ? 2.1 : 1.8} />
              {badges?.[t.key] ? (
                <View
                  style={{
                    position: 'absolute',
                    top: -2,
                    right: -5,
                    width: 8,
                    height: 8,
                    borderRadius: 4,
                    backgroundColor: colors.accent,
                    borderWidth: 1.5,
                    borderColor: colors.bgInset,
                  }}
                />
              ) : null}
            </View>
            <Text variant="label" style={{ color: tint, fontSize: 10, letterSpacing: 0.3 }}>
              {t.label}
            </Text>
          </PressableScale>
        );
      })}
    </View>
  );
}
