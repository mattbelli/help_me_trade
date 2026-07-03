import { View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';

import { useResearch } from '@/api/queries';
import { BackRow, ErrorState, Screen, Skeleton, Stack, Text } from '@/components/ui';
import { DocViewer } from '@/features/reports/doc-viewer';
import { colors, spacing } from '@/theme';

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

  const html = research.data?.html;
  const useHtml = typeof html === 'string' && html.length > 0;

  return (
    <Screen>
      <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
        <Stack gap={spacing.md} style={{ flex: 1 }}>
          <BackRow onBack={() => router.back()} />

          {research.isLoading ? (
            <Stack gap={spacing.sm}>
              <Skeleton height={28} width="80%" />
              <Skeleton height={14} width={120} />
            </Stack>
          ) : research.data ? (
            <Stack gap={spacing.xs}>
              <Text variant="title">
                {research.data.topic}
                {research.data.symbol ? (
                  <Text variant="title" color="textMuted">{` · ${research.data.symbol}`}</Text>
                ) : null}
              </Text>
              {research.data.status !== 'completed' ? (
                <Text variant="caption" color="textFaint">
                  still {research.data.status}…
                </Text>
              ) : null}
            </Stack>
          ) : null}

          <View style={{ flex: 1, marginHorizontal: -spacing.xl, backgroundColor: colors.bg }}>
            {research.isLoading ? (
              <View style={{ flex: 1, paddingHorizontal: spacing.xl }}>
                <Skeleton style={{ flex: 1 }} />
              </View>
            ) : research.data ? (
              <DocViewer
                content={useHtml ? html : research.data.markdown}
                mode={useHtml ? 'html' : 'markdown'}
              />
            ) : null}
          </View>
        </Stack>
      </View>
    </Screen>
  );
}
