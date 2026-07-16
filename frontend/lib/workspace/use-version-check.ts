"use client";

import { useEffect, useRef } from "react";

import { useWorkspaceToast } from "@/components/workspace/workspace-toast";

const POLL_MS = 5 * 60_000;

async function fetchBuildId(): Promise<string | null> {
  try {
    const res = await fetch("/api/version", { cache: "no-store" });
    if (!res.ok) return null;
    const data = (await res.json()) as { buildId?: string | null };
    return data.buildId ?? null;
  } catch {
    return null;
  }
}

/**
 * Polls /api/version for the server's build id. The first successful read is
 * the baseline; any later change means a new deploy shipped while this tab was
 * open, so we toast once (latched) offering a reload. In dev the route returns
 * null (no .next/BUILD_ID), so nothing is ever captured and it never toasts.
 */
export function useVersionCheck() {
  const { showToast } = useWorkspaceToast();
  const baselineRef = useRef<string | null>(null);
  const notifiedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    async function check() {
      if (cancelled || notifiedRef.current) return;
      const buildId = await fetchBuildId();
      if (cancelled || !buildId) return;
      if (baselineRef.current === null) {
        baselineRef.current = buildId;
        return;
      }
      if (buildId !== baselineRef.current && !notifiedRef.current) {
        notifiedRef.current = true;
        showToast("A new version is available.", "info", {
          label: "Reload",
          onClick: () => location.reload(),
        });
      }
    }

    void check();
    const id = setInterval(() => void check(), POLL_MS);

    function onVisible() {
      if (document.visibilityState === "visible") void check();
    }
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [showToast]);
}
