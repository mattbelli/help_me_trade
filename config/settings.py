"""Persistent app settings (broker keys, AI config) stored in data/settings.json."""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "data" / "settings.json"
SECRET_MASK = "********"
SECRET_KEYS = frozenset(
    {
        "api_key_id",
        "api_secret_key",
        "api_key",
        "app_secret",
        "openai_api_key",
        "anthropic_api_key",
    }
)

load_dotenv(ROOT / ".env")

DEFAULT_SETTINGS: dict[str, Any] = {
    "primary_broker": "alpaca",
    "brokers": {
        "alpaca": {
            "enabled": True,
            "api_key_id": "",
            "api_secret_key": "",
            "api_base_url": "https://paper-api.alpaca.markets",
            "data_base_url": "https://data.alpaca.markets",
        },
        "schwab": {
            "enabled": False,
            "api_key": "",
            "app_secret": "",
            "callback_url": "https://127.0.0.1:8182",
            "account_hash": "",
        },
    },
    "ai": {
        "provider": "openai",
        "openai_api_key": "",
        "openai_model": "gpt-4o",
        "anthropic_api_key": "",
        "ollama_base_url": "http://localhost:11434",
    },
    "watchlist": ["SPY", "QQQ", "AAPL", "NVDA", "MU", "AMD"],
    "watchlists": {
        "active_id": "main",
        "lists": [
            {
                "id": "main",
                "name": "Main",
                "symbols": ["SPY", "QQQ", "AAPL", "NVDA", "MU", "AMD"],
            }
        ],
    },
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _env_defaults() -> dict[str, Any]:
    broker_env = os.environ.get("BROKER", "alpaca").strip().lower()
    return {
        "primary_broker": broker_env if broker_env in ("alpaca", "schwab") else "alpaca",
        "brokers": {
            "alpaca": {
                "enabled": broker_env == "alpaca",
                "api_key_id": os.environ.get("APCA_API_KEY_ID", "").strip(),
                "api_secret_key": os.environ.get("APCA_API_SECRET_KEY", "").strip(),
                "api_base_url": os.environ.get(
                    "APCA_API_BASE_URL", "https://paper-api.alpaca.markets"
                ).strip(),
                "data_base_url": os.environ.get(
                    "APCA_DATA_BASE_URL", "https://data.alpaca.markets"
                ).strip(),
            },
            "schwab": {
                "enabled": broker_env == "schwab",
                "api_key": os.environ.get("SCHWAB_API_KEY", "").strip(),
                "app_secret": os.environ.get("SCHWAB_APP_SECRET", "").strip(),
                "callback_url": os.environ.get(
                    "SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182"
                ).strip(),
                "account_hash": os.environ.get("SCHWAB_ACCOUNT_HASH", "").strip(),
            },
        },
        "ai": {
            "provider": os.environ.get("AI_PROVIDER", "openai").strip() or "openai",
            "openai_api_key": os.environ.get("OPENAI_API_KEY", "").strip(),
            "openai_model": os.environ.get("OPENAI_MODEL", "gpt-4o").strip(),
            "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
            "ollama_base_url": os.environ.get(
                "OLLAMA_BASE_URL", "http://localhost:11434"
            ).strip(),
        },
    }


def load_settings() -> dict[str, Any]:
    """Effective settings: defaults <- env <- saved file."""
    effective = _deep_merge(DEFAULT_SETTINGS, _env_defaults())

    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                effective = _deep_merge(effective, saved)
        except (OSError, json.JSONDecodeError):
            pass

    return effective


def save_settings(settings: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _mask_value(key: str, value: Any) -> Any:
    if key in SECRET_KEYS and value:
        return SECRET_MASK
    return value


def mask_settings(settings: dict[str, Any]) -> dict[str, Any]:
    masked = copy.deepcopy(settings)
    for broker_cfg in masked.get("brokers", {}).values():
        if isinstance(broker_cfg, dict):
            for key in list(broker_cfg.keys()):
                broker_cfg[key] = _mask_value(key, broker_cfg[key])
    ai = masked.get("ai")
    if isinstance(ai, dict):
        for key in list(ai.keys()):
            ai[key] = _mask_value(key, ai[key])
    return masked


def _is_masked(value: Any) -> bool:
    return value == SECRET_MASK


def apply_settings_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge patch into saved settings, preserving secrets when masked/empty."""
    current = load_settings()

    # Only persist user-editable fields from file (not env-only fallbacks)
    if SETTINGS_PATH.exists():
        try:
            on_disk = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(on_disk, dict):
                current = _deep_merge(current, on_disk)
        except (OSError, json.JSONDecodeError):
            pass

    def merge_section(
        target: dict, source: dict | None, *, secret_keys: frozenset[str]
    ) -> None:
        if not isinstance(source, dict):
            return
        for key, value in source.items():
            if key in secret_keys and (not value or _is_masked(value)):
                continue
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge_section(target[key], value, secret_keys=secret_keys)
            else:
                target[key] = value

    if "primary_broker" in patch and patch["primary_broker"] in ("alpaca", "schwab"):
        current["primary_broker"] = patch["primary_broker"]

    if "brokers" in patch and isinstance(patch["brokers"], dict):
        for name, broker_patch in patch["brokers"].items():
            if name not in current["brokers"] or not isinstance(broker_patch, dict):
                continue
            merge_section(
                current["brokers"][name],
                broker_patch,
                secret_keys=SECRET_KEYS,
            )

    if "ai" in patch and isinstance(patch["ai"], dict):
        merge_section(current["ai"], patch["ai"], secret_keys=SECRET_KEYS)

    if "watchlist" in patch and isinstance(patch["watchlist"], list):
        current["watchlist"] = normalize_watchlist(patch["watchlist"])
        state = get_watchlists_state()
        lists = []
        for item in state["lists"]:
            if item["id"] == state["active_id"]:
                lists.append({**item, "symbols": current["watchlist"]})
            else:
                lists.append(item)
        current["watchlists"] = normalize_watchlists_state(
            {**state, "lists": lists}
        )

    if "watchlists" in patch and isinstance(patch["watchlists"], dict):
        current["watchlists"] = normalize_watchlists_state(patch["watchlists"])
        active = _find_watchlist(current["watchlists"], current["watchlists"]["active_id"])
        current["watchlist"] = active["symbols"] if active else []

    if "chart_layout" in patch and isinstance(patch["chart_layout"], list):
        current["chart_layout"] = patch["chart_layout"]

    save_settings(current)
    return load_settings()


def normalize_watchlist(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in symbols:
        if not isinstance(raw, str):
            continue
        sym = raw.strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def _slug_watchlist_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or f"list-{uuid.uuid4().hex[:8]}"


def normalize_watchlists_state(state: dict) -> dict:
    lists_in = state.get("lists")
    if not isinstance(lists_in, list) or not lists_in:
        symbols = normalize_watchlist(DEFAULT_SETTINGS["watchlist"])
        return {
            "active_id": "main",
            "lists": [{"id": "main", "name": "Main", "symbols": symbols}],
        }

    lists: list[dict] = []
    seen_ids: set[str] = set()
    for item in lists_in:
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("id", "")).strip()
        list_id = raw_id or _slug_watchlist_id(str(item.get("name", "List")))
        if list_id in seen_ids:
            list_id = f"{list_id}-{uuid.uuid4().hex[:6]}"
        seen_ids.add(list_id)
        name = str(item.get("name", list_id)).strip() or list_id
        symbols = normalize_watchlist(item.get("symbols", []))
        lists.append({"id": list_id, "name": name, "symbols": symbols})

    if not lists:
        symbols = normalize_watchlist(DEFAULT_SETTINGS["watchlist"])
        return {
            "active_id": "main",
            "lists": [{"id": "main", "name": "Main", "symbols": symbols}],
        }

    active_id = str(state.get("active_id", lists[0]["id"])).strip()
    if not any(lst["id"] == active_id for lst in lists):
        active_id = lists[0]["id"]

    return {"active_id": active_id, "lists": lists}


def get_watchlists_state() -> dict:
    settings = load_settings()
    raw = settings.get("watchlists")
    if isinstance(raw, dict):
        return normalize_watchlists_state(raw)

    legacy = settings.get("watchlist", DEFAULT_SETTINGS["watchlist"])
    symbols = normalize_watchlist(legacy if isinstance(legacy, list) else [])
    if not symbols:
        symbols = normalize_watchlist(DEFAULT_SETTINGS["watchlist"])
    return {
        "active_id": "main",
        "lists": [{"id": "main", "name": "Main", "symbols": symbols}],
    }


def _save_watchlists_state(state: dict) -> dict:
    normalized = normalize_watchlists_state(state)
    current = load_settings()
    current["watchlists"] = normalized
    active = _find_watchlist(normalized, normalized["active_id"])
    current["watchlist"] = active["symbols"] if active else []
    save_settings(current)
    return normalized


def _find_watchlist(state: dict, list_id: str) -> dict | None:
    for item in state.get("lists", []):
        if isinstance(item, dict) and item.get("id") == list_id:
            return item
    return None


def get_active_watchlist() -> dict:
    state = get_watchlists_state()
    active = _find_watchlist(state, state["active_id"])
    return active or state["lists"][0]


def get_watchlist() -> list[str]:
    return list(get_active_watchlist().get("symbols", []))


def save_watchlist(symbols: list[str]) -> list[str]:
    state = get_watchlists_state()
    active_id = state["active_id"]
    lists = []
    for item in state["lists"]:
        if item["id"] == active_id:
            lists.append({**item, "symbols": normalize_watchlist(symbols)})
        else:
            lists.append(item)
    saved = _save_watchlists_state({**state, "lists": lists})
    active = _find_watchlist(saved, saved["active_id"])
    return list(active["symbols"]) if active else []


def set_active_watchlist(list_id: str) -> dict:
    state = get_watchlists_state()
    if not _find_watchlist(state, list_id):
        raise ValueError(f"Unknown watchlist {list_id!r}")
    return _save_watchlists_state({**state, "active_id": list_id})


def create_watchlist(name: str, symbols: list[str] | None = None) -> dict:
    clean_name = name.strip() or "New list"
    state = get_watchlists_state()
    base_id = _slug_watchlist_id(clean_name)
    list_id = base_id
    existing_ids = {item["id"] for item in state["lists"]}
    if list_id in existing_ids:
        list_id = f"{base_id}-{uuid.uuid4().hex[:6]}"
    new_list = {
        "id": list_id,
        "name": clean_name,
        "symbols": normalize_watchlist(symbols or []),
    }
    return _save_watchlists_state(
        {
            **state,
            "active_id": list_id,
            "lists": [*state["lists"], new_list],
        }
    )


def update_watchlist(
    list_id: str,
    *,
    name: str | None = None,
    symbols: list[str] | None = None,
) -> dict:
    state = get_watchlists_state()
    if not _find_watchlist(state, list_id):
        raise ValueError(f"Unknown watchlist {list_id!r}")

    lists = []
    for item in state["lists"]:
        if item["id"] != list_id:
            lists.append(item)
            continue
        updated = dict(item)
        if name is not None:
            updated["name"] = name.strip() or item["name"]
        if symbols is not None:
            updated["symbols"] = normalize_watchlist(symbols)
        lists.append(updated)

    return _save_watchlists_state({**state, "lists": lists})


def delete_watchlist(list_id: str) -> dict:
    state = get_watchlists_state()
    if len(state["lists"]) <= 1:
        raise ValueError("Cannot delete the last watchlist")
    if not _find_watchlist(state, list_id):
        raise ValueError(f"Unknown watchlist {list_id!r}")

    lists = [item for item in state["lists"] if item["id"] != list_id]
    active_id = state["active_id"]
    if active_id == list_id:
        active_id = lists[0]["id"]
    return _save_watchlists_state({"active_id": active_id, "lists": lists})


def add_watchlist_symbol(symbol: str, *, list_id: str | None = None) -> list[str]:
    sym = symbol.strip().upper()
    if not sym:
        raise ValueError("symbol is required")
    state = get_watchlists_state()
    target_id = list_id or state["active_id"]
    lists = []
    updated_symbols: list[str] = []
    for item in state["lists"]:
        if item["id"] != target_id:
            lists.append(item)
            continue
        symbols = list(item["symbols"])
        if sym not in symbols:
            symbols.append(sym)
        updated_symbols = symbols
        lists.append({**item, "symbols": symbols})
    if not updated_symbols:
        raise ValueError(f"Unknown watchlist {target_id!r}")
    _save_watchlists_state({**state, "lists": lists})
    return updated_symbols


def remove_watchlist_symbol(symbol: str, *, list_id: str | None = None) -> list[str]:
    sym = symbol.strip().upper()
    state = get_watchlists_state()
    target_id = list_id or state["active_id"]
    lists = []
    updated_symbols: list[str] = []
    found = False
    for item in state["lists"]:
        if item["id"] != target_id:
            lists.append(item)
            continue
        found = True
        symbols = [s for s in item["symbols"] if s != sym]
        updated_symbols = symbols
        lists.append({**item, "symbols": symbols})
    if not found:
        raise ValueError(f"Unknown watchlist {target_id!r}")
    _save_watchlists_state({**state, "lists": lists})
    return updated_symbols


def get_chart_layout() -> list[dict]:
    layout = load_settings().get("chart_layout")
    if isinstance(layout, list) and layout:
        return layout
    return default_chart_layout(get_watchlist())


def save_chart_layout(layout: list[dict]) -> list[dict]:
    current = load_settings()
    current["chart_layout"] = layout
    save_settings(current)
    return layout


def default_chart_layout(symbols: list[str]) -> list[dict]:
    """Six-chart 2×3 grid for react-grid-layout (12 columns)."""
    picks = (symbols + ["SPY", "QQQ", "AAPL", "NVDA", "MU", "AMD"])[:6]
    slots = [
        {"x": 0, "y": 0, "w": 4, "h": 5},
        {"x": 4, "y": 0, "w": 4, "h": 5},
        {"x": 8, "y": 0, "w": 4, "h": 5},
        {"x": 0, "y": 5, "w": 4, "h": 5},
        {"x": 4, "y": 5, "w": 4, "h": 5},
        {"x": 8, "y": 5, "w": 4, "h": 5},
    ]
    return [
        {
            "i": str(i),
            "symbol": picks[i],
            **slots[i],
        }
        for i in range(6)
    ]


def alpaca_config() -> dict[str, str]:
    cfg = load_settings()["brokers"]["alpaca"]
    return {
        "api_key_id": str(cfg.get("api_key_id", "")).strip(),
        "api_secret_key": str(cfg.get("api_secret_key", "")).strip(),
        "api_base_url": str(
            cfg.get("api_base_url", "https://paper-api.alpaca.markets")
        ).strip(),
        "data_base_url": str(
            cfg.get("data_base_url", "https://data.alpaca.markets")
        ).strip(),
    }


def schwab_config() -> dict[str, str]:
    cfg = load_settings()["brokers"]["schwab"]
    return {
        "api_key": str(cfg.get("api_key", "")).strip(),
        "app_secret": str(cfg.get("app_secret", "")).strip(),
        "callback_url": str(
            cfg.get("callback_url", "https://127.0.0.1:8182")
        ).strip(),
        "account_hash": str(cfg.get("account_hash", "")).strip(),
    }


def primary_broker() -> str:
    name = load_settings().get("primary_broker", "alpaca")
    return name if name in ("alpaca", "schwab") else "alpaca"


def enabled_brokers() -> list[str]:
    settings = load_settings()
    enabled: list[str] = []
    for name in ("alpaca", "schwab"):
        cfg = settings["brokers"].get(name, {})
        if cfg.get("enabled"):
            enabled.append(name)
    if not enabled:
        return [primary_broker()]
    return enabled


def broker_is_configured(name: str) -> bool:
    settings = load_settings()
    cfg = settings["brokers"].get(name, {})
    if name == "alpaca":
        return bool(cfg.get("api_key_id") and cfg.get("api_secret_key"))
    if name == "schwab":
        return bool(cfg.get("api_key") and cfg.get("app_secret"))
    return False
