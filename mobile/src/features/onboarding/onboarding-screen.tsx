/**
 * Onboarding — one job: take the API token, validate it against the server, and
 * stash it in the Keychain. Deliberately calm and premium; this is the first
 * thing the user sees. (Brief §12 Q6: manual paste once is the v1 path.)
 */
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, TextInput, View } from 'react-native';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';

import { Button, Screen, Stack, Text } from '@/components/ui';
import { useAuth } from '@/auth/auth-context';
import { API_BASE_URL } from '@/config';
import { colors, haptics, hairline, radius, spacing } from '@/theme';

export function OnboardingScreen() {
  const { signIn } = useAuth();
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const host = API_BASE_URL.replace(/^https?:\/\//, '');

  async function connect() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await signIn(value);
      haptics.success();
    } catch (err) {
      haptics.error();
      setError(err instanceof Error ? err.message : 'Could not connect');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen padded>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={{ flex: 1, justifyContent: 'center', gap: spacing['3xl'] }}>
          <Animated.View entering={FadeIn.duration(500)}>
            <Stack gap={spacing.sm}>
              <Text variant="label" upper color="accent">
                help me trade
              </Text>
              <Text variant="title">Your agent,{'\n'}in your pocket.</Text>
              <Text variant="body" color="textMuted">
                Watch what the agent is doing and approve its trades with Face ID — wherever you are.
              </Text>
            </Stack>
          </Animated.View>

          <Animated.View entering={FadeInDown.delay(120).duration(500)}>
            <Stack gap={spacing.md}>
              <Text variant="label" upper color="textFaint">
                API token
              </Text>
              <TextInput
                value={value}
                onChangeText={(t) => {
                  setValue(t);
                  if (error) setError(null);
                }}
                placeholder="Paste your token"
                placeholderTextColor={colors.textFaint}
                autoCapitalize="none"
                autoCorrect={false}
                secureTextEntry
                selectionColor={colors.accent}
                onSubmitEditing={connect}
                returnKeyType="go"
                style={{
                  color: colors.text,
                  fontSize: 16,
                  paddingHorizontal: spacing.lg,
                  paddingVertical: spacing.lg,
                  backgroundColor: colors.surface,
                  borderRadius: radius.lg,
                  borderWidth: hairline,
                  borderColor: error ? colors.negative : colors.border,
                }}
              />
              {error ? (
                <Text variant="caption" color="negative">
                  {error}
                </Text>
              ) : (
                <Text variant="caption" color="textFaint">
                  Connecting to {host}
                </Text>
              )}
              <Button
                label={busy ? 'Connecting…' : 'Connect'}
                onPress={connect}
                disabled={busy || value.trim().length === 0}
                style={{ marginTop: spacing.sm }}
              />
            </Stack>
          </Animated.View>
        </View>
      </KeyboardAvoidingView>
    </Screen>
  );
}
