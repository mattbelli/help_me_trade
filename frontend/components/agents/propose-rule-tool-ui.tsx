"use client";

import { makeAssistantToolUI } from "@assistant-ui/react";
import { CheckCircle2Icon, Loader2Icon, XCircleIcon } from "lucide-react";

import { RuleProposalCard } from "@/components/agents/rule-proposal-card";
import { cn } from "@/lib/utils";

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

type ProposalArgs = {
  spec?: RuleSpec;
};

type ProposalResult = {
  status?: string;
  rule_id?: string;
  rule_name?: string;
  summary?: string;
  message?: string;
  error?: string;
};

function isProposalResult(value: unknown): value is ProposalResult {
  return typeof value === "object" && value !== null;
}

function ProposalStatusBanner({
  variant,
  title,
  description,
  className,
}: {
  variant: "success" | "rejected" | "processing";
  title: string;
  description?: string;
  className?: string;
}) {
  const Icon =
    variant === "success"
      ? CheckCircle2Icon
      : variant === "rejected"
        ? XCircleIcon
        : Loader2Icon;

  return (
    <div
      className={cn(
        "my-2 rounded-lg border p-3 text-sm",
        variant === "success" &&
          "border-[var(--c-green)]/40 bg-[var(--c-green)]/10 text-[var(--c-green)]",
        variant === "rejected" &&
          "border-border/70 bg-muted/40 text-muted-foreground",
        variant === "processing" &&
          "border-[var(--c-accent)]/40 bg-[var(--c-accent-soft)] text-foreground",
        className,
      )}
    >
      <div className="flex items-start gap-2">
        <Icon
          className={cn(
            "mt-0.5 size-4 shrink-0",
            variant === "processing" && "animate-spin",
          )}
        />
        <div className="min-w-0 space-y-1">
          <p className="font-medium">{title}</p>
          {description ? (
            <p className="text-xs leading-relaxed opacity-90">{description}</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export const ProposeRuleToolUI = makeAssistantToolUI<ProposalArgs, ProposalResult>(
  {
    toolName: "propose_rule",
    display: "standalone",
    render: ({
      args,
      result,
      isError,
      status,
      approval,
      respondToApproval,
    }) => {
      const pendingApproval =
        approval?.id != null &&
        approval.approved === undefined &&
        approval.resolution === undefined;

      if (pendingApproval) {
        return (
          <RuleProposalCard
            input={args}
            approvalId={approval.id}
            onApprove={() => respondToApproval({ approved: true })}
            onReject={() =>
              respondToApproval({
                approved: false,
                reason: "User rejected rule proposal",
              })
            }
          />
        );
      }

      if (approval?.approved === false || approval?.resolution === "cancelled") {
        return (
          <ProposalStatusBanner
            variant="rejected"
            title="Alert rule cancelled"
            description="No rule was saved or armed."
          />
        );
      }

      if (approval?.approved === true && !result && status?.type === "running") {
        return (
          <ProposalStatusBanner
            variant="processing"
            title="Arming…"
            description="Saving and enabling your alert rule for the signal scan."
          />
        );
      }

      if (result && isProposalResult(result)) {
        if (result.error || isError) {
          return (
            <ProposalStatusBanner
              variant="rejected"
              title="Could not arm alert rule"
              description={result.error ?? "The propose_rule tool returned an error."}
            />
          );
        }

        const name = result.rule_name ?? args.spec?.name ?? "Alert rule";

        return (
          <ProposalStatusBanner
            variant="success"
            title={`Armed: ${name}`}
            description={
              result.message ??
              result.summary ??
              "Rule saved and enabled — the signal scan will evaluate it."
            }
          />
        );
      }

      return null;
    },
  },
);
