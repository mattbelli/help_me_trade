import { View } from 'react-native';

import { CockpitPager } from '@/components/pager/cockpit-pager';
import { Text } from '@/components/ui';
import { useAuth } from '@/auth/auth-context';
import { OnboardingScreen } from '@/features/onboarding/onboarding-screen';
import { AlertsProvider } from '@/state/alerts';
import { colors } from '@/theme';

/**
 * Entry gate: splash while the Keychain token loads → onboarding if absent →
 * the live cockpit (wrapped in the alert store the WS feeds) once authed.
 */
export default function Index() {
  const { token, isLoading } = useAuth();

  if (isLoading) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg, alignItems: 'center', justifyContent: 'center' }}>
        <Text variant="label" upper color="accent">
          help me trade
        </Text>
      </View>
    );
  }

  if (!token) {
    return <OnboardingScreen />;
  }

  return (
    <AlertsProvider>
      <CockpitPager />
    </AlertsProvider>
  );
}
