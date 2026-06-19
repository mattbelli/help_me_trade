"use client";

import { CheckIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

type RuleAction = {
  type?: string;
  message?: string;
  trigger_on?: string;
  cooldown_seconds?: number;
};

type RuleSpec = {
  name?: string;
  condition?: unknown;
  action?: RuleAction;
};

type ProposalInput = {
  spec?: RuleSpec;
};

const OPERATOR_LABELS: Record<string, string> = {
  lessThan: "<",
  lessThanInclusive: "≤",
  greaterThan: ">",
  greaterThanInclusive: "≥",
  equal: "==",
  notEqual: "!=",
  in: "in",
  contains: "contains",
};

const BOOL_METRICS = new Set([
  "cross_above_sma_200",
  "cross_below_sma_200",
  "has_positions",
]);

type LeafCondition = {
  metric?: string;
  symbol?: string;
  operator?: string;
  value?: unknown;
};

type GroupCondition = {
  all?: unknown[];
  any?: unknown[];
  not?: unknown;
};

type Condition = LeafCondition | GroupCondition;

function isLeafCondition(value: unknown): value is LeafCondition {
  return typeof value === "object" && value !== null && "metric" in value;
}

function isGroupCondition(value: Condition): value is GroupCondition {
  return (
    typeof value === "object" &&
    value !== null &&
    ("all" in value || "any" in value || "not" in value)
  );
}

function formatLeafCondition(leaf: LeafCondition): string {
  const metric = leaf.metric ?? "metric";
  const prefix = leaf.symbol ? `${leaf.symbol} ` : "";
  if (BOOL_METRICS.has(metric)) {
    return `${prefix}${metric.replace(/_/g, " ")}`;
  }
  const op = OPERATOR_LABELS[leaf.operator ?? ""] ?? leaf.operator ?? "?";
  const value =
    leaf.value === undefined || leaf.value === null ? "—" : String(leaf.value);
  return `${prefix}${metric} ${op} ${value}`;
}

function formatCondition(condition: unknown): string {
  if (!condition || typeof condition !== "object") return "—";
  if (isLeafCondition(condition)) {
    return formatLeafCondition(condition);
  }
  if (isGroupCondition(condition)) {
    if (condition.all?.length) {
      return condition.all
        .map((child) =>
          formatCondition(child as Condition),
        )
        .join(" AND ");
    }
    if (condition.any?.length) {
      return condition.any
        .map((child) =>
          formatCondition(child as Condition),
        )
        .join(" OR ");
    }
    if (condition.not) {
      return `NOT (${formatCondition(condition.not as Condition)})`;
    }
  }
  return "—";
}

type Props = {
  input: ProposalInput;
  approvalId: string;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  disabled?: boolean;
};

export function RuleProposalCard({
  input,
  approvalId,
  onApprove,
  onReject,
  disabled,
}: Props) {
  const spec = input.spec ?? {};
  const action = spec.action ?? {};
  const actionType = (action.type ?? "alert").toUpperCase();

  return (
    <div className="space-y-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm shadow-sm">
      <div>
        <p className="font-medium text-foreground">Alert rule — your approval needed</p>
        <p className="text-xs text-muted-foreground">
          Review below, then approve or reject. On approve the rule is saved and
          armed for the signal scan.
        </p>
      </div>
      <dl className="grid gap-y-1 text-xs">
        <div>
          <dt className="text-muted-foreground">Name</dt>
          <dd className="font-medium">{spec.name ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Condition</dt>
          <dd className="font-mono font-medium leading-relaxed">
            {formatCondition(spec.condition)}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Action</dt>
          <dd className="font-medium">{actionType}</dd>
        </div>
        {action.message ? (
          <div>
            <dt className="text-muted-foreground">Message</dt>
            <dd className="leading-relaxed">{action.message}</dd>
          </div>
        ) : null}
      </dl>
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          className="gap-1.5"
          disabled={disabled}
          onClick={() => onApprove(approvalId)}
        >
          <CheckIcon className="size-3.5" />
          Approve
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="gap-1.5"
          disabled={disabled}
          onClick={() => onReject(approvalId)}
        >
          <XIcon className="size-3.5" />
          Reject
        </Button>
      </div>
    </div>
  );
}
