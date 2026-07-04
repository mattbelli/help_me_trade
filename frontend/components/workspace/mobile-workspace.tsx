"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Bell,
  Eye,
  MessageSquare,
  Wallet,
  type LucideIcon,
} from "lucide-react";

import { PagesMenu } from "@/components/layout/pages-menu";
import { SettingsDialog } from "@/components/settings/settings-dialog";
import { ThemeSwitcher } from "@/components/workspace/theme-switcher";
import { WorkspaceThread } from "@/components/workspace/workspace-chat";
import { cn } from "@/lib/utils";
import { PendingProposalCard } from "@/components/workspace/pending-proposal-card";
import type { PendingProposal } from "@/lib/api";
import {
  useNotificationStore,
  type WorkspaceNotification,
} from "@/lib/workspace/use-notification-store";
import { usePendingProposals } from "@/lib/workspace/use-pending-proposals";
import { getWidgetDefinition } from "@/lib/workspace/widget-registry";

type TabId = "watch" | "portfolio" | "agent" | "activity";

type TabDef = {
  id: TabId;
  label: string;
  icon: LucideIcon;
};

const TABS: TabDef[] = [
  { id: "watch", label: "Watch", icon: Eye },
  { id: "portfolio", label: "Portfolio", icon: Wallet },
  { id: "agent", label: "Agent", icon: MessageSquare },
  { id: "activity", label: "Activity", icon: Bell },
];

function formatRelativeTime(iso: string): string {
  const ms = Date.now() - Date.parse(iso);
  if (!Number.isFinite(ms)) return "";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

type MobileWidgetPanelProps = {
  type: "account-summary" | "positions" | "open-orders";
  instanceId: string;
  scrollable?: boolean;
};

function MobileWidgetPanel({
  type,
  instanceId,
  scrollable = false,
}: MobileWidgetPanelProps) {
  const def = getWidgetDefinition(type);
  if (!def) return null;

  const Widget = def.component;

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--panel)]">
      <div className="border-b border-[var(--border)] px-3 py-2 font-mono text-[12px] font-semibold">
        {def.title}
      </div>
      <div className={cn(scrollable && "max-h-[40vh] overflow-auto")}>
        <Widget
          instanceId={instanceId}
          config={{}}
          onConfigChange={() => {}}
        />
      </div>
    </div>
  );
}

function WatchTab() {
  const def = getWidgetDefinition("watchlist");
  if (!def) return null;

  const Widget = def.component;

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--panel)]">
      <Widget
        instanceId="mobile-watchlist"
        config={{}}
        onConfigChange={() => {}}
      />
    </div>
  );
}

function PortfolioTab() {
  return (
    <div className="flex flex-col gap-3">
      <MobileWidgetPanel type="account-summary" instanceId="mobile-account-summary" />
      <MobileWidgetPanel
        type="positions"
        instanceId="mobile-positions"
        scrollable
      />
      <MobileWidgetPanel
        type="open-orders"
        instanceId="mobile-open-orders"
        scrollable
      />
    </div>
  );
}

type ActivityTabProps = {
  proposals: PendingProposal[];
  refetchProposals: () => void;
};

function ActivityTab({ proposals, refetchProposals }: ActivityTabProps) {
  const notifications = useNotificationStore((s) => s.notifications);
  const hydrate = useNotificationStore((s) => s.hydrate);

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  const sorted = useMemo(
    () =>
      [...notifications].sort(
        (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
      ),
    [notifications],
  );

  if (proposals.length === 0 && sorted.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-16 text-[var(--fg-dim)]">
        <Bell className="size-8 text-[var(--fg-faint)]" />
        <p className="text-sm">No activity yet</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {proposals.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h2 className="font-mono text-[11px] font-semibold uppercase tracking-wide text-[var(--fg-dim)]">
            Pending approvals
          </h2>
          {proposals.map((p) => (
            <PendingProposalCard
              key={p.id}
              proposal={p}
              onResolved={refetchProposals}
            />
          ))}
        </section>
      ) : null}
      {sorted.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {sorted.map((n) => (
            <ActivityCard key={n.id} notification={n} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ActivityCard({ notification: n }: { notification: WorkspaceNotification }) {
  return (
    <li
      className={cn(
        "rounded-xl border border-[var(--border)] bg-[var(--panel)] px-3 py-2.5",
        !n.read && "border-l-2 border-l-[var(--c-accent)]",
      )}
    >
      <p className="text-sm font-medium text-foreground">{n.title}</p>
      {n.body ? (
        <p className="mt-0.5 text-sm text-[var(--fg-dim)]">{n.body}</p>
      ) : null}
      <p className="mt-1 text-[11px] text-[var(--fg-faint)]">
        {formatRelativeTime(n.created_at)}
      </p>
    </li>
  );
}

export function MobileWorkspace() {
  const [tab, setTab] = useState<TabId>("watch");
  const { proposals, refetch } = usePendingProposals();

  const activeLabel = TABS.find((t) => t.id === tab)?.label ?? "Watch";

  return (
    <div className="flex h-svh flex-col bg-background">
      <header className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--border)] px-3 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <PagesMenu hideLabel />
          <h1 className="truncate font-mono text-[13px] font-semibold tracking-tight text-foreground">
            {activeLabel}
          </h1>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <SettingsDialog collapsed />
          <ThemeSwitcher />
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-3 pt-3 pb-32">
        {tab === "watch" ? (
          <WatchTab />
        ) : tab === "portfolio" ? (
          <PortfolioTab />
        ) : tab === "agent" ? (
          <div className="flex h-full min-h-0 flex-col">
            <WorkspaceThread />
          </div>
        ) : (
          <ActivityTab proposals={proposals} refetchProposals={refetch} />
        )}
      </main>

      <nav className="pointer-events-none fixed inset-x-0 bottom-0 z-30 flex justify-center pb-[max(env(safe-area-inset-bottom),24px)]">
        <div className="pointer-events-auto flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--panel)]/90 px-1.5 py-1.5 shadow-[var(--panel-shadow)] backdrop-blur-md">
          {TABS.map((t) => {
            const Icon = t.icon;
            const isActive = tab === t.id;

            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={cn(
                  "relative flex flex-col items-center gap-0.5 rounded-full px-4 py-1.5 text-[10px] font-medium transition-colors",
                  isActive
                    ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
                    : "text-[var(--fg-faint)] hover:text-foreground",
                )}
                aria-current={isActive ? "page" : undefined}
              >
                <Icon className="size-4" />
                <span>{t.label}</span>
                {t.id === "activity" && proposals.length > 0 ? (
                  <span className="absolute -right-0.5 -top-0.5 flex min-h-4 min-w-4 items-center justify-center rounded-full bg-[var(--c-accent)] px-1 text-[9px] font-semibold leading-none text-[var(--c-on-accent)]">
                    {proposals.length}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
