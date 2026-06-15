"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { useSnapshot } from "@/components/providers/snapshot-provider";
import { API_BASE } from "@/lib/api";

type Props = {
  title: string;
  subtitle?: string;
};

export function PageHeader({ title, subtitle }: Props) {
  const { snapshot, brokerInfo } = useSnapshot();
  const [pollSeconds, setPollSeconds] = useState(20);

  useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then((res) => res.json())
      .then((data: { poll_interval_seconds?: number }) => {
        if (data.poll_interval_seconds) {
          setPollSeconds(data.poll_interval_seconds);
        }
      })
      .catch(() => {});
  }, []);

  return (
    <header className="mb-8">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-2xl font-medium tracking-tight text-foreground">
          {title}
        </h1>
        {brokerInfo && (
          <>
            {brokerInfo.brokers
              ? Object.entries(brokerInfo.brokers)
                  .filter(([, b]) => b.enabled)
                  .map(([name]) => (
                    <Badge
                      key={name}
                      variant="outline"
                      className="border-accent/30 bg-accent/10 text-accent-foreground uppercase"
                    >
                      {name}
                    </Badge>
                  ))
              : (
                  <Badge
                    variant="outline"
                    className="border-accent/30 bg-accent/10 text-accent-foreground uppercase"
                  >
                    {brokerInfo.broker}
                  </Badge>
                )}
            {brokerInfo.primary_broker &&
              brokerInfo.primary_broker !== brokerInfo.broker && (
                <span className="text-xs text-muted-foreground">
                  charts: {brokerInfo.primary_broker}
                </span>
              )}
          </>
        )}
      </div>
      <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
        {subtitle ??
          (snapshot
            ? `Last updated ${new Date(snapshot.captured_at).toLocaleString()} · API polls every ${pollSeconds}s`
            : "Connect to the FastAPI backend on port 8000")}
      </p>
      {brokerInfo?.brokers?.schwab?.auth && (
        <p className="mt-1 text-xs text-muted-foreground">
          {brokerInfo.brokers.schwab.auth.authenticated
            ? brokerInfo.brokers.schwab.auth.estimated_days_until_reauth != null
              ? `Schwab session ~${brokerInfo.brokers.schwab.auth.estimated_days_until_reauth}d until re-login`
              : brokerInfo.brokers.schwab.auth.message
            : brokerInfo.brokers.schwab.auth.message}
        </p>
      )}
    </header>
  );
}
