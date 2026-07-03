import { View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';

import { useResearch } from '@/api/queries';
import { BackRow, ErrorState, Screen, Skeleton, Stack, Text } from '@/components/ui';
import { DocViewer } from '@/features/reports/doc-viewer';
import { spacing } from '@/theme';

export default function ResearchScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ id: string }>();
  const id = typeof params.id === 'string' ? params.id : (params.id?.[0] ?? '');
  const research = useResearch(id);

  if (research.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
          <BackRow onBack={() => router.back()} />
          <ErrorState onRetry={research.refetch} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
        <Stack gap={spacing.md} style={{ flex: 1 }}>
          <BackRow onBack={() => router.back()} />

          {/* No screen heading: the markdown leads with its own `# topic` h1. */}
          {!research.isLoading && research.data && research.data.status !== 'completed' ? (
            <Text variant="caption" color="textFaint">
              still {research.data.status}…
            </Text>
          ) : null}

          {research.isLoading ? (
            <Skeleton style={{ flex: 1 }} />
          ) : research.data ? (
            <DocViewer content={research.data.markdown} mode="markdown" />
          ) : null}
        </Stack>
      </View>
    </Screen>
  );
}
