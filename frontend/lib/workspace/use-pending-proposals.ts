"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchPendingProposals,
  type PendingProposal,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/workspace/use-notification-store";

export function usePendingProposals() {
  const [proposals, setProposals] = useState<PendingProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const requestIdRef = useRef(0);
  const notifications = useNotificationStore((s) => s.notifications);
  const prevProposalAlertCountRef = useRef(0);

  const refetch = useCallback(() => {
    const requestId = ++requestIdRef.current;

    fetchPendingProposals()
      .then((next) => {
        if (requestId !== requestIdRef.current) return;
        setProposals(next);
      })
      .catch(() => {
        if (requestId !== requestIdRef.current) return;
      })
      .finally(() => {
        if (requestId !== requestIdRef.current) return;
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  useEffect(() => {
    const id = setInterval(refetch, 15_000);
    return () => clearInterval(id);
  }, [refetch]);

  useEffect(() => {
    const proposalAlertCount = notifications.filter(
      (n) => n.source === "proposal",
    ).length;
    if (proposalAlertCount > prevProposalAlertCountRef.current) {
      refetch();
    }
    prevProposalAlertCountRef.current = proposalAlertCount;
  }, [notifications, refetch]);

  return { proposals, loading, refetch };
}
