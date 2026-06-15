"""Curated chat model presets per LLM provider."""

from __future__ import annotations

CHAT_MODEL_PRESETS: dict[str, list[dict[str, str]]] = {
    "mock": [
        {"id": "mock", "label": "Mock (offline)"},
    ],
    "openai": [
        {"id": "gpt-4o", "label": "GPT-4o"},
        {"id": "gpt-4o-mini", "label": "GPT-4o Mini"},
        {"id": "gpt-4.1", "label": "GPT-4.1"},
        {"id": "gpt-4.1-mini", "label": "GPT-4.1 Mini"},
        {"id": "o3-mini", "label": "o3-mini"},
    ],
    "anthropic": [
        {"id": "claude-sonnet-4-20250514", "label": "Claude Sonnet 4"},
        {"id": "claude-3-5-sonnet-20241022", "label": "Claude 3.5 Sonnet"},
        {"id": "claude-3-5-haiku-20241022", "label": "Claude 3.5 Haiku"},
        {"id": "claude-opus-4-20250514", "label": "Claude Opus 4"},
    ],
    "deepseek": [
        {"id": "deepseek-chat", "label": "DeepSeek Chat"},
        {"id": "deepseek-reasoner", "label": "DeepSeek Reasoner"},
    ],
    "openrouter": [
        {"id": "openai/gpt-4o", "label": "OpenAI GPT-4o"},
        {"id": "openai/gpt-4o-mini", "label": "OpenAI GPT-4o Mini"},
        {"id": "anthropic/claude-sonnet-4", "label": "Anthropic Claude Sonnet 4"},
        {"id": "google/gemini-2.5-pro-preview", "label": "Google Gemini 2.5 Pro"},
        {"id": "deepseek/deepseek-chat", "label": "DeepSeek Chat"},
    ],
    "ollama": [
        {"id": "llama3.2", "label": "Llama 3.2"},
        {"id": "llama3.1", "label": "Llama 3.1"},
        {"id": "mistral", "label": "Mistral"},
        {"id": "qwen2.5", "label": "Qwen 2.5"},
    ],
}
