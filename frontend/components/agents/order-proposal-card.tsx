"use client";

import { CheckIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

type ProposalInput = {
  symbol?: string;
  side?: string;
  qty?: number;
  order_type?: string;
  limit_price?: number;
  asset_class?: string;
  occ_symbol?: string;
  option_underlying?: string;
  option_expiration?: string;
  option_strike?: number;
  option_right?: string;
  multiplier?: number;
};

type Props = {
  input: ProposalInput;
  approvalId: string;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  disabled?: boolean;
};

function isOptionProposal(input: ProposalInput): boolean {
  return (
    input.asset_class === "us_option" ||
    input.occ_symbol != null ||
    input.option_underlying != null
  );
}

function formatMoney(value: number): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function OrderProposalCard({
  input,
  approvalId,
  onApprove,
  onReject,
  disabled,
}: Props) {
  const side = (input.side ?? "buy").toUpperCase();
  const orderType = (input.order_type ?? "market").toUpperCase();
  const isOption = isOptionProposal(input);
  const contracts = input.qty ?? 0;
  const premium = input.limit_price ?? 0;
  const multiplier = input.multiplier ?? 100;
  const totalCost = premium * multiplier * contracts;
  const rightLabel =
    input.option_right === "C"
      ? "Call"
      : input.option_right === "P"
        ? "Put"
        : (input.option_right ?? "—");

  return (
    <div className="space-y-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm shadow-sm">
      <div>
        <p className="font-medium text-foreground">
          {isOption ? "Paper option order" : "Paper order"} — your approval needed
        </p>
        <p className="text-xs text-muted-foreground">
          Review below, then approve or reject. Long buy-to-open only; nothing is sent
          until you approve.
        </p>
      </div>

      {isOption ? (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <div>
            <dt className="text-muted-foreground">Underlying</dt>
            <dd className="font-mono font-medium">
              {input.option_underlying ?? input.symbol ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Type</dt>
            <dd className="font-medium">{rightLabel}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Strike</dt>
            <dd className="font-medium">
              {input.option_strike != null ? `$${input.option_strike}` : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Expiry</dt>
            <dd className="font-medium">{input.option_expiration ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Contracts</dt>
            <dd className="font-medium">{contracts || "—"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Side</dt>
            <dd className="font-medium">{side}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Order</dt>
            <dd className="font-medium">{orderType}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Premium (limit)</dt>
            <dd className="font-medium">
              {input.limit_price != null ? `$${formatMoney(premium)}/share` : "—"}
            </dd>
          </div>
          {input.occ_symbol ? (
            <div className="col-span-2">
              <dt className="text-muted-foreground">OCC</dt>
              <dd className="font-mono text-[11px] font-medium">{input.occ_symbol}</dd>
            </div>
          ) : null}
          <div className="col-span-2 border-t border-amber-500/20 pt-2">
            <dt className="text-muted-foreground">Total cost (max loss)</dt>
            <dd className="text-sm font-semibold text-foreground">
              ${formatMoney(totalCost)}
              <span className="ml-1 text-xs font-normal text-muted-foreground">
                = ${formatMoney(premium)} × {multiplier} × {contracts || 0}
              </span>
            </dd>
          </div>
        </dl>
      ) : (
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
      )}

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
