"use client";

import { PageHeader } from "@/components/layout/page-header";
import { RulesSection } from "@/components/rules/rules-section";

export function RulesPage() {
  return (
    <div className="space-y-8">
      <PageHeader
        title="Rules"
        subtitle="Portfolio alerts evaluated on each broker poll. Triggered events are logged below."
      />
      <RulesSection />
    </div>
  );
}
