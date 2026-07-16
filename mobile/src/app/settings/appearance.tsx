import { ScrollView, View } from 'react-native';
import { useRouter } from 'expo-router';

import { Icon } from '@/components/ui/icon';
import {
  BackRow,
  Eyebrow,
  Screen,
  SettingsRow,
  Stack,
  Text,
} from '@/components/ui';
import { spacing, useColors, useThemeMode } from '@/theme';

export default function AppearanceScreen() {
  const router = useRouter();
  const { mode, setMode, options } = useThemeMode();

  const autoOptions = options.filter((opt) => opt.kind === 'system');
  const builtinOptions = options.filter((opt) => opt.kind === 'builtin');
  const customOptions = options.filter((opt) => opt.kind === 'custom');

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{
          paddingTop: spacing.sm,
          paddingHorizontal: spacing.xl,
          paddingBottom: 110,
        }}
      >
        <Stack gap={spacing.lg}>
          <BackRow onBack={() => router.back()} />
          <Text variant="title">Appearance</Text>

          <Stack gap={spacing.xs}>
            {autoOptions.map((opt) => (
              <ThemeOptionRow
                key={opt.mode}
                label={opt.label}
                swatch={opt.swatch}
                selected={mode === opt.mode}
                onPress={() => setMode(opt.mode)}
              />
            ))}
          </Stack>

          <Stack gap={spacing.sm}>
            <Eyebrow>Built-in</Eyebrow>
            <Stack gap={spacing.xs}>
              {builtinOptions.map((opt) => (
                <ThemeOptionRow
                  key={opt.mode}
                  label={opt.label}
                  swatch={opt.swatch}
                  selected={mode === opt.mode}
                  onPress={() => setMode(opt.mode)}
                />
              ))}
            </Stack>
          </Stack>

          {customOptions.length > 0 ? (
            <Stack gap={spacing.sm}>
              <Eyebrow>My themes</Eyebrow>
              <Stack gap={spacing.xs}>
                {customOptions.map((opt) => (
                  <ThemeOptionRow
                    key={opt.mode}
                    label={opt.label}
                    swatch={opt.swatch}
                    selected={mode === opt.mode}
                    onPress={() => setMode(opt.mode)}
                  />
                ))}
              </Stack>
            </Stack>
          ) : null}
        </Stack>
      </ScrollView>
    </Screen>
  );
}

function ThemeOptionRow({
  label,
  swatch,
  selected,
  onPress,
}: {
  label: string;
  swatch: string;
  selected: boolean;
  onPress: () => void;
}) {
  const colors = useColors();
  return (
    <SettingsRow
      label={label}
      swatch={swatch}
      onPress={onPress}
      right={
        selected ? <Icon name="check" size={20} color={colors.accent} /> : <View style={{ width: 20 }} />
      }
    />
  );
}
