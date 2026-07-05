"use client";

import { PendingProposalCard } from "@/components/workspace/pending-proposal-card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ChannelId } from "@/lib/workspace/channels";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { usePendingProposals } from "@/lib/workspace/use-pending-proposals";

export function AgentProposalsWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { proposals, loading, refetch } = usePendingProposals();

  if (loading && proposals.length === 0) {
    return <Skeleton className="m-3 h-full min-h-24 rounded-md" />;
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto rounded-lg border border-[var(--border)] bg-[var(--panel)] p-3">
      <header className="mb-2 flex shrink-0 items-baseline justify-between gap-2">
        <h2 className="font-mono text-[length:var(--fs-body)] font-semibold uppercase tracking-[0.08em] text-[var(--fg-dim)]">
          Pending approvals
        </h2>
        {proposals.length > 0 ? (
          <span className="font-mono text-[length:var(--fs-table)] tabular-nums text-[var(--fg-faint)]">
            {proposals.length}
          </span>
        ) : null}
      </header>

      {proposals.length === 0 ? (
        <p className="font-mono text-[length:var(--fs-body)] text-[var(--fg-dim)]">No pending proposals</p>
      ) : (
        <div className="space-y-2">
          {proposals.map((proposal) => (
            <PendingProposalCard
              key={proposal.id}
              proposal={proposal}
              onResolved={refetch}
              channel={channel}
            />
          ))}
        </div>
      )}
    </div>
  );
}
