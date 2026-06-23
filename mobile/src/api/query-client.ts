import { QueryClient } from '@tanstack/react-query';

/** Shared React Query client for the mobile app. */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

export { QueryClient, QueryClientProvider } from '@tanstack/react-query';
