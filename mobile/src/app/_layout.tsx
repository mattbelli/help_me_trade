import 'react-native-reanimated';
import { useEffect } from 'react';
import { AppState } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { QueryClientProvider, focusManager } from '@tanstack/react-query';
import { Stack } from 'expo-router';

import { queryClient } from '@/api/query-client';
import { AuthProvider } from '@/auth/auth-context';
import { ThemeProvider, useColors } from '@/theme';

/**
 * Root layout — the provider stack for the whole app:
 * gesture root → safe-area → react-query → auth. A single headerless Stack;
 * the cockpit's own paged navigation lives inside the index route.
 */
export default function RootLayout() {
  return (
    <ThemeProvider>
      <RootLayoutInner />
    </ThemeProvider>
  );
}

function RootLayoutInner() {
  const colors = useColors();

  // Tell React Query when the app is foreground/background. Without this the RN
  // focusManager assumes "always focused", so every refetchInterval keeps polling
  // the network while the phone is in your pocket. Backgrounding now pauses all
  // interval refetches (and they resume on return) — the REST-poll analog of the
  // market WS's own AppState pause.
  useEffect(() => {
    let wasActive = AppState.currentState === 'active';
    focusManager.setFocused(wasActive);
    const sub = AppState.addEventListener('change', (status) => {
      const isActive = status === 'active';
      focusManager.setFocused(isActive);
      if (isActive && !wasActive) {
        // A backgrounded process cannot consume WS control frames. Reconcile
        // immediately on resume; cold starts reconcile when the query mounts.
        void queryClient.invalidateQueries({ queryKey: ['price-alerts'] });
      }
      wasActive = isActive;
    });
    return () => sub.remove();
  }, []);

  return (
    <GestureHandlerRootView style={{ flex: 1, backgroundColor: colors.bg }}>
      <SafeAreaProvider>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <Stack
              screenOptions={{
                headerShown: false,
                contentStyle: { backgroundColor: colors.bg },
                animation: 'fade',
              }}
            />
          </AuthProvider>
        </QueryClientProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
