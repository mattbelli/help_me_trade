"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  getFusionEpisode,
  getFusionEpisodes,
  type EvidenceDot,
  type FusionEpisode,
  type FusionEpisodesResponse,
} from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import { usePagePoll } from "@/lib/workspace/use-page-poll";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;
const TH =
  "border-b border-[var(--line-divider)] px-2 py-1.5 text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-faint)] uppercase";
const TD = "border-b border-[var(--line-divider)] px-2 py-1.5";
const ROW = "cursor-pointer transition-colors hover:bg-[var(--bg-hover)]";

type EvidenceStackConfig = {
  evidenceMinScore?: number;
  evidenceDirection?: string;
};

function episodeDirection(episode: FusionEpisode): "bull" | "bear" | "contested" {
  if (episode.contested || episode.direction_state === "contested") return "contested";
  if (episode.direction_state.includes("bear")) return "bear";
  if (episode.direction_state.includes("bull")) return "bull";
  return episode.anchor_direction === "bear" ? "bear" : "bull";
}

function directionClass(direction: string): string {
  if (direction === "bull") return "text-[var(--c-green)]";
  if (direction === "bear") return "text-[var(--c-red)]";
  if (direction === "contested") return "text-[var(--c-amber)]";
  return "text-[var(--fg-faint)]";
}

function words(value: string): string {
  return value.replaceAll("_", " ").toLowerCase();
}

function formatSignature(signature: string): string {
  return signature
    .split(",")
    .map((component) => words(component.trim()))
    .filter(Boolean)
    .join(", ");
}

function formatEtTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString("en-GB", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatRawValue(dot: EvidenceDot): string {
  if (dot.raw_value == null || dot.raw_value === "") return "—";
  const value = Number(dot.raw_value);
  if (!Number.isFinite(value)) return dot.raw_value;
  if (dot.actor_group === "options_actor" || dot.actor_group === "equity_actor") {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(value);
  }
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(value);
}

function DotTimeline({
  dots,
  error,
}: {
  dots: EvidenceDot[] | undefined;
  error: string | undefined;
}) {
  if (error) {
    return <div className="px-2 py-2 text-[length:var(--fs-micro)] text-[var(--c-red)]">{error}</div>;
  }
  if (!dots) {
    return <div className="px-2 py-2 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">loading dots…</div>;
  }
  return (
    <table className="w-full border-collapse font-mono text-[length:var(--fs-micro)]">
      <thead>
        <tr>
          <th className={cn(TH, "text-left")}>Time ET</th>
          <th className={cn(TH, "text-left")}>Component</th>
          <th className={cn(TH, "text-left")}>Group</th>
          <th className={cn(TH, "text-left")}>Direction</th>
          <th className={cn(TH, "text-right")}>Raw</th>
          <th className={cn(TH, "text-right")}>Points</th>
        </tr>
      </thead>
      <tbody className="leading-[20px]">
        {dots.map((dot) => (
          <tr key={dot.id}>
            <td className={cn(TD, "whitespace-nowrap tabular-nums text-[var(--fg-faint)]")}>
              {formatEtTime(dot.ts)}
            </td>
            <td className={cn(TD, "text-[var(--fg-dim)]")}>{words(dot.component)}</td>
            <td className={cn(TD, "text-[var(--fg-dim)]")}>{words(dot.actor_group)}</td>
            <td className={cn(TD, directionClass(dot.direction))}>{dot.direction}</td>
            <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
              {formatRawValue(dot)}
            </td>
            <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
              {dot.points >= 0 ? "+" : ""}
              {dot.points.toFixed(1)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function EvidenceStackWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const stackConfig = config as typeof config & EvidenceStackConfig;
  const minScore = stackConfig.evidenceMinScore ?? 0;
  const directionFilter = stackConfig.evidenceDirection ?? "all";
  const [data, setData] = useState<FusionEpisodesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [dotsByEpisode, setDotsByEpisode] = useState<Record<string, EvidenceDot[]>>({});
  const [dotErrors, setDotErrors] = useState<Record<string, string>>({});
  const cancelledRef = useRef(false);

  const load = useCallback(async (isInitial: boolean) => {
    if (isInitial) setLoading(true);
    try {
      const result = await getFusionEpisodes();
      if (cancelledRef.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to load evidence stacks");
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, [load]);

  usePagePoll(load, POLL_MS);

  const toggleEpisode = useCallback(
    async (id: string) => {
      if (expandedId === id) {
        setExpandedId(null);
        return;
      }
      setExpandedId(id);
      if (dotsByEpisode[id]) return;
      try {
        const result = await getFusionEpisode(id);
        if (cancelledRef.current) return;
        setDotsByEpisode((current) => ({ ...current, [id]: result.dots }));
        setDotErrors((current) => {
          const next = { ...current };
          delete next[id];
          return next;
        });
      } catch (err) {
        if (cancelledRef.current) return;
        setDotErrors((current) => ({
          ...current,
          [id]: err instanceof Error ? err.message : "Failed to load evidence dots",
        }));
      }
    },
    [dotsByEpisode, expandedId],
  );

  const episodes = useMemo(
    () =>
      (data?.episodes ?? []).filter((episode) => {
        const direction = episodeDirection(episode);
        return (
          episode.score >= minScore &&
          (directionFilter === "all" || direction === directionFilter)
        );
      }),
    [data, directionFilter, minScore],
  );

  if (loading && !data) return <WidgetLoading cols={[1, 1, 1, 3, 1]} />;
  if (error && !data) {
    return <WidgetError message={error} onRetry={() => void load(false)} />;
  }
  if (!data || data.episodes.length === 0) {
    return <WidgetEmpty message="no evidence stacks yet — collector runs market hours" />;
  }
  if (episodes.length === 0) {
    return (
      <WidgetEmpty
        message={`no stacks match filters — session ${data.day ?? "unknown"}`}
      />
    );
  }

  return (
    <div className="h-full overflow-auto scroll-shadow-y px-1.5 py-1">
      <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
        <thead>
          <tr>
            <th className={cn(TH, "text-left")}>Symbol</th>
            <th className={cn(TH, "text-right")}>Score</th>
            <th className={cn(TH, "text-left")}>Direction</th>
            <th className={cn(TH, "text-left")}>Signature</th>
            <th className={cn(TH, "text-left")}>Shock</th>
            <th className={cn(TH, "text-right")}>Dots</th>
          </tr>
        </thead>
        <tbody className="leading-[20px]">
          {episodes.map((episode) => {
            const expanded = expandedId === episode.id;
            const direction = episodeDirection(episode);
            return (
              <Fragment key={episode.id}>
                <tr
                  className={ROW}
                  onClick={() => void toggleEpisode(episode.id)}
                  aria-expanded={expanded}
                >
                  <td className={cn(TD, "font-medium text-foreground")}>
                    <ClickableSymbol
                      symbol={episode.symbol}
                      linked={linked}
                      onActivate={() => publish(episode.symbol)}
                    />
                  </td>
                  <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                    {episode.score.toFixed(1)}
                  </td>
                  <td className={cn(TD, directionClass(direction))}>{direction}</td>
                  <td className={cn(TD, "text-[var(--fg-faint)]")}>
                    {formatSignature(episode.signature) || "—"}
                  </td>
                  <td className={cn(TD, "text-[var(--fg-faint)]")}>
                    {episode.market_shock ? "shock" : ""}
                  </td>
                  <td className={cn(TD, "text-right tabular-nums text-[var(--fg-faint)]")}>
                    {episode.dot_count ?? dotsByEpisode[episode.id]?.length ?? "—"}
                  </td>
                </tr>
                {expanded ? (
                  <tr>
                    <td
                      colSpan={6}
                      className="border-b border-[var(--line-divider)] bg-[var(--bg-hover)] p-1"
                    >
                      <DotTimeline
                        dots={dotsByEpisode[episode.id]}
                        error={dotErrors[episode.id]}
                      />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
          <tr>
            <td
              colSpan={6}
              className="px-2 py-1.5 text-[length:var(--fs-micro)] text-[var(--fg-faint)]"
            >
              session {data.day ?? "unknown"} — observe-only — mechanical stack
              scores, not validated signals
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
