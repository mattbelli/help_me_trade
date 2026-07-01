"""Persistent app settings (broker keys, AI config) stored in data/settings.json."""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from config.ai_providers import DEFAULT_AI_SETTINGS, SECRET_AI_KEY_FIELDS, VALID_PROVIDERS

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "data" / "settings.json"
SECRET_MASK = "********"
VALID_ALPACA_DATA_FEEDS = frozenset({"iex", "sip"})

SECRET_KEYS = frozenset(
    {
        "api_key_id",
        "api_secret_key",
        "api_key",
        "app_secret",
        *SECRET_AI_KEY_FIELDS,
    }
)

load_dotenv(ROOT / ".env")

_settings_lock = threading.Lock()
_settings_cache: dict[str, Any] | None = None

DEFAULT_SETTINGS: dict[str, Any] = {
    "primary_broker": "alpaca",
    "brokers": {
        "alpaca": {
            "enabled": True,
            "api_key_id": "",
            "api_secret_key": "",
            "api_base_url": "https://paper-api.alpaca.markets",
            "data_base_url": "https://data.alpaca.markets",
            "data_feed": "iex",
        },
        "schwab": {
            "enabled": False,
            "api_key": "",
            "app_secret": "",
            "callback_url": "https://127.0.0.1:8182",
            "account_hash": "",
        },
    },
    "ai": DEFAULT_AI_SETTINGS.copy(),
    "research": {
        "searxng_url": "",
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
    "reports": {
        # Legacy SPY morning-levels job (kept as its own path — the Settings /
        # Automations UI toggles these two fields).
        "morning_levels_enabled": True,
        "hour_et": 8,
        # Additional scheduled report jobs (the LLM-authored kinds + extra
        # levels). Each: {kind, hour_et (ET), symbols, enabled}. Opt-in —
        # briefs cost LLM tokens, so they ship disabled and the founder arms
        # them.
        "jobs": [
            {"kind": "morning_brief", "hour_et": 8, "symbols": [], "enabled": False},
            {"kind": "evening_brief", "hour_et": 17, "symbols": [], "enabled": False},
        ],
    },
    "trading": {
        "execution_mode": "dry_run",
        "kill_switch_active": False,
        "agent_proposals_enabled": True,
        # deprecated: see strategies registry (db/strategies.py)
        "dip_strategy_enabled": False,
        # deprecated: see strategies registry (db/strategies.py)
        "dip_strategy_notional_usd": 500,
        "max_pending_agent_proposals": 3,
        "max_order_value_usd": 50000,
        "max_position_pct": 30,
        "max_open_orders": 10,
        "max_daily_orders": 20,
        # Short selling (sell-to-open). OFF by default. Only ever honored in
        # dry_run/paper — LIVE short selling stays hard-blocked regardless.
        "allow_short_selling": False,
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
                "data_feed": os.environ.get("APCA_DATA_FEED", "iex").strip(),
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
            "provider": os.environ.get("LLM_PROVIDER", os.environ.get("AI_PROVIDER", "mock")).strip()
            or "mock",
            "openai_api_key": os.environ.get("OPENAI_API_KEY", "").strip(),
            "openai_base_url": os.environ.get(
                "OPENAI_BASE_URL", DEFAULT_AI_SETTINGS["openai_base_url"]
            ).strip(),
            "openai_model": os.environ.get("OPENAI_MODEL", DEFAULT_AI_SETTINGS["openai_model"]).strip(),
            "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
            "anthropic_base_url": os.environ.get(
                "ANTHROPIC_BASE_URL", DEFAULT_AI_SETTINGS["anthropic_base_url"]
            ).strip(),
            "anthropic_model": os.environ.get(
                "ANTHROPIC_MODEL", DEFAULT_AI_SETTINGS["anthropic_model"]
            ).strip(),
            "deepseek_api_key": os.environ.get("DEEPSEEK_API_KEY", "").strip(),
            "deepseek_base_url": os.environ.get(
                "DEEPSEEK_BASE_URL", DEFAULT_AI_SETTINGS["deepseek_base_url"]
            ).strip(),
            "deepseek_model": os.environ.get(
                "DEEPSEEK_MODEL", DEFAULT_AI_SETTINGS["deepseek_model"]
            ).strip(),
            "openrouter_api_key": os.environ.get("OPENROUTER_API_KEY", "").strip(),
            "openrouter_base_url": os.environ.get(
                "OPENROUTER_BASE_URL", DEFAULT_AI_SETTINGS["openrouter_base_url"]
            ).strip(),
            "openrouter_model": os.environ.get(
                "OPENROUTER_MODEL", DEFAULT_AI_SETTINGS["openrouter_model"]
            ).strip(),
            "ollama_base_url": os.environ.get(
                "OLLAMA_BASE_URL", DEFAULT_AI_SETTINGS["ollama_base_url"]
            ).strip(),
            "ollama_model": os.environ.get(
                "OLLAMA_MODEL", DEFAULT_AI_SETTINGS["ollama_model"]
            ).strip(),
        },
        "research": {
            "searxng_url": os.environ.get("SEARXNG_URL", "").strip().rstrip("/"),
        },
        "trading": {
            "execution_mode": os.environ.get("TRADING_EXECUTION_MODE", "dry_run").strip()
            or "dry_run",
            "kill_switch_active": os.environ.get("TRADING_KILL_SWITCH", "")
            .strip()
            .lower()
            in ("1", "true", "yes"),
        },
    }


def clear_settings_cache() -> None:
    global _settings_cache
    with _settings_lock:
        _settings_cache = None


def _build_settings() -> dict[str, Any]:
    effective = _deep_merge(DEFAULT_SETTINGS, _env_defaults())

    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                effective = _deep_merge(effective, saved)
        except (OSError, json.JSONDecodeError):
            pass

    return effective


def load_settings() -> dict[str, Any]:
    """Effective settings: defaults <- env <- saved file."""
    global _settings_cache
    with _settings_lock:
        if _settings_cache is None:
            _settings_cache = _build_settings()
        return copy.deepcopy(_settings_cache)


def save_settings(settings: dict[str, Any]) -> None:
    global _settings_cache
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(settings, indent=2, sort_keys=True) + "\n"
    with _settings_lock:
        fd, tmp_path = tempfile.mkstemp(
            dir=SETTINGS_PATH.parent,
            prefix=".settings-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(tmp_path, SETTINGS_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        _settings_cache = copy.deepcopy(settings)


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
        from ai.llm.config import maybe_upgrade_default_llm, sync_default_llm_fields

        current["ai"] = maybe_upgrade_default_llm(current["ai"])
        current["ai"] = sync_default_llm_fields(current["ai"])
        provider = str(current["ai"].get("provider", "")).strip().lower()
        if provider not in VALID_PROVIDERS or provider == "mock":
            fallback = current["ai"].get("default_llm")
            if isinstance(fallback, dict):
                fb = str(fallback.get("provider", "")).strip().lower()
                if fb in VALID_PROVIDERS and fb != "mock":
                    current["ai"]["provider"] = fb
                else:
                    current["ai"]["provider"] = "deepseek"
            else:
                current["ai"]["provider"] = "deepseek"

    if "research" in patch and isinstance(patch["research"], dict):
        if "research" not in current or not isinstance(current.get("research"), dict):
            current["research"] = {"searxng_url": ""}
        url = patch["research"].get("searxng_url")
        if url is not None:
            current["research"]["searxng_url"] = str(url).strip().rstrip("/")

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

    if "reports" in patch and isinstance(patch["reports"], dict):
        if "reports" not in current or not isinstance(current.get("reports"), dict):
            current["reports"] = copy.deepcopy(DEFAULT_SETTINGS["reports"])
        reports_patch = patch["reports"]
        if "morning_levels_enabled" in reports_patch:
            current["reports"]["morning_levels_enabled"] = bool(
                reports_patch["morning_levels_enabled"]
            )
        if "hour_et" in reports_patch:
            current["reports"]["hour_et"] = max(
                0, min(23, int(reports_patch["hour_et"]))
            )
        if "jobs" in reports_patch:
            current["reports"]["jobs"] = _normalize_report_jobs(reports_patch["jobs"])

    if "trading" in patch and isinstance(patch["trading"], dict):
        if "trading" not in current or not isinstance(current.get("trading"), dict):
            current["trading"] = copy.deepcopy(DEFAULT_SETTINGS["trading"])
        trading_patch = patch["trading"]
        for key, value in trading_patch.items():
            if key == "kill_switch_active":
                current["trading"]["kill_switch_active"] = bool(value)
            elif key == "execution_mode" and str(value).strip() in (
                "dry_run",
                "paper",
                "live",
            ):
                current["trading"]["execution_mode"] = str(value).strip()
            elif key == "max_order_value_usd":
                current["trading"]["max_order_value_usd"] = float(value)
            elif key == "max_position_pct":
                current["trading"]["max_position_pct"] = float(value)
            elif key == "max_open_orders":
                current["trading"]["max_open_orders"] = int(value)
            elif key == "max_daily_orders":
                current["trading"]["max_daily_orders"] = int(value)
            elif key == "allow_short_selling":
                current["trading"]["allow_short_selling"] = bool(value)
            elif key == "agent_proposals_enabled":
                current["trading"]["agent_proposals_enabled"] = bool(value)
            elif key == "dip_strategy_enabled":
                current["trading"]["dip_strategy_enabled"] = bool(value)
            elif key == "dip_strategy_notional_usd":
                current["trading"]["dip_strategy_notional_usd"] = max(0.0, float(value))
            elif key == "max_pending_agent_proposals":
                current["trading"]["max_pending_agent_proposals"] = max(
                    0, min(50, int(value))
                )

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


def normalize_alpaca_data_feed(value: Any) -> str:
    feed = str(value or "iex").strip().lower()
    return feed if feed in VALID_ALPACA_DATA_FEEDS else "iex"


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
        "data_feed": normalize_alpaca_data_feed(cfg.get("data_feed")),
    }


_ALPACA_KEY_SUFFIX_RE = re.compile(r"^APCA_API_KEY_ID_(.+)$")


def alpaca_config_for_suffix(suffix: str | None) -> dict[str, str]:
    """Resolve Alpaca credentials from env, optionally via a connection suffix."""
    if not suffix:
        return alpaca_config()
    primary = alpaca_config()
    tag = str(suffix).strip()
    data_feed_env = os.environ.get(f"APCA_DATA_FEED_{tag}", "").strip()
    data_base_env = os.environ.get(f"APCA_DATA_BASE_URL_{tag}", "").strip()
    return {
        "api_key_id": os.environ.get(f"APCA_API_KEY_ID_{tag}", "").strip(),
        "api_secret_key": os.environ.get(f"APCA_API_SECRET_KEY_{tag}", "").strip(),
        "api_base_url": os.environ.get(f"APCA_API_BASE_URL_{tag}", "").strip(),
        "data_base_url": data_base_env or primary["data_base_url"],
        "data_feed": normalize_alpaca_data_feed(
            data_feed_env or primary["data_feed"]
        ),
    }


def discover_alpaca_credential_suffixes() -> list[str]:
    """Return env suffixes with a complete Alpaca key pair + base URL."""
    found: set[str] = set()
    for key in os.environ:
        match = _ALPACA_KEY_SUFFIX_RE.match(key)
        if not match:
            continue
        suffix = match.group(1)
        cfg = alpaca_config_for_suffix(suffix)
        if cfg["api_key_id"] and cfg["api_secret_key"] and cfg["api_base_url"]:
            found.add(suffix)
    return sorted(found)


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


def trading_config() -> dict[str, Any]:
    cfg = load_settings().get("trading", {})
    defaults = DEFAULT_SETTINGS["trading"]
    return {
        "execution_mode": str(cfg.get("execution_mode", defaults["execution_mode"])).strip()
        or defaults["execution_mode"],
        "kill_switch_active": bool(
            cfg.get("kill_switch_active", defaults["kill_switch_active"])
        ),
        "agent_proposals_enabled": bool(
            cfg.get("agent_proposals_enabled", defaults["agent_proposals_enabled"])
        ),
        "dip_strategy_enabled": bool(
            cfg.get("dip_strategy_enabled", defaults["dip_strategy_enabled"])
        ),
        "dip_strategy_notional_usd": float(
            cfg.get("dip_strategy_notional_usd", defaults["dip_strategy_notional_usd"])
        ),
        "max_pending_agent_proposals": int(
            cfg.get(
                "max_pending_agent_proposals",
                defaults["max_pending_agent_proposals"],
            )
        ),
        "max_order_value_usd": float(
            cfg.get("max_order_value_usd", defaults["max_order_value_usd"])
        ),
        "max_position_pct": float(
            cfg.get("max_position_pct", defaults["max_position_pct"])
        ),
        "max_open_orders": int(cfg.get("max_open_orders", defaults["max_open_orders"])),
        "max_daily_orders": int(cfg.get("max_daily_orders", defaults["max_daily_orders"])),
        "allow_short_selling": bool(
            cfg.get("allow_short_selling", defaults["allow_short_selling"])
        ),
    }


# Report kinds the background scheduler can run as configured jobs. The legacy
# SPY ``morning_levels`` keeps its own dedicated path, so it is not listed here.
SCHEDULABLE_REPORT_KINDS = ("levels", "morning_brief", "evening_brief")


def _normalize_report_jobs(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return copy.deepcopy(DEFAULT_SETTINGS["reports"]["jobs"])
    jobs: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip()
        if kind not in SCHEDULABLE_REPORT_KINDS:
            continue
        try:
            hour_et = max(0, min(23, int(item.get("hour_et", 8))))
        except (TypeError, ValueError):
            hour_et = 8
        symbols = [
            str(s).strip().upper()
            for s in (item.get("symbols") or [])
            if str(s).strip()
        ]
        jobs.append(
            {
                "kind": kind,
                "hour_et": hour_et,
                "symbols": symbols,
                "enabled": bool(item.get("enabled", False)),
            }
        )
    return jobs


def reports_config() -> dict[str, Any]:
    """Effective scheduled-report settings (env overrides hour when set)."""
    with _settings_lock:
        settings = _settings_cache if _settings_cache is not None else _build_settings()
        defaults = DEFAULT_SETTINGS["reports"]
        cfg = settings.get("reports")
        if not isinstance(cfg, dict):
            cfg = {}
        hour_raw = os.environ.get(
            "MORNING_REPORT_HOUR_ET",
            str(cfg.get("hour_et", defaults["hour_et"])),
        )
        try:
            hour_et = max(0, min(23, int(hour_raw)))
        except (TypeError, ValueError):
            hour_et = int(defaults["hour_et"])
        return {
            "morning_levels_enabled": bool(
                cfg.get("morning_levels_enabled", defaults["morning_levels_enabled"])
            ),
            "hour_et": hour_et,
            "jobs": _normalize_report_jobs(cfg.get("jobs", defaults["jobs"])),
        }


def short_selling_allowed() -> bool:
    """True only when shorting is explicitly enabled AND we're not in live mode.

    Single source of truth for the short-sell gate. LIVE short selling is
    hard-blocked regardless of the toggle (live needs the typed double-gate,
    not built) — only ``dry_run``/``paper`` ever honor the toggle.
    """
    cfg = trading_config()
    return bool(cfg.get("allow_short_selling", False)) and cfg.get(
        "execution_mode"
    ) in ("dry_run", "paper")


def is_paper_trading() -> bool:
    return "paper-api" in alpaca_config()["api_base_url"]


def is_kill_switch_active() -> bool:
    with _settings_lock:
        settings = _settings_cache if _settings_cache is not None else _build_settings()
        cfg = settings.get("trading", {})
        defaults = DEFAULT_SETTINGS["trading"]
        return bool(cfg.get("kill_switch_active", defaults["kill_switch_active"]))


def set_kill_switch(active: bool) -> None:
    apply_settings_patch({"trading": {"kill_switch_active": active}})


def broker_is_configured(name: str) -> bool:
    settings = load_settings()
    cfg = settings["brokers"].get(name, {})
    if name == "alpaca":
        return bool(cfg.get("api_key_id") and cfg.get("api_secret_key"))
    if name == "schwab":
        return bool(cfg.get("api_key") and cfg.get("app_secret"))
    return False


def finnhub_api_key() -> str:
    """Finnhub API key for the earnings-calendar feed (provider-agnostic, .env)."""
    return os.environ.get("FINNHUB_API_KEY", "").strip()


def fred_api_key() -> str:
    """FRED API key for macro regime series (yield curve, VIX)."""
    return os.environ.get("FRED_API_KEY", "").strip()


def fred_is_configured() -> bool:
    return bool(fred_api_key())


def alphavantage_api_key() -> str:
    return os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()


def push_config() -> dict[str, Any]:
    return {
        "enabled": os.environ.get("PUSH_ENABLED", "").strip().lower()
        in ("1", "true", "yes"),
        "provider": os.environ.get("PUSH_PROVIDER", "expo").strip().lower(),
        "expo_access_token": os.environ.get("EXPO_ACCESS_TOKEN", "").strip(),
        "apns_key_id": os.environ.get("APNS_KEY_ID", "").strip(),
        "apns_team_id": os.environ.get("APNS_TEAM_ID", "").strip(),
        "apns_bundle_id": os.environ.get("APNS_BUNDLE_ID", "").strip(),
        "apns_key_path": os.environ.get("APNS_KEY_PATH", "").strip(),
        "apns_use_sandbox": os.environ.get("APNS_USE_SANDBOX", "1").strip().lower()
        in ("1", "true", "yes"),
    }
