"use client";

// Strategy Lab — page shell. Renders full-bleed inside the workspace shell
// (the toolbar + terminal strip stay) when the LAB_PAGE_ID sentinel is active.
// Top tabs: overview | live | backtesting (plain lowercase words, accent on
// active). Internal navigation is mirrored to the URL hash so actor pages are
// deep-linkable, e.g. /#lab/live/llm/acct_alpaca_fable.

import { useCallback, useEffect, useState } from "react";

import { cn } from "@/lib/utils";

import { BacktestingTab } from "./backtesting-tab";
import {
  DEFAULT_LAB_ROUTE,
  isLabHash,
  labHash,
  parseLabHash,
  type LabRoute,
  type LabTab,
} from "./lab-nav";
import { LiveTab } from "./live-tab";
import { OverviewTab } from "./overview-tab";

const TABS: LabTab[] = ["overview", "live", "backtesting"];

export function LabPage() {
  const [route, setRoute] = useState<LabRoute>(() =>
    typeof window !== "undefined"
      ? parseLabHash(window.location.hash)
      : DEFAULT_LAB_ROUTE,
  );

  const navigate = useCallback((next: LabRoute) => {
    setRoute(next);
    const h = labHash(next);
    if (typeof window !== "undefined" && window.location.hash !== h) {
      window.history.replaceState(null, "", h);
    }
  }, []);

  // hashchange → route (external deep links / back-forward while mounted).
  useEffect(() => {
    function onHash() {
      if (isLabHash(window.location.hash)) {
        setRoute(parseLabHash(window.location.hash));
      }
    }
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // On mount canonicalize the hash; on unmount drop it so a later reload doesn't
  // silently reopen the Lab over the user's real page.
  useEffect(() => {
    const h = labHash(route);
    if (window.location.hash !== h) {
      window.history.replaceState(null, "", h);
    }
    return () => {
      if (isLabHash(window.location.hash)) {
        window.history.replaceState(
          null,
          "",
          window.location.pathname + window.location.search,
        );
      }
    };
    // mount/unmount only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="h-full overflow-y-auto bg-background tabular-nums">
      <div className="mx-auto max-w-[1440px] px-5 py-4">
        <div className="mb-5 flex items-center gap-1 border-b border-[var(--line-divider)]">
          {TABS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() =>
                navigate(
                  t === "live"
                    ? { tab: "live", family: route.family, actor: null }
                    : { ...DEFAULT_LAB_ROUTE, tab: t },
                )
              }
              className={cn(
                "-mb-px h-10 border-b-2 px-4 text-[length:var(--fs-body)] lowercase tracking-[0.03em] outline-none",
                route.tab === t
                  ? "border-[var(--c-accent)] text-foreground"
                  : "border-transparent text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
              )}
            >
              {t}
            </button>
          ))}
          <span className="ml-auto text-[length:var(--fs-table)] uppercase tracking-[0.14em] text-[var(--fg-faint)]">
            strategy <span className="text-[var(--c-accent)]">lab</span>
          </span>
        </div>

        {route.tab === "overview" ? (
          <OverviewTab onRoute={navigate} />
        ) : route.tab === "live" ? (
          <LiveTab route={route} onRoute={navigate} />
        ) : (
          <BacktestingTab />
        )}
      </div>
    </div>
  );
}
