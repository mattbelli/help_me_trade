import { View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';

import { useReport } from '@/api/queries';
import { BackRow, ErrorState, Screen, Skeleton, Stack, Text } from '@/components/ui';
import { DocViewer } from '@/features/reports/doc-viewer';
import { spacing, useColors } from '@/theme';

const MONTHS_SHORT = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
] as const;

function fmtDate(iso?: string | null): string | null {
  if (!iso) return null;
  const [year, month, day] = iso.split('-');
  const mi = Number(month) - 1;
  const name = MONTHS_SHORT[mi];
  if (!name || !day) return iso;
  return `${name} ${Number(day)}, ${year}`;
}

export default function ReportScreen() {
  const colors = useColors();
  const router = useRouter();
  const params = useLocalSearchParams<{ id: string }>();
  const id = typeof params.id === 'string' ? params.id : (params.id?.[0] ?? '');
  const report = useReport(id);

  if (report.isError) {
    return (
      <Screen style={{ backgroundColor: colors.surface }}>
        <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.md }}>
          <BackRow onBack={() => router.back()} />
          <ErrorState onRetry={report.refetch} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen style={{ backgroundColor: colors.surface }}>
      <View
        style={{
          flex: 1,
          paddingTop: spacing.sm,
          paddingHorizontal: spacing.md,
        }}
      >
        <Stack gap={spacing.md} style={{ flex: 1 }}>
          <BackRow onBack={() => router.back()} />

          {report.isLoading ? (
            <Stack gap={spacing.sm}>
              <Skeleton height={28} width="80%" />
              <Skeleton height={14} width={120} />
            </Stack>
          ) : report.data ? (
            <Stack gap={spacing.xs}>
              <Text variant="title">{report.data.title}</Text>
              {report.data.generated_at ? (
                <Text variant="caption" color="textFaint">
                  {fmtDate(report.data.generated_at.slice(0, 10)) ?? report.data.generated_at}
                </Text>
              ) : null}
            </Stack>
          ) : null}

          {/* Break out of the screen's md gutter so the document runs near full-width. */}
          <View style={{ flex: 1, marginHorizontal: -spacing.md }}>
            {report.isLoading ? (
              <Skeleton style={{ flex: 1 }} />
            ) : report.data ? (
              <DocViewer
                content={report.data.body}
                // Unrecognized formats fall back to plain text rather than mis-rendering.
                mode={
                  report.data.format === 'html' || report.data.format === 'markdown'
                    ? report.data.format
                    : 'text'
                }
              />
            ) : null}
          </View>
        </Stack>
      </View>
    </Screen>
  );
}
