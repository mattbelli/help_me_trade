"use client";

import { useEffect, useRef, useState } from "react";
import { CheckIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import { ProposalScore } from "@/components/workspace/proposal-score";
import { useWorkspaceToast } from "@/components/workspace/workspace-toast";
import {
  approveProposal,
  formatMoney,
  formatQty,
  rejectProposal,
  type PendingProposal,
} from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";

type Props = {
  proposal: PendingProposal;
  onResolved: () => void;
  channel?: ChannelId;
};

function formatCountdown(expiresAt: string, now: number): string {
  const ms = Date.parse(expiresAt) - now;
  if (!Number.isFinite(ms) || ms <= 0) return "0:00";
  const totalSec = Math.ceil(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

export function PendingProposalCard({
  proposal,
  onResolved,
  channel = "none",
}: Props) {
  const { showToast } = useWorkspaceToast();
  const { publish, linked } = usePublishSymbol(channel);
  const [pending, setPending] = useState(false);
  const [armedProposalId, setArmedProposalId] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const approveButtonRef = useRef<HTMLButtonElement>(null);
  const expiredNotifiedRef = useRef(false);

  const side = proposal.side.toUpperCase();
  const approveArmed = armedProposalId === proposal.id;
  const hasExpiry =
    proposal.expires_at != null && Number.isFinite(Date.parse(proposal.expires_at));
  const countdown = hasExpiry
    ? formatCountdown(proposal.expires_at!, now)
    : null;
  const expired = countdown === "0:00";

  useEffect(() => {
    expiredNotifiedRef.current = false;
  }, [proposal.id]);

  useEffect(() => {
    if (!hasExpiry) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [hasExpiry, proposal.expires_at]);

  useEffect(() => {
    if (!expired || expiredNotifiedRef.current) return;
    expiredNotifiedRef.current = true;
    onResolved();
  }, [expired, onResolved]);

  useEffect(() => {
    if (!approveArmed) return;

    const timeoutId = window.setTimeout(
      () => setArmedProposalId(null),
      5000,
    );
    const disarmOnOutsidePointer = (event: PointerEvent) => {
      if (
        !(event.target instanceof Node) ||
        !approveButtonRef.current?.contains(event.target)
      ) {
        setArmedProposalId(null);
      }
    };

    document.addEventListener("pointerdown", disarmOnOutsidePointer);
    return () => {
      window.clearTimeout(timeoutId);
      document.removeEventListener("pointerdown", disarmOnOutsidePointer);
    };
  }, [approveArmed]);

  const disabled = pending || expired;
  const confirmationDetails = [
    typeof proposal.notional === "number" &&
    Number.isFinite(proposal.notional)
      ? `~${formatMoney(proposal.notional)}`
      : null,
    typeof proposal.limit_price === "number" &&
    Number.isFinite(proposal.limit_price)
      ? `${formatMoney(proposal.limit_price)} limit`
      : null,
  ].filter((detail): detail is string => detail !== null);
  const approveLabel = approveArmed
    ? `Confirm ${side} ${formatQty(proposal.qty)} ${proposal.symbol}${
        confirmationDetails.length > 0
          ? ` · ${confirmationDetails.join(" · ")}`
          : ""
      }`
    : "Approve";

  async function handleApprove() {
    setPending(true);
    try {
      const body = await approveProposal(proposal.id, proposal.payload_hash);
      switch (body.status) {
        case "executed":
          showToast(
            `Approved — ${body.execution_mode === "dry_run" ? "dry run logged" : "order placed"}`,
          );
          break;
        case "blocked":
          showToast(`Blocked: ${typeof body.reason === "string" ? body.reason : "risk check"}`, "error");
          break;
        case "expired":
          showToast("Proposal expired", "error");
          break;
        case "already_decided":
          showToast("Already decided");
          break;
        case "hash_mismatch":
          showToast("Proposal changed — refresh", "error");
          break;
        default:
          showToast("Approve failed", "error");
      }
    } catch {
      showToast("Approve failed", "error");
    } finally {
      setPending(false);
      onResolved();
    }
  }

  async function handleReject() {
    setPending(true);
    try {
      const body = await rejectProposal(proposal.id);
      if (body.status === "already_decided") {
        showToast("Already decided");
      } else {
        showToast("Rejected");
      }
    } catch {
      showToast("Reject failed", "error");
    } finally {
      setPending(false);
      onResolved();
    }
  }

  function handleApproveClick() {
    if (!approveArmed) {
      setArmedProposalId(proposal.id);
      return;
    }
    setArmedProposalId(null);
    void handleApprove();
  }

  return (
    <div className="group/row space-y-3 rounded-lg border border-[color-mix(in_srgb,var(--c-amber)_30%,transparent)] bg-[color-mix(in_srgb,var(--c-amber)_5%,transparent)] p-3 text-[length:var(--fs-title)] shadow-sm">
      <div>
        <p className="font-medium text-foreground">{proposal.summary}</p>
        {countdown != null ? (
          <p className="text-[length:var(--fs-body)] text-muted-foreground">
            {expired ? "Expired" : `Expires in ${countdown}`}
          </p>
        ) : null}
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[length:var(--fs-body)]">
        <div>
          <dt className="text-muted-foreground">Symbol</dt>
          <dd className="font-mono font-medium">
            <ClickableSymbol
              symbol={proposal.symbol}
              linked={linked}
              onActivate={() => publish(proposal.symbol)}
            />
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Side</dt>
          <dd className="font-medium">{side}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Qty</dt>
          <dd className="font-medium">{proposal.qty}</dd>
        </div>
      </dl>

      {proposal.score ? <ProposalScore score={proposal.score} /> : null}

      <div className="flex gap-2">
        <Button
          ref={approveButtonRef}
          type="button"
          size="sm"
          className="gap-1.5"
          disabled={disabled}
          onBlur={() => setArmedProposalId(null)}
          onClick={handleApproveClick}
          onKeyDown={(event) => {
            if (event.key === "Escape") setArmedProposalId(null);
          }}
        >
          <CheckIcon className="size-3.5" />
          {pending ? "…" : approveLabel}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="gap-1.5"
          disabled={disabled}
          onClick={() => void handleReject()}
        >
          <XIcon className="size-3.5" />
          Reject
        </Button>
      </div>
    </div>
  );
}
