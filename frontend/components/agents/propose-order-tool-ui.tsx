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
          "border-emerald-500/40 bg-emerald-500/10 text-emerald-950 dark:text-emerald-100",
        variant === "rejected" &&
          "border-border/70 bg-muted/40 text-muted-foreground",
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
            description="This is a paper proposal only — nothing is sent to the broker yet."
          />
        );
      }

      if (result && isProposalResult(result)) {
        if (result.error || isError) {
          return (
            <ProposalStatusBanner
              variant="rejected"
              title="Could not record proposal"
              description={result.error ?? "The proposal tool returned an error."}
            />
          );
        }

        const proposal = result.proposal ?? args;
        const side = (proposal.side ?? "buy").toUpperCase();
        const qty = proposal.qty ?? "—";
        const symbol = proposal.symbol ?? "—";

        return (
          <ProposalStatusBanner
            variant="success"
            title={`Proposal recorded: ${side} ${qty} ${symbol}`}
            description={
              result.message ??
              "Saved as paper intent only — not sent to Alpaca/Schwab and not shown under broker open orders."
            }
          />
        );
      }

      return null;
    },
  },
);
