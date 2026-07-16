"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Download, Trash2 } from "lucide-react";

import { downloadResearchMarkdown } from "@/components/research/research-download";
import {
  SettingsSection,
  TerminalButton,
  TerminalField,
  TerminalInput,
  TerminalSelect,
} from "@/components/ui/terminal";
import { API_BASE, apiFetch, type ResearchSettings } from "@/lib/api";
import { getDefaultLlm } from "@/lib/ai-settings";
import type { AiSettings } from "@/lib/ai-settings";
import {
  deleteResearchArtifact,
  EFFORT_LABELS,
  getResearchArtifact,
  listResearchArtifacts,
  type ResearchArtifact,
  type ResearchDepth,
} from "@/lib/research";

export const DEFAULT_RESEARCH: ResearchSettings = {
  searxng_url: "",
  model_roles: {},
  strong_model: "",
  fast_model: "",
  auto_delegate: true,
  effort: "standard",
};

const ROLES = [
  "planner",
  "specialists",
  "extraction",
  "critic",
  "editor",
] as const;

const EFFORT_PRESETS = ["quick", "standard", "deep", "exhaustive"] as const;

type Role = (typeof ROLES)[number];

type ResearchConfigMeta = {
  known_models: string[];
  default_model: string;
};

type Props = {
  value: ResearchSettings | undefined;
  ai: AiSettings;
  onChange: (research: ResearchSettings) => void;
};

function normalizeResearch(value: ResearchSettings | undefined): ResearchSettings {
  const base = value ?? DEFAULT_RESEARCH;
  const modelRoles = { ...DEFAULT_RESEARCH.model_roles, ...base.model_roles };
  return {
    ...DEFAULT_RESEARCH,
    ...base,
    model_roles: modelRoles,
    auto_delegate: base.auto_delegate ?? DEFAULT_RESEARCH.auto_delegate,
    effort: base.effort ?? DEFAULT_RESEARCH.effort,
  };
}

function formatArchiveDate(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return iso;
  return new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function statusClass(status: string): string {
  switch (status) {
    case "completed":
      return "text-[var(--c-accent)]";
    case "failed":
      return "text-[var(--c-red)]";
    case "running":
    case "queued":
      return "text-[var(--c-blue)]";
    default:
      return "text-[var(--fg-dim)]";
  }
}

export function SettingsResearchPanel({ value, ai, onChange }: Props) {
  const research = normalizeResearch(value);
  const defaultModel = getDefaultLlm(ai).model;
  const [meta, setMeta] = useState<ResearchConfigMeta>({
    known_models: defaultModel ? [defaultModel] : [],
    default_model: defaultModel,
  });
  const [archive, setArchive] = useState<ResearchArtifact[]>([]);
  const [archiveLoading, setArchiveLoading] = useState(true);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [archiveBusyId, setArchiveBusyId] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch(`${API_BASE}/api/ai/research-config`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        const known = Array.isArray(data.known_models)
          ? (data.known_models as string[])
          : [];
        setMeta({
          known_models: known.length ? known : defaultModel ? [defaultModel] : [],
          default_model: String(data.default_model ?? defaultModel),
        });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [defaultModel]);

  const refreshArchive = useCallback(async () => {
    try {
      const items = await listResearchArtifacts(100);
      setArchive(items);
      setArchiveError(null);
    } catch (err) {
      setArchiveError(err instanceof Error ? err.message : "Failed to load archive");
    } finally {
      setArchiveLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshArchive();
  }, [refreshArchive]);

  async function handleArchiveDownload(item: ResearchArtifact) {
    setArchiveBusyId(item.id);
    setArchiveError(null);
    try {
      const full =
        item.markdown.trim().length > 0
          ? item
          : await getResearchArtifact(item.id);
      if (!full.markdown.trim()) {
        throw new Error("No markdown available for this report");
      }
      downloadResearchMarkdown(full);
    } catch (err) {
      setArchiveError(err instanceof Error ? err.message : "Download failed");
    } finally {
      setArchiveBusyId(null);
    }
  }

  async function handleArchiveDelete(item: ResearchArtifact) {
    if (
      !window.confirm(
        `Delete research "${item.topic}"? This cannot be undone.`,
      )
    ) {
      return;
    }
    setArchiveBusyId(item.id);
    setArchiveError(null);
    try {
      await deleteResearchArtifact(item.id);
      setArchive((prev) => prev.filter((row) => row.id !== item.id));
    } catch (err) {
      setArchiveError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setArchiveBusyId(null);
    }
  }

  const modelSuggestions = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const model of [
      meta.default_model,
      defaultModel,
      ...meta.known_models,
      research.strong_model,
      research.fast_model,
      ...Object.values(research.model_roles ?? {}),
    ]) {
      const trimmed = model?.trim();
      if (!trimmed || seen.has(trimmed)) continue;
      seen.add(trimmed);
      out.push(trimmed);
    }
    return out;
  }, [defaultModel, meta, research]);

  function patch(partial: Partial<ResearchSettings>) {
    onChange({ ...research, ...partial });
  }

  function patchRole(role: Role, model: string) {
    patch({
      model_roles: {
        ...research.model_roles,
        [role]: model,
      },
    });
  }

  return (
    <div className="space-y-5">
      <SettingsSection title="Web search">
        <TerminalField label="SearXNG URL (optional)" htmlFor="searxng-url">
          <TerminalInput
            id="searxng-url"
            value={research.searxng_url}
            placeholder="http://localhost:8080"
            onChange={(e) => patch({ searxng_url: e.target.value.trim() })}
          />
        </TerminalField>
        <p className="font-mono text-[10px] text-[var(--fg-dim)]">
          When set, deep research uses live web search instead of mock snippets. You
          can also set <code className="text-[10px]">SEARXNG_URL</code> in{" "}
          <code className="text-[10px]">.env</code>. Env wins if both are set.
        </p>
      </SettingsSection>

      <SettingsSection title="Engine v2 (config only)">
        <p className="font-mono text-[10px] leading-snug text-[var(--fg-dim)]">
          Model roles and effort presets for the upcoming specialist engine. Provider:{" "}
          <span className="text-foreground">{ai.provider || "—"}</span>, default model:{" "}
          <span className="text-foreground">{defaultModel || "(none)"}</span>.
        </p>

        <TerminalField label="Default effort preset" htmlFor="research-effort">
          <TerminalSelect
            id="research-effort"
            value={research.effort ?? "standard"}
            onChange={(e) => patch({ effort: e.target.value })}
          >
            {EFFORT_PRESETS.map((name) => (
              <option key={name} value={name}>
                {EFFORT_LABELS[name as ResearchDepth] ?? name}
              </option>
            ))}
          </TerminalSelect>
        </TerminalField>

        <div className="space-y-2">
          <p className="font-mono text-[11px] tracking-[0.06em] uppercase text-foreground">
            Role models
          </p>
          <p className="font-mono text-[10px] text-[var(--fg-dim)]">
            Assign a model to each research role, or leave it on “app default”.
          </p>
          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full min-w-[28rem] border-collapse font-mono text-[11px]">
              <thead>
                <tr className="border-b border-[var(--line-divider)] bg-[var(--bg-inset)] text-left text-[var(--fg-dim)]">
                  <th className="px-3 py-2 font-normal">Role</th>
                  <th className="px-3 py-2 font-normal">Model</th>
                </tr>
              </thead>
              <tbody>
                {ROLES.map((role) => (
                  <tr key={role} className="border-b border-[var(--line-divider)] last:border-b-0">
                    <td className="px-3 py-2 align-top text-[var(--fg-dim)]">{role}</td>
                    <td className="px-3 py-2">
                      <TerminalSelect
                        value={research.model_roles?.[role] ?? ""}
                        onChange={(e) => patchRole(role, e.target.value)}
                        className="w-full"
                      >
                        <option value="">{`(app default${defaultModel ? `: ${defaultModel}` : ""})`}</option>
                        {modelSuggestions.map((model) => (
                          <option key={model} value={model}>
                            {model}
                          </option>
                        ))}
                      </TerminalSelect>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </SettingsSection>

      <SettingsSection title="Archive">
        <p className="font-mono text-[10px] text-[var(--fg-dim)]">
          All saved research reports. Download markdown or remove artifacts you no
          longer need.
        </p>
        {archiveError ? (
          <p className="font-mono text-[10px] text-[var(--c-red)]">{archiveError}</p>
        ) : null}
        <div className="overflow-x-auto rounded border border-border">
          <table className="w-full min-w-[36rem] border-collapse font-mono text-[11px]">
            <thead>
              <tr className="border-b border-border bg-[var(--bg-inset)] text-left text-[var(--fg-dim)]">
                <th className="px-3 py-2 font-normal">Topic</th>
                <th className="px-3 py-2 font-normal">Symbol</th>
                <th className="px-3 py-2 font-normal">Date</th>
                <th className="px-3 py-2 font-normal">Status</th>
                <th className="px-3 py-2 font-normal text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {archiveLoading ? (
                <tr>
                  <td
                    colSpan={5}
                    className="px-3 py-4 text-center text-[var(--fg-dim)]"
                  >
                    Loading…
                  </td>
                </tr>
              ) : archive.length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    className="px-3 py-4 text-center text-[var(--fg-dim)]"
                  >
                    No research artifacts yet.
                  </td>
                </tr>
              ) : (
                archive.map((item) => (
                  <tr key={item.id} className="border-b border-[var(--line-divider)] last:border-b-0">
                    <td className="max-w-[14rem] truncate px-3 py-2 align-top text-foreground">
                      {item.topic}
                    </td>
                    <td className="px-3 py-2 align-top text-[var(--fg-dim)]">
                      {item.symbol ?? "—"}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 align-top text-[var(--fg-dim)]">
                      {formatArchiveDate(item.created_at)}
                    </td>
                    <td
                      className={`px-3 py-2 align-top uppercase ${statusClass(item.status)}`}
                    >
                      {item.status}
                    </td>
                    <td className="px-3 py-2 align-top">
                      <div className="flex items-center justify-end gap-1">
                        <TerminalButton
                          variant="default"
                          disabled={archiveBusyId === item.id}
                          onClick={() => void handleArchiveDownload(item)}
                          aria-label={`Download ${item.topic}`}
                        >
                          <Download className="size-3" />
                          .md
                        </TerminalButton>
                        <TerminalButton
                          variant="danger"
                          disabled={archiveBusyId === item.id}
                          onClick={() => void handleArchiveDelete(item)}
                          aria-label={`Delete ${item.topic}`}
                        >
                          <Trash2 className="size-3" />
                          Delete
                        </TerminalButton>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </SettingsSection>
    </div>
  );
}
