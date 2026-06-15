export type AiProviderId =
  | "mock"
  | "openai"
  | "anthropic"
  | "deepseek"
  | "openrouter"
  | "ollama";

export type AiSettings = {
  provider: AiProviderId | string;
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
  provider: "mock",
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
};

export const AI_PROVIDER_OPTIONS: {
  id: AiProviderId;
  label: string;
  description: string;
}[] = [
  { id: "mock", label: "Mock (offline)", description: "Deterministic responses for dev/test" },
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

export function normalizeAiSettings(ai: Partial<AiSettings> | undefined): AiSettings {
  return { ...DEFAULT_AI_SETTINGS, ...ai };
}
