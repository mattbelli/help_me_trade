"use client";

import { CheckIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

type ProposalInput = {
  symbol?: string;
  side?: string;
  qty?: number;
  order_type?: string;
  limit_price?: number;
};

type Props = {
  input: ProposalInput;
  approvalId: string;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  disabled?: boolean;
};

export function OrderProposalCard({
  input,
  approvalId,
  onApprove,
  onReject,
  disabled,
}: Props) {
  const side = (input.side ?? "buy").toUpperCase();
  const orderType = (input.order_type ?? "market").toUpperCase();

  return (
    <div className="space-y-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm shadow-sm">
      <div>
        <p className="font-medium text-foreground">Paper order — your approval needed</p>
        <p className="text-xs text-muted-foreground">
          Review below, then approve or reject. Nothing is sent to your broker
          until Phase 4 (RiskEngine).
        </p>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <div>
          <dt className="text-muted-foreground">Symbol</dt>
          <dd className="font-mono font-medium">{input.symbol ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Side</dt>
          <dd className="font-medium">{side}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Qty</dt>
          <dd className="font-medium">{input.qty ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Type</dt>
          <dd className="font-medium">{orderType}</dd>
        </div>
        {input.limit_price != null ? (
          <div className="col-span-2">
            <dt className="text-muted-foreground">Limit</dt>
            <dd className="font-medium">${input.limit_price}</dd>
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
