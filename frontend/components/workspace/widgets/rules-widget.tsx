"use client";

import { RulesSection } from "@/components/rules/rules-section";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

export function RulesWidget({}: WidgetProps) {
  return (
    <div className="h-full overflow-auto px-1.5 py-1">
      <RulesSection showCards={false} />
    </div>
  );
}
