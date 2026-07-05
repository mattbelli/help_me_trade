"use client";

import { useEffect, useState } from "react";
import { CheckIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ProposalScore } from "@/components/workspace/proposal-score";
import { fetchProposalScore, type ProposalScore as ProposalScoreData } from "@/lib/api";
import { cn } from "@/lib/utils";

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
  held_qty?: number;
};

type Props = {
  input: ProposalInput;
  approvalId: string;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  disabled?: boolean;
  symbol?: string;
  side?: string;
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
  symbol: symbolProp,
  side: sideProp,
}: Props) {
  const scoreSymbol = (symbolProp ?? input.symbol ?? "").trim();
  const scoreSide = (sideProp ?? input.side ?? "buy").toLowerCase();
  const shouldFetchScore = scoreSymbol.length > 0 && scoreSide === "buy";
  const [score, setScore] = useState<ProposalScoreData | null>(null);

  useEffect(() => {
    if (!shouldFetchScore) return;

    let cancelled = false;
    void fetchProposalScore(scoreSymbol, scoreSide).then((result) => {
      if (!cancelled) setScore(result);
    });

    return () => {
      cancelled = true;
    };
  }, [scoreSymbol, scoreSide, shouldFetchScore]);

  const side = (input.side ?? "buy").toUpperCase();
  const isSell = (input.side ?? "buy").toLowerCase() === "sell";
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
    <div className="space-y-3 rounded-[16px] border border-[var(--c-accent)] bg-[var(--c-accent-soft)] p-3 text-sm">
      <div>
        <p className="mb-1 font-mono text-[11px] tracking-[0.1em] lowercase text-[var(--c-accent)]">
          proposal ready
        </p>
        <p className="font-medium text-foreground">
          {isOption ? "Paper option order" : "Paper order"}
          <span
            className={cn(
              "ml-2 inline-flex items-center rounded-full px-2 py-0.5 font-mono text-xs lowercase",
              isSell
                ? "bg-[var(--c-red)]/15 text-[var(--c-red)]"
                : "bg-[var(--c-green-bg)]/15 text-[var(--c-green)]",
            )}
          >
            {side.toLowerCase()}
          </span>
          {" — your approval needed"}
        </p>
        <p className="text-xs text-muted-foreground">
          {isSell
            ? "Sell to close — exits a position you already hold; nothing is sent until you approve."
            : "Review below, then approve or reject. Long buy-to-open only; nothing is sent until you approve."}
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
          {input.held_qty != null ? (
            <div>
              <dt className="text-muted-foreground">You hold</dt>
              <dd className="font-medium">{input.held_qty}</dd>
            </div>
          ) : null}
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
          <div className="col-span-2 border-t border-[var(--border)] pt-2">
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
          {input.held_qty != null ? (
            <div>
              <dt className="text-muted-foreground">You hold</dt>
              <dd className="font-medium">{input.held_qty}</dd>
            </div>
          ) : null}
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

      {shouldFetchScore && score ? <ProposalScore score={score} /> : null}

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
