"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/workspace/primitives/data-table";
import { Tag } from "@/components/workspace/primitives/tag";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  getFusionEpisode,
  getFusionEpisodeDossier,
  getFusionEpisodes,
  type EvidenceDot,
  type FusionDossier,
  type FusionEpisode,
  type FusionEpisodesResponse,
} from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import { usePagePoll } from "@/lib/workspace/use-page-poll";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;

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

const DOT_COLUMNS: DataTableColumn<EvidenceDot>[] = [
  {
    key: "ts",
    header: "Time ET",
    className: "whitespace-nowrap tabular-nums text-[var(--fg-faint)]",
    render: (dot) => formatEtTime(dot.ts),
  },
  {
    key: "component",
    header: "Component",
    className: "text-[var(--fg-dim)]",
    render: (dot) => words(dot.component),
  },
  {
    key: "actor_group",
    header: "Group",
    className: "text-[var(--fg-dim)]",
    render: (dot) => words(dot.actor_group),
  },
  {
    key: "direction",
    header: "Direction",
    className: (dot) => directionClass(dot.direction),
    render: (dot) => dot.direction,
  },
  {
    key: "raw",
    header: "Raw",
    align: "right",
    className: "tabular-nums text-[var(--fg-dim)]",
    render: formatRawValue,
  },
  {
    key: "points",
    header: "Points",
    align: "right",
    className: "tabular-nums text-[var(--fg-dim)]",
    render: (dot) => (
      <>
        {dot.points >= 0 ? "+" : ""}
        {dot.points.toFixed(1)}
      </>
    ),
  },
];

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
    <DataTable
      className="text-[length:var(--fs-micro)]"
      columns={DOT_COLUMNS}
      rows={dots}
      rowKey={(dot) => dot.id}
    />
  );
}

const CLAIM_COLUMNS: DataTableColumn<FusionDossier["claims"][number]>[] = [
  {
    key: "angle",
    header: "Angle",
    className: "whitespace-nowrap align-top text-[var(--fg-faint)]",
    render: (claim) => words(claim.angle),
  },
  {
    key: "claim",
    header: "Claim",
    className: "text-[var(--fg-dim)]",
    render: (claim) => (
      <>
        {claim.statement}
        {!claim.fresh ? <Tag tone="amber"> stale</Tag> : null}
        {claim.contradicts.length > 0 ? (
          <span className="text-[var(--c-amber)]">
            {" "}contradicts {claim.contradicts.join(", ")}
          </span>
        ) : null}
      </>
    ),
  },
  {
    key: "direction",
    header: "Direction",
    className: (claim) => cn("align-top", directionClass(claim.direction)),
    render: (claim) => claim.direction,
  },
];

function DossierView({
  dossier,
  error,
}: {
  dossier: FusionDossier | undefined;
  error: string | undefined;
}) {
  if (error) {
    return <div className="px-2 py-2 text-[length:var(--fs-micro)] text-[var(--c-red)]">{error}</div>;
  }
  if (!dossier) {
    return <div className="px-2 py-2 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">building dossier…</div>;
  }
  const contradictions =
    dossier.claims.reduce((total, claim) => total + claim.contradicts.length, 0) / 2;
  return (
    <DataTable
      className="text-[length:var(--fs-micro)]"
      columns={CLAIM_COLUMNS}
      rows={dossier.claims}
      rowKey={(claim) => claim.claim_id}
      footer={
        <>
          {dossier.claims.length} claims · {contradictions} contradiction
          {contradictions === 1 ? "" : "s"} · deterministic perception — angles
          stated, never a verdict or a win%
        </>
      }
    />
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
  const [expandedView, setExpandedView] = useState<"dots" | "dossier">("dots");
  const [dotsByEpisode, setDotsByEpisode] = useState<Record<string, EvidenceDot[]>>({});
  const [dotErrors, setDotErrors] = useState<Record<string, string>>({});
  const [dossierByEpisode, setDossierByEpisode] = useState<Record<string, FusionDossier>>({});
  const [dossierErrors, setDossierErrors] = useState<Record<string, string>>({});
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

  const openDossier = useCallback(
    async (id: string) => {
      setExpandedView("dossier");
      if (dossierByEpisode[id]) return;
      try {
        const dossier = await getFusionEpisodeDossier(id);
        if (cancelledRef.current) return;
        setDossierByEpisode((current) => ({ ...current, [id]: dossier }));
        setDossierErrors((current) => {
          const next = { ...current };
          delete next[id];
          return next;
        });
      } catch (err) {
        if (cancelledRef.current) return;
        setDossierErrors((current) => ({
          ...current,
          [id]: err instanceof Error ? err.message : "Failed to build dossier",
        }));
      }
    },
    [dossierByEpisode],
  );

  const toggleEpisode = useCallback(
    async (id: string) => {
      if (expandedId === id) {
        setExpandedId(null);
        return;
      }
      setExpandedId(id);
      setExpandedView("dots");
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

  const columns: DataTableColumn<FusionEpisode>[] = [
    {
      key: "symbol",
      header: "Symbol",
      className: "font-medium text-foreground",
      render: (episode) => (
        <ClickableSymbol
          symbol={episode.symbol}
          linked={linked}
          onActivate={() => publish(episode.symbol)}
        />
      ),
    },
    {
      key: "score",
      header: "Score",
      align: "right",
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (episode) => episode.score.toFixed(1),
    },
    {
      key: "direction",
      header: "Direction",
      className: (episode) => directionClass(episodeDirection(episode)),
      render: (episode) => episodeDirection(episode),
    },
    {
      key: "signature",
      header: "Signature",
      className: "text-[var(--fg-faint)]",
      render: (episode) => formatSignature(episode.signature) || "—",
    },
    {
      key: "shock",
      header: "Shock",
      className: "text-[var(--fg-faint)]",
      render: (episode) => (episode.market_shock ? "shock" : ""),
    },
    {
      key: "dots",
      header: "Dots",
      align: "right",
      className: "tabular-nums text-[var(--fg-faint)]",
      render: (episode) =>
        episode.dot_count ?? dotsByEpisode[episode.id]?.length ?? "—",
    },
  ];

  return (
    <div className="h-full overflow-auto scroll-shadow-y px-1.5 py-1">
      <DataTable
        columns={columns}
        rows={episodes}
        rowKey={(episode) => episode.id}
        rowClassName="cursor-pointer"
        onRowClick={(episode) => void toggleEpisode(episode.id)}
        expandedClassName="p-1"
        renderExpanded={(episode) =>
          expandedId === episode.id ? (
            <>
              <div className="flex gap-3 px-2 pb-1 pt-0.5 text-[length:var(--fs-micro)]">
                <button
                  type="button"
                  onClick={() => setExpandedView("dots")}
                  className={cn(
                    "lowercase",
                    expandedView === "dots"
                      ? "text-foreground"
                      : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
                  )}
                >
                  dots
                </button>
                <button
                  type="button"
                  onClick={() => void openDossier(episode.id)}
                  className={cn(
                    "lowercase",
                    expandedView === "dossier"
                      ? "text-foreground"
                      : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
                  )}
                >
                  dossier
                </button>
              </div>
              {expandedView === "dossier" ? (
                <DossierView
                  dossier={dossierByEpisode[episode.id]}
                  error={dossierErrors[episode.id]}
                />
              ) : (
                <DotTimeline
                  dots={dotsByEpisode[episode.id]}
                  error={dotErrors[episode.id]}
                />
              )}
            </>
          ) : null
        }
        footer={
          <>
            session {data.day ?? "unknown"} — observe-only — mechanical stack
            scores, not validated signals
          </>
        }
      />
    </div>
  );
}
