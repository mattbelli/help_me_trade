import { API_BASE, apiFetch } from "./api";

export type ChatModelOption = {
  id: string;
  label: string;
};

export type ChatModelCatalog = {
  provider: string;
  default_model: string;
  models: ChatModelOption[];
  supports_custom: boolean;
  source?: "live" | "unavailable";
  catalog_models?: ChatModelOption[];
  picker_enabled_ids?: string[] | null;
};

const CUSTOM_MODEL_VALUE = "__custom__";

export { CUSTOM_MODEL_VALUE };

export function chatModelStorageKey(provider: string): string {
  return `hmt-chat-model:${provider}`;
}

export function readStoredChatModel(provider: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    const value = localStorage.getItem(chatModelStorageKey(provider));
    return value?.trim() || null;
  } catch {
    return null;
  }
}

export function writeStoredChatModel(provider: string, model: string): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(chatModelStorageKey(provider), model);
  } catch {
    // ignore quota errors
  }
}

export async function fetchChatModels(
  provider?: string,
  options?: { catalog?: boolean },
): Promise<ChatModelCatalog> {
  const params = new URLSearchParams();
  if (provider) params.set("provider", provider);
  if (options?.catalog) params.set("catalog", "true");
  const qs = params.toString();
  const res = await apiFetch(
    `${API_BASE}/api/ai/models${qs ? `?${qs}` : ""}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`Failed to load chat models (${res.status})`);
  }
  return res.json() as Promise<ChatModelCatalog>;
}

export function resolveInitialChatModel(
  catalog: ChatModelCatalog,
  stored: string | null,
): { model: string; isCustom: boolean } {
  const fallback = catalog.default_model || catalog.models[0]?.id || "";
  const candidate = stored?.trim() || fallback;
  const known = catalog.models.some((m) => m.id === candidate);
  if (known) {
    return { model: candidate, isCustom: false };
  }
  if (catalog.supports_custom && candidate) {
    return { model: candidate, isCustom: true };
  }
  return { model: fallback, isCustom: false };
}

export async function fetchDefaultModelOptions(): Promise<{
  default_llm: { provider: string; model: string };
  options: Array<{
    provider: string;
    model: string;
    label: string;
    provider_label: string;
  }>;
}> {
  const res = await apiFetch(`${API_BASE}/api/ai/models/options`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Failed to load model options (${res.status})`);
  }
  return res.json();
}

export function defaultLlmKey(provider: string, model: string): string {
  return `${provider}::${model}`;
}

export function parseDefaultLlmKey(value: string): { provider: string; model: string } | null {
  const idx = value.indexOf("::");
  if (idx <= 0) return null;
  return {
    provider: value.slice(0, idx),
    model: value.slice(idx + 2),
  };
}

export function shortModelLabel(model: string): string {
  const slash = model.lastIndexOf("/");
  return slash >= 0 ? model.slice(slash + 1) : model;
}
