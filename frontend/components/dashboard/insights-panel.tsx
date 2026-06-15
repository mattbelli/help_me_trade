"use client";

import { useEffect, useRef, useState } from "react";
import { RefreshCw, Sparkles } from "lucide-react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Button } from "@/components/ui/button";
import { API_BASE } from "@/lib/api";
import { cn } from "@/lib/utils";

async function consumeInsightsStream(
  signal: AbortSignal,
  onToken: (chunk: string) => void,
  onStatus: (provider: string) => void,
): Promise<string> {
  const res = await fetch(`${API_BASE}/api/insights/stream`, { signal });
  if (!res.ok) {
    throw new Error(
      res.status === 503
        ? "No portfolio snapshot yet — waiting for broker poll"
        : `Insights unavailable (${res.status})`,
    );
  }
  if (!res.body) {
    throw new Error("Insights stream unavailable");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalText = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      const lines = chunk.split("\n");
      let event = "";
      let dataLine = "";
      for (const line of lines) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) dataLine = line.slice(5).trim();
      }
      if (!dataLine) continue;
      const data = JSON.parse(dataLine) as { text?: string; provider?: string; error?: string };
      if (event === "status" && data.provider) {
        onStatus(data.provider);
      }
      if (event === "token" && data.text) {
        onToken(data.text);
      }
      if (event === "done" && data.text) {
        finalText = data.text;
      }
      if (event === "error") {
        throw new Error(data.error ?? "Insight stream failed");
      }
    }
  }

  return finalText;
}

export function InsightsPanel() {
  const { snapshot } = useSnapshot();
  const [text, setText] = useState("");
  const [provider, setProvider] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  const snapshotAt = snapshot?.captured_at ?? null;

  useEffect(() => {
    if (!snapshotAt) {
      setText("");
      setError("Waiting for portfolio snapshot…");
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    let streamed = "";
    setLoading(true);
    setError(null);
    setText("");

    void consumeInsightsStream(
      controller.signal,
      (chunk) => {
        streamed += chunk;
        setText(streamed);
      },
      (activeProvider) => setProvider(activeProvider),
    )
      .then((finalText) => {
        if (finalText) setText(finalText);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "Failed to load insights");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [snapshotAt, refreshKey]);

  function handleRefresh() {
    setRefreshKey((n) => n + 1);
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-border bg-background/60">
      <div className="flex items-start justify-between gap-2 border-b border-border px-3 py-2.5">
        <div>
          <div className="flex items-center gap-1.5">
            <Sparkles className="size-3.5 text-accent" />
            <p className="text-sm font-medium">Portfolio insight</p>
          </div>
          <p className="text-[11px] text-muted-foreground">
            {provider ? `${provider} · ` : ""}
            {snapshotAt
              ? `Updated ${new Date(snapshotAt).toLocaleTimeString()}`
              : "Waiting for data"}
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={handleRefresh}
          disabled={loading || !snapshotAt}
          aria-label="Refresh insight"
        >
          <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
        </Button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-3">
        {error && !loading && !text ? (
          <p className="text-sm text-muted-foreground">{error}</p>
        ) : loading && !text ? (
          <Shimmer className="text-sm">Analyzing portfolio…</Shimmer>
        ) : (
          <p className="text-sm leading-relaxed text-foreground/90">{text}</p>
        )}
        {error && text ? (
          <p className="mt-2 text-xs text-destructive">{error}</p>
        ) : null}
      </div>
    </div>
  );
}
