"use client";

import { useEffect, useMemo, useState } from "react";

import {
  SettingsSection,
  TerminalField,
  TerminalInput,
  TerminalSelect,
} from "@/components/ui/terminal";
import { API_BASE, apiFetch, type ResearchSettings } from "@/lib/api";
import { getDefaultLlm } from "@/lib/ai-settings";
import type { AiSettings } from "@/lib/ai-settings";

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

export function SettingsResearchPanel({ value, ai, onChange }: Props) {
  const research = normalizeResearch(value);
  const defaultModel = getDefaultLlm(ai).model;
  const [meta, setMeta] = useState<ResearchConfigMeta>({
    known_models: defaultModel ? [defaultModel] : [],
    default_model: defaultModel,
  });

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
                {name}
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
                <tr className="border-b border-border bg-[var(--bg-inset)] text-left text-[var(--fg-dim)]">
                  <th className="px-3 py-2 font-normal">Role</th>
                  <th className="px-3 py-2 font-normal">Model</th>
                </tr>
              </thead>
              <tbody>
                {ROLES.map((role) => (
                  <tr key={role} className="border-b border-border last:border-b-0">
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
    </div>
  );
}
