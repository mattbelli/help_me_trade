export type AiProviderId =
  | "mock"
  | "openai"
  | "anthropic"
  | "deepseek"
  | "openrouter"
  | "ollama";

export type DefaultLlm = {
  provider: AiProviderId;
  model: string;
};

export type AiSettings = {
  provider: AiProviderId | string;
  default_llm: DefaultLlm;
  openai_api_key: string;
  openai_base_url: string;
  openai_model: string;
  anthropic_api_key: string;
  anthropic_base_url: string;
  anthropic_model: string;
  deepseek_api_key: string;
  deepseek_base_url: string;
  deepseek_model: string;
  openrouter_api_key: string;
  openrouter_base_url: string;
  openrouter_model: string;
  ollama_base_url: string;
  ollama_model: string;
  /** Provider id -> model ids allowed in chat picker; omit entry = all discovered models. */
  chat_enabled_models: Partial<Record<AiProviderId, string[]>>;
};

export type AiProviderMeta = {
  id: AiProviderId;
  label: string;
  requires_key: boolean;
  key_field?: string;
  base_url_field?: string;
  model_field?: string;
  default_base_url: string;
  default_model: string;
  key_placeholder?: string;
};

export const DEFAULT_AI_SETTINGS: AiSettings = {
  provider: "deepseek",
  default_llm: { provider: "deepseek", model: "" },
  openai_api_key: "",
  openai_base_url: "https://api.openai.com/v1",
  openai_model: "gpt-4o",
  anthropic_api_key: "",
  anthropic_base_url: "https://api.anthropic.com",
  anthropic_model: "claude-sonnet-4-20250514",
  deepseek_api_key: "",
  deepseek_base_url: "https://api.deepseek.com",
  deepseek_model: "deepseek-chat",
  openrouter_api_key: "",
  openrouter_base_url: "https://openrouter.ai/api/v1",
  openrouter_model: "openai/gpt-4o",
  ollama_base_url: "http://localhost:11434",
  ollama_model: "llama3.2",
  chat_enabled_models: {},
};

export const AI_PROVIDER_OPTIONS: {
  id: AiProviderId;
  label: string;
  description: string;
}[] = [
  { id: "openai", label: "OpenAI", description: "GPT models via api.openai.com" },
  { id: "anthropic", label: "Anthropic", description: "Claude via api.anthropic.com" },
  { id: "deepseek", label: "DeepSeek", description: "DeepSeek chat API" },
  { id: "openrouter", label: "OpenRouter", description: "Multi-model gateway" },
  { id: "ollama", label: "Ollama", description: "Local models on your machine" },
];

type ProviderFieldConfig = {
  keyField?: keyof AiSettings;
  baseUrlField: keyof AiSettings;
  modelField: keyof AiSettings;
  keyPlaceholder?: string;
  requiresKey: boolean;
};

export const PROVIDER_FIELDS: Record<AiProviderId, ProviderFieldConfig> = {
  mock: {
    baseUrlField: "ollama_base_url",
    modelField: "ollama_model",
    requiresKey: false,
  },
  openai: {
    keyField: "openai_api_key",
    baseUrlField: "openai_base_url",
    modelField: "openai_model",
    keyPlaceholder: "sk-…",
    requiresKey: true,
  },
  anthropic: {
    keyField: "anthropic_api_key",
    baseUrlField: "anthropic_base_url",
    modelField: "anthropic_model",
    keyPlaceholder: "sk-ant-…",
    requiresKey: true,
  },
  deepseek: {
    keyField: "deepseek_api_key",
    baseUrlField: "deepseek_base_url",
    modelField: "deepseek_model",
    keyPlaceholder: "sk-…",
    requiresKey: true,
  },
  openrouter: {
    keyField: "openrouter_api_key",
    baseUrlField: "openrouter_base_url",
    modelField: "openrouter_model",
    keyPlaceholder: "sk-or-…",
    requiresKey: true,
  },
  ollama: {
    baseUrlField: "ollama_base_url",
    modelField: "ollama_model",
    requiresKey: false,
  },
};

export function isProviderId(value: string): value is AiProviderId {
  return value in PROVIDER_FIELDS;
}

export function getDefaultLlm(ai: AiSettings): DefaultLlm {
  const block = ai.default_llm;
  if (block?.provider && isProviderId(block.provider) && block.provider !== "mock") {
    if (block.model) {
      return { provider: block.provider, model: block.model };
    }
    const fields = PROVIDER_FIELDS[block.provider];
    const model = String(ai[fields.modelField] || "").trim();
    if (model) {
      return { provider: block.provider, model };
    }
    return { provider: block.provider, model: "" };
  }
  const provider =
    isProviderId(String(ai.provider)) && ai.provider !== "mock"
      ? ai.provider
      : "deepseek";
  const fields = PROVIDER_FIELDS[provider];
  const model = String(ai[fields.modelField] || "").trim();
  return { provider, model };
}

export function setDefaultLlm(ai: AiSettings, next: DefaultLlm): AiSettings {
  const fields = PROVIDER_FIELDS[next.provider];
  return {
    ...ai,
    default_llm: next,
    provider: next.provider,
    [fields.modelField]: next.model,
  };
}

export function normalizeAiSettings(ai: Partial<AiSettings> | undefined): AiSettings {
  const merged = { ...DEFAULT_AI_SETTINGS, ...ai } as AiSettings;
  if (!merged.chat_enabled_models || typeof merged.chat_enabled_models !== "object") {
    merged.chat_enabled_models = {};
  }
  const resolved = getDefaultLlm(merged);
  if (resolved.model) {
    return setDefaultLlm(merged, resolved);
  }
  return {
    ...merged,
    provider: resolved.provider,
    default_llm: resolved,
  };
}
