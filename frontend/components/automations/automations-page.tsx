"use client";

import { useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { SettingsStrategiesPanel } from "@/components/settings/settings-strategies-panel";
import { RulesSection } from "@/components/rules/rules-section";
import { AutomationsReportsSection } from "@/components/automations/automations-reports-section";
import { RunningNow } from "@/components/automations/running-now";
import { PriceAlertsWidget } from "@/components/workspace/widgets/price-alerts-widget";
import { WorkspaceToastProvider } from "@/components/workspace/workspace-toast";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAutomationsOverview } from "@/lib/use-automations-overview";

function SectionCard({
  title,
  count,
  countLabel,
  note,
  children,
}: {
  title: string;
  count?: number;
  countLabel?: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center gap-2 border-b pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
        {countLabel ? (
          <Badge variant="outline" className="text-[10px] font-normal">
            {countLabel}
          </Badge>
        ) : count !== undefined ? (
          <Badge variant="outline" className="text-[10px] font-normal">
            {count} active
          </Badge>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3 pt-4">
        {note ? (
          <p className="text-xs text-muted-foreground">{note}</p>
        ) : null}
        {children}
      </CardContent>
    </Card>
  );
}

export function AutomationsPage() {
  const { overview, loading } = useAutomationsOverview();
  const [rulesEnabled, setRulesEnabled] = useState<number | null>(null);

  const rulesCount = rulesEnabled ?? overview.rules.active;

  return (
    <div className="space-y-8">
      <PageHeader
        title="Automations"
        subtitle="Background strategies, scans, scheduled reports, and price alerts — all in one place."
      />

      <RunningNow overview={overview} loading={loading} />

      <SectionCard title="Strategies" count={overview.strategies.active}>
        <SettingsStrategiesPanel embedded />
      </SectionCard>

      <SectionCard title="Scans & Rules" count={rulesCount}>
        <RulesSection
          showCards={false}
          onCountsChange={({ enabled }) => setRulesEnabled(enabled)}
        />
      </SectionCard>

      <SectionCard
        title="Reports"
        countLabel={overview.reports.enabled ? "scheduled" : "off"}
      >
        <AutomationsReportsSection />
      </SectionCard>

      <SectionCard title="Alerts" count={overview.alerts.active}>
        <div className="overflow-hidden rounded-lg border border-border/80 bg-card/20">
          <div className="min-h-[200px]">
            {/* widget hooks need the workspace toast context (publish confirmations) */}
            <WorkspaceToastProvider>
              <PriceAlertsWidget
                instanceId="legacy-alerts"
                config={{}}
                onConfigChange={() => {}}
              />
            </WorkspaceToastProvider>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          Earnings and RVOL alerts run in the background but don&apos;t have a UI
          surface yet — coming in a future release.
        </p>
      </SectionCard>
    </div>
  );
}
