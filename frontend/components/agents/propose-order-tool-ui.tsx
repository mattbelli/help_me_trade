"use client";

import { makeAssistantToolUI } from "@assistant-ui/react";
import { CheckCircle2Icon, Loader2Icon, XCircleIcon } from "lucide-react";

import { OrderProposalCard } from "@/components/agents/order-proposal-card";
import { cn } from "@/lib/utils";

type ProposalArgs = {
  symbol?: string;
  side?: string;
  qty?: number;
  order_type?: string;
  limit_price?: number;
};

type ProposalResult = {
  status?: string;
  execution_mode?: string;
  execution_id?: string;
  proposal?: ProposalArgs;
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
  variant: "success" | "rejected" | "processing" | "dry_run";
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
          "border-emerald-500/40 bg-emerald-500/10 text-emerald-950 dark:text-emerald-100",
        variant === "dry_run" &&
          "border-amber-500/40 bg-amber-500/10 text-amber-950 dark:text-amber-100",
        variant === "rejected" &&
          "border-destructive/40 bg-destructive/10 text-destructive dark:text-red-200",
        variant === "processing" &&
          "border-amber-500/30 bg-amber-500/5 text-foreground",
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

function executionOutcomeBanner(result: ProposalResult): {
  variant: "success" | "rejected" | "dry_run";
  title: string;
  description: string;
} {
  const proposal = result.proposal;
  const side = (proposal?.side ?? "buy").toUpperCase();
  const qty = proposal?.qty ?? "—";
  const symbol = proposal?.symbol ?? "—";
  const execId = result.execution_id;
  const trace = execId ? ` Execution ID: ${execId}.` : "";

  if (result.status === "rejected" || result.error) {
    return {
      variant: "rejected",
      title: "Rejected",
      description: `Rejected: ${result.error ?? result.message ?? "Order was rejected."}${trace}`,
    };
  }

  if (result.status === "submitted" || result.execution_mode === "paper") {
    return {
      variant: "success",
      title: "Submitted to Alpaca paper",
      description: `${side} ${qty} ${symbol} sent to Alpaca paper account.${trace}`,
    };
  }

  if (result.status === "dry_run" || result.execution_mode === "dry_run") {
    return {
      variant: "dry_run",
      title: "DRY RUN — no order placed",
      description:
        result.message ??
        `Would ${side} ${qty} ${symbol} — logged only, not sent to broker.${trace}`,
    };
  }

  return {
    variant: "success",
    title: `Proposal recorded: ${side} ${qty} ${symbol}`,
    description: (result.message ?? "Order recorded.") + trace,
  };
}

export const ProposeOrderToolUI = makeAssistantToolUI<ProposalArgs, ProposalResult>(
  {
    toolName: "propose_order",
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
          <OrderProposalCard
            input={args}
            approvalId={approval.id}
            onApprove={() => respondToApproval({ approved: true })}
            onReject={() =>
              respondToApproval({
                approved: false,
                reason: "User rejected order proposal",
              })
            }
          />
        );
      }

      if (approval?.approved === false || approval?.resolution === "cancelled") {
        return (
          <ProposalStatusBanner
            variant="rejected"
            title="Order proposal cancelled"
            description="No order was recorded or sent to your broker."
          />
        );
      }

      if (approval?.approved === true && !result && status?.type === "running") {
        return (
          <ProposalStatusBanner
            variant="processing"
            title="Recording your approval…"
            description="Submitting through the execution service."
          />
        );
      }

      if (result && isProposalResult(result)) {
        if (result.error || isError) {
          const outcome = executionOutcomeBanner(result);
          return (
            <ProposalStatusBanner
              variant={outcome.variant}
              title={outcome.title}
              description={outcome.description}
            />
          );
        }

        const outcome = executionOutcomeBanner(result);
        return (
          <ProposalStatusBanner
            variant={outcome.variant}
            title={outcome.title}
            description={outcome.description}
          />
        );
      }

      return null;
    },
  },
);
