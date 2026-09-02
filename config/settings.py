"""Persistent app settings (broker keys, AI config) stored in data/settings.json."""

from __future__ import annotations

import copy
import fcntl
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from config.ai_providers import DEFAULT_AI_SETTINGS, SECRET_AI_KEY_FIELDS, VALID_PROVIDERS

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "data" / "settings.json"
SECRET_MASK = "********"
VALID_ALPACA_DATA_FEEDS = frozenset({"iex", "sip"})
VALID_ALPACA_CONFIGURED_DATA_FEEDS = frozenset({"iex", "sip", "auto"})

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
# Serializes every _mutate_settings read-modify-write within this process so two
# concurrent PUTs can't lost-update a breaker-armed kill_switch_active/
# entries_halted back to False (cross-process, _file_lock does the same job).
# Deliberately DISTINCT from _settings_lock (which is held only briefly by
# load/save): a patch can run a slow AI model-list probe while it holds this
# lock, and that must never block the fast is_kill_switch_active /
# is_entries_halted readers (they gate every order) — they only touch
# _settings_lock.
_patch_lock = threading.Lock()
_settings_cache: dict[str, Any] | None = None
# True when the LAST _build_settings() found settings.json present but
# unreadable (I/O error, bad JSON, non-object). Breaker readers fail CLOSED on
# it; writers refuse to use that defaults-only build as a base — persisting
# defaults over a corrupt file would silently disarm the breakers.
_file_parse_failed = False
# Re-entry tripwire: a mutate callback that writes settings again would
# self-deadlock on _patch_lock / a second flock fd. Fail loudly instead.
_in_mutate = threading.local()

# A wedged foreign lock holder must degrade into a loud error, not freeze every
# writer (and an async caller's event loop) forever. Normal holds are sub-ms.
_FILE_LOCK_TIMEOUT_SEC = 15.0


@contextmanager
def _file_lock() -> Iterator[None]:
    """Cross-PROCESS write lock (flock on settings.json.lock, in gitignored data/).

    _settings_lock/_patch_lock only serialize threads inside ONE process, but the
    backend, flow collector, report jobs, and scripts are separate processes that
    all write settings.json. #242's data loss (options_flow.provider silently
    deleted by a watchlist save) needs writers to take turns at the OS level.
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Computed at call time: tests repoint SETTINGS_PATH to a tmp file.
    lock_path = SETTINGS_PATH.with_name(SETTINGS_PATH.name + ".lock")
    with open(lock_path, "w", encoding="utf-8") as handle:
        deadline = time.monotonic() + _FILE_LOCK_TIMEOUT_SEC
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "settings write lock held by another process for "
                        f">{_FILE_LOCK_TIMEOUT_SEC:.0f}s — refusing to wait longer"
                    ) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


DEFAULT_SETTINGS: dict[str, Any] = {
    "primary_broker": "alpaca",
    "brokers": {
        "alpaca": {
            "enabled": True,
            "api_key_id": "",
            "api_secret_key": "",
            "api_base_url": "https://paper-api.alpaca.markets",
            "data_base_url": "https://data.alpaca.markets",
            "data_feed": "auto",
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
        "model_roles": {},
        "strong_model": "",
        "fast_model": "",
        "auto_delegate": True,
        "effort": "standard",
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
            # Deterministic (no LLM tokens); the generator itself waits for the
            # close-window gauge finalize before producing anything.
            {"kind": "flow_decomp", "hour_et": 16, "symbols": [], "enabled": True},
            # Deterministic; the generator waits for the 9:15 ET GTH close, so
            # the 9-o'clock window's first useful poll lands ~9:16-9:31.
            {"kind": "overnight_flow", "hour_et": 9, "symbols": [], "enabled": True},
        ],
    },
    "alerts": {
        # Daily ceiling on flow/confluence phone pushes (in-app notifications
        # are never capped — this only throttles what reaches the phone).
        "max_push_per_day": 5,
        # Unusual-vs-peers flow perception cards (signals/flow_perception.py).
        # OFF until the founder arms it: the scan's forward screening value was
        # pre-registered NULL twice (FLOW_ROADMAP 1.14c/1.14d), so it ships as
        # context to read, not as a source that files cards on its own.
        "flow_perception_alerts_enabled": False,
    },
    "trading": {
        "execution_mode": "dry_run",
        "kill_switch_active": False,
        # HALT: blocks exposure-INCREASING orders (entries/adds/short-opens)
        # while app-managed reduce-only EXITS keep flowing. Distinct from the
        # kill switch (total freeze). Armed by the daily-loss breaker's hard
        # stage OR by a human; never auto-arms the kill switch.
        "entries_halted": False,
        "agent_proposals_enabled": True,
        # deprecated: see strategies registry (db/strategies.py)
        "dip_strategy_enabled": False,
        # deprecated: see strategies registry (db/strategies.py)
        "dip_strategy_notional_usd": 500,
        "max_pending_agent_proposals": 3,
        "max_order_value_usd": 50000,
        "max_position_pct": 30,
        "max_open_orders": 10,
        # 0 = unlimited. A positive cap is a throttle, not a risk gate; the
        # global UTC counter used to let a morning rebalance silence the books.
        "max_daily_orders": 0,
        "max_daily_loss_usd": 2500.0,
        "max_daily_notional_usd": 150000.0,
        "max_rebalance_run_notional_usd": 120000.0,
        # Short selling (sell-to-open). OFF by default. Only ever honored in
        # dry_run/paper — LIVE short selling stays hard-blocked regardless.
        "allow_short_selling": False,
        # Vol-targeted sizing (signals/volatility.py). Master switch OFF: with
        # it off nothing calls the estimator and every notional is unchanged.
        # The per-strategy `vol_target` flag opts a single strategy in once
        # this is on, so arming is two deliberate steps, never one.
        "volatility_enabled": False,
        # One dial instead of raw target/clamp numbers — see VOL_TARGET_PROFILES.
        "vol_target_profile": "balanced",
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
                "data_feed": os.environ.get("APCA_DATA_FEED", "auto").strip(),
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
    global _file_parse_failed
    effective = _deep_merge(DEFAULT_SETTINGS, _env_defaults())

    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("settings.json is not a JSON object")
            effective = _deep_merge(effective, saved)
            _file_parse_failed = False
        except (OSError, ValueError) as exc:  # JSONDecodeError is a ValueError
            # Reads degrade to defaults (as always), but the flag makes breaker
            # readers fail CLOSED and writers refuse this build as their base.
            _file_parse_failed = True
            logger.error("settings.json exists but is unreadable: %s", exc)
    else:
        _file_parse_failed = False

    return effective


def _build_settings_for_write() -> dict[str, Any]:
    """Fresh effective settings as the BASE of a read-modify-write.

    A file that exists but can't be read must never be treated as empty here:
    writing the resulting defaults would disarm kill_switch/entries_halted and
    wipe every key. Fall back to this process's warm cache (the write then
    repairs the file — the pre-#242 behavior), and with no cache refuse to
    write at all.
    """
    effective = _deep_merge(DEFAULT_SETTINGS, _env_defaults())
    if not SETTINGS_PATH.exists():
        return effective
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("settings.json is not a JSON object")
    except (OSError, ValueError) as exc:
        with _settings_lock:
            cache = copy.deepcopy(_settings_cache)
        if cache is not None:
            logger.error(
                "settings.json unreadable (%s) — writing from the in-process "
                "cache to repair it",
                exc,
            )
            return cache
        raise RuntimeError(
            f"refusing to overwrite unreadable settings.json with defaults: {exc}"
        ) from exc
    return _deep_merge(effective, saved)


def load_settings() -> dict[str, Any]:
    """Effective settings: defaults <- env <- saved file."""
    global _settings_cache
    with _settings_lock:
        if _settings_cache is None:
            _settings_cache = _build_settings()
        return copy.deepcopy(_settings_cache)


def reload_settings() -> None:
    """Drop the in-process cache so the next load re-reads the settings file.

    Needed by processes that don't own settings writes (the flow collector):
    the API saves watchlist edits to the file, but this cache never sees them
    cross-process. Cheap — the next load_settings() rebuilds once.
    """
    global _settings_cache
    with _settings_lock:
        _settings_cache = None


def _write_settings_file(settings: dict[str, Any]) -> None:
    """Atomic write + in-process cache refresh. Callers hold _file_lock()."""
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
                handle.flush()
                # Durability: without fsync a power cut can persist the rename
                # but not the data — a zero-length settings.json (serv has no
                # UPS, and a corrupt file is the #242-review CRITICAL trigger).
                os.fsync(handle.fileno())
            os.replace(tmp_path, SETTINGS_PATH)
            dir_fd = os.open(SETTINGS_PATH.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        _settings_cache = copy.deepcopy(settings)


def _mutate_settings(mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """The one safe read-modify-write: FRESH file read -> delta -> write, locked.

    Under the cross-process file lock, rebuilds effective settings from the file
    as it is RIGHT NOW (never from this process's cache — a cache built before
    another process's write would silently delete that writer's keys, #242),
    applies the caller's delta in place, and writes atomically. _patch_lock is
    kept so two threads in this process serialize before touching the file lock;
    fast readers (kill switch / entries halted) only touch _settings_lock and
    never wait on writers. Returns the written effective settings (captured
    inside the lock, so a caller never sees a concurrent writer's state).
    """
    if getattr(_in_mutate, "active", False):
        raise RuntimeError(
            "re-entrant settings write — a mutate callback must not call "
            "apply_settings_patch/watchlist/chart-layout savers (self-deadlock)"
        )
    _in_mutate.active = True
    try:
        with _patch_lock:
            with _file_lock():
                current = _build_settings_for_write()
                mutate(current)
                _write_settings_file(current)
                return copy.deepcopy(current)
    finally:
        _in_mutate.active = False


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
    """Merge patch into saved settings, preserving secrets when masked/empty.

    Route-level auth guards trading.*, kill_switch_active, and entries_halted;
    this function remains callable internally (e.g. the risk-engine hard-trip
    arming entries_halted).

    The whole read-modify-write runs via _mutate_settings: fresh file read under
    the cross-process file lock, so a concurrent writer (or a stale in-process
    cache) can't lost-update someone else's keys or a breaker-armed flag. The
    slow AI model-list probe runs BEFORE the locks (_probe_default_llm) — the
    fast kill-switch/entries-halted readers touch just _settings_lock.
    """
    default_llm_override = _probe_default_llm(patch)
    return _mutate_settings(
        lambda current: _apply_patch(current, patch, default_llm_override)
    )


def _probe_default_llm(patch: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the post-patch default LLM BEFORE any locks are taken.

    maybe_upgrade_default_llm can probe provider model-list HTTP APIs for tens
    of seconds; holding the cross-process file lock across that would block
    every other settings writer (and time some out). Runs on an unlocked fresh
    snapshot — a concurrent ai edit in that window just re-resolves next patch.
    """
    if "ai" not in patch or not isinstance(patch["ai"], dict):
        return None
    from ai.llm.config import maybe_upgrade_default_llm

    try:
        preview = _build_settings_for_write()
    except RuntimeError:
        return None  # the locked write will surface the real error
    ai = preview.get("ai")
    ai = copy.deepcopy(ai) if isinstance(ai, dict) else {}
    _merge_section(ai, patch["ai"], secret_keys=SECRET_KEYS)
    default_llm = maybe_upgrade_default_llm(ai).get("default_llm")
    return default_llm if isinstance(default_llm, dict) else None


def _merge_section(
    target: dict, source: dict | None, *, secret_keys: frozenset[str]
) -> None:
    if not isinstance(source, dict):
        return
    for key, value in source.items():
        if key in secret_keys and (not value or _is_masked(value)):
            continue
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_section(target[key], value, secret_keys=secret_keys)
        else:
            target[key] = value


def _apply_patch(
    current: dict[str, Any],
    patch: dict[str, Any],
    default_llm_override: dict[str, Any] | None = None,
) -> None:
    """Apply patch onto current (the fresh effective settings) in place."""

    if "primary_broker" in patch and patch["primary_broker"] in ("alpaca", "schwab"):
        current["primary_broker"] = patch["primary_broker"]

    if "brokers" in patch and isinstance(patch["brokers"], dict):
        for name, broker_patch in patch["brokers"].items():
            if name not in current["brokers"] or not isinstance(broker_patch, dict):
                continue
            _merge_section(
                current["brokers"][name],
                broker_patch,
                secret_keys=SECRET_KEYS,
            )

    if "ai" in patch and isinstance(patch["ai"], dict):
        _merge_section(current["ai"], patch["ai"], secret_keys=SECRET_KEYS)
        from ai.llm.config import sync_default_llm_fields

        if default_llm_override is not None:
            # Resolved by _probe_default_llm outside the locks.
            current["ai"]["default_llm"] = default_llm_override
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
            current["research"] = copy.deepcopy(DEFAULT_SETTINGS["research"])
        research_patch = patch["research"]
        if "searxng_url" in research_patch:
            current["research"]["searxng_url"] = (
                str(research_patch.get("searxng_url", "")).strip().rstrip("/")
            )
        if "strong_model" in research_patch:
            current["research"]["strong_model"] = str(
                research_patch.get("strong_model", "")
            ).strip()
        if "fast_model" in research_patch:
            current["research"]["fast_model"] = str(
                research_patch.get("fast_model", "")
            ).strip()
        if "auto_delegate" in research_patch:
            current["research"]["auto_delegate"] = bool(
                research_patch.get("auto_delegate")
            )
        if "effort" in research_patch:
            current["research"]["effort"] = str(
                research_patch.get("effort", "standard")
            ).strip() or "standard"
        if "model_roles" in research_patch and isinstance(
            research_patch["model_roles"], dict
        ):
            roles = current["research"].get("model_roles")
            if not isinstance(roles, dict):
                roles = {}
            for role, model in research_patch["model_roles"].items():
                roles[str(role).strip()] = str(model or "").strip()
            current["research"]["model_roles"] = roles

    if "watchlist" in patch and isinstance(patch["watchlist"], list):
        current["watchlist"] = normalize_watchlist(patch["watchlist"])
        # From the fresh `current`, not the process cache (same staleness trap).
        state = _watchlists_state_from(current)
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

    if "alerts" in patch and isinstance(patch["alerts"], dict):
        if "alerts" not in current or not isinstance(current.get("alerts"), dict):
            current["alerts"] = copy.deepcopy(DEFAULT_SETTINGS["alerts"])
        alerts_patch = patch["alerts"]
        if "max_push_per_day" in alerts_patch:
            try:
                current["alerts"]["max_push_per_day"] = max(
                    0, min(100, int(alerts_patch["max_push_per_day"]))
                )
            except (TypeError, ValueError):
                pass

    if "trading" in patch and isinstance(patch["trading"], dict):
        if "trading" not in current or not isinstance(current.get("trading"), dict):
            current["trading"] = copy.deepcopy(DEFAULT_SETTINGS["trading"])
        trading_patch = patch["trading"]
        for key, value in trading_patch.items():
            if key == "kill_switch_active":
                current["trading"]["kill_switch_active"] = bool(value)
            elif key == "entries_halted":
                current["trading"]["entries_halted"] = bool(value)
            elif key == "execution_mode" and str(value).strip() in (
                "dry_run",
                "paper",
                "live",
            ):
                current["trading"]["execution_mode"] = str(value).strip()
            elif key == "max_order_value_usd":
                current["trading"]["max_order_value_usd"] = float(value)
            elif key == "max_daily_loss_usd":
                current["trading"]["max_daily_loss_usd"] = float(value)
            elif key == "max_daily_notional_usd":
                current["trading"]["max_daily_notional_usd"] = float(value)
            elif key == "max_rebalance_run_notional_usd":
                current["trading"]["max_rebalance_run_notional_usd"] = float(value)
            elif key == "max_position_pct":
                current["trading"]["max_position_pct"] = float(value)
            elif key == "max_open_orders":
                current["trading"]["max_open_orders"] = int(value)
            elif key == "max_daily_orders":
                current["trading"]["max_daily_orders"] = max(0, int(value))
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


_WATCHLIST_DENSITIES = {"compact", "comfortable", "spacious"}


def normalize_watchlist_settings(raw: object) -> dict | None:
    """Validate a per-list display-settings blob (columns/order/widths, row
    density). Unknown column keys are kept as-is — the frontend owns the column
    registry and silently drops keys it doesn't know, so old servers never
    strip columns added by a newer frontend."""
    if not isinstance(raw, dict):
        return None
    out: dict = {}

    columns_in = raw.get("columns")
    if isinstance(columns_in, list):
        columns: list[dict] = []
        seen: set[str] = set()
        for item in columns_in[:64]:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            if not key or len(key) > 40 or key in seen:
                continue
            seen.add(key)
            col: dict = {"key": key, "visible": bool(item.get("visible", True))}
            width = item.get("width")
            if isinstance(width, (int, float)):
                col["width"] = max(40, min(600, int(width)))
            columns.append(col)
        if columns:
            out["columns"] = columns

    density = raw.get("density")
    if isinstance(density, str) and density in _WATCHLIST_DENSITIES:
        out["density"] = density

    return out or None


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
        entry = {"id": list_id, "name": name, "symbols": symbols}
        max_slots = item.get("max_slots")
        if isinstance(max_slots, (int, float)) and int(max_slots) > 0:
            entry["max_slots"] = min(500, int(max_slots))
        pinned_in = item.get("pinned")
        if isinstance(pinned_in, list):
            # Pins only mean anything for list MEMBERS — a symbol removed from
            # the list sheds its pin rather than haunting the state.
            members = set(symbols)
            pinned = [s for s in normalize_watchlist(pinned_in) if s in members]
            if pinned:
                entry["pinned"] = pinned
        list_settings = normalize_watchlist_settings(item.get("settings"))
        if list_settings is not None:
            entry["settings"] = list_settings
        lists.append(entry)

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


def watchlist_capacity_error(entry: dict) -> str | None:
    """Slot-cap check for a normalized list entry, or None when it fits.

    Pinned symbols ride ABOVE the cap (founder 07-22: names pinned "no matter
    what" are added on top of the slots), so capacity counts unpinned members
    only. Enforced at the EDIT seams (update/add), never at load — a normalize
    must not silently drop symbols from an over-full list that already exists
    on disk.
    """
    max_slots = entry.get("max_slots")
    if not isinstance(max_slots, int):
        return None
    pinned = set(entry.get("pinned", []))
    unpinned = [s for s in entry.get("symbols", []) if s not in pinned]
    if len(unpinned) <= max_slots:
        return None
    label = entry.get("name") or entry.get("id") or "watchlist"
    return (
        f"“{label}” is full ({len(unpinned)}/{max_slots} slots) — "
        "remove a symbol first (pinned symbols don't use slots)"
    )


def _normalize_single_watchlist(entry: dict) -> dict:
    return normalize_watchlists_state({"active_id": entry.get("id", ""), "lists": [entry]})[
        "lists"
    ][0]


def get_watchlists_state() -> dict:
    return _watchlists_state_from(load_settings())


def _watchlists_state_from(settings: dict) -> dict:
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


def _update_watchlists_state(transform: Callable[[dict], dict]) -> dict:
    """Read-modify-write of the watchlists subtree against the FRESH file state.

    transform receives the state as it is on disk RIGHT NOW (inside the write
    lock) and returns the new state. Deriving state from get_watchlists_state()
    (the cache) outside the lock would wholesale-replace the subtree and delete
    lists another process added — the same lost update #242 is about, one key
    down. Validation errors raised inside transform abort before any write.
    """
    box: dict[str, dict] = {}

    def mutate(current: dict[str, Any]) -> None:
        normalized = normalize_watchlists_state(
            transform(_watchlists_state_from(current))
        )
        current["watchlists"] = normalized
        active = _find_watchlist(normalized, normalized["active_id"])
        current["watchlist"] = active["symbols"] if active else []
        box["state"] = normalized

    _mutate_settings(mutate)
    return box["state"]


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
    clean = normalize_watchlist(symbols)

    def transform(state: dict) -> dict:
        lists = [
            {**item, "symbols": clean} if item["id"] == state["active_id"] else item
            for item in state["lists"]
        ]
        return {**state, "lists": lists}

    saved = _update_watchlists_state(transform)
    active = _find_watchlist(saved, saved["active_id"])
    return list(active["symbols"]) if active else []


def set_active_watchlist(list_id: str) -> dict:
    def transform(state: dict) -> dict:
        if not _find_watchlist(state, list_id):
            raise ValueError(f"Unknown watchlist {list_id!r}")
        return {**state, "active_id": list_id}

    return _update_watchlists_state(transform)


def create_watchlist(
    name: str,
    symbols: list[str] | None = None,
    settings: dict | None = None,
) -> dict:
    clean_name = name.strip() or "New list"
    clean_symbols = normalize_watchlist(symbols or [])
    clean_settings = normalize_watchlist_settings(settings)

    def transform(state: dict) -> dict:
        base_id = _slug_watchlist_id(clean_name)
        list_id = base_id
        existing_ids = {item["id"] for item in state["lists"]}
        if list_id in existing_ids:
            list_id = f"{base_id}-{uuid.uuid4().hex[:6]}"
        new_list = {
            "id": list_id,
            "name": clean_name,
            "symbols": clean_symbols,
        }
        if clean_settings is not None:
            new_list["settings"] = clean_settings
        return {
            **state,
            "active_id": list_id,
            "lists": [*state["lists"], new_list],
        }

    return _update_watchlists_state(transform)


def update_watchlist(
    list_id: str,
    *,
    name: str | None = None,
    symbols: list[str] | None = None,
    settings: dict | None = None,
    max_slots: int | None = None,
    pinned: list[str] | None = None,
) -> dict:
    def transform(state: dict) -> dict:
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
            if settings is not None:
                clean = normalize_watchlist_settings(settings)
                if clean is not None:
                    updated["settings"] = clean
                else:
                    updated.pop("settings", None)
            if max_slots is not None:
                if int(max_slots) > 0:
                    updated["max_slots"] = min(500, int(max_slots))
                else:
                    updated.pop("max_slots", None)  # 0 clears the cap
            if pinned is not None:
                updated["pinned"] = normalize_watchlist(pinned)
            if symbols is not None or max_slots is not None or pinned is not None:
                err = watchlist_capacity_error(_normalize_single_watchlist(updated))
                if err:
                    raise ValueError(err)
            lists.append(updated)

        return {**state, "lists": lists}

    return _update_watchlists_state(transform)


def delete_watchlist(list_id: str) -> dict:
    def transform(state: dict) -> dict:
        if len(state["lists"]) <= 1:
            raise ValueError("Cannot delete the last watchlist")
        if not _find_watchlist(state, list_id):
            raise ValueError(f"Unknown watchlist {list_id!r}")

        lists = [item for item in state["lists"] if item["id"] != list_id]
        active_id = state["active_id"]
        if active_id == list_id:
            active_id = lists[0]["id"]
        return {"active_id": active_id, "lists": lists}

    return _update_watchlists_state(transform)


def add_watchlist_symbol(symbol: str, *, list_id: str | None = None) -> list[str]:
    sym = symbol.strip().upper()
    if not sym:
        raise ValueError("symbol is required")
    updated_symbols: list[str] = []

    def transform(state: dict) -> dict:
        target_id = list_id or state["active_id"]
        lists = []
        found = False
        for item in state["lists"]:
            if item["id"] != target_id:
                lists.append(item)
                continue
            found = True
            symbols = list(item["symbols"])
            added = sym not in symbols
            if added:
                symbols.append(sym)
            candidate = {**item, "symbols": symbols}
            if added:  # re-adding a member is a no-op, never a capacity error
                err = watchlist_capacity_error(_normalize_single_watchlist(candidate))
                if err:
                    raise ValueError(err)
            updated_symbols[:] = symbols
            lists.append(candidate)
        if not found:
            raise ValueError(f"Unknown watchlist {target_id!r}")
        return {**state, "lists": lists}

    _update_watchlists_state(transform)
    return updated_symbols


def remove_watchlist_symbol(symbol: str, *, list_id: str | None = None) -> list[str]:
    sym = symbol.strip().upper()
    updated_symbols: list[str] = []

    def transform(state: dict) -> dict:
        target_id = list_id or state["active_id"]
        lists = []
        found = False
        for item in state["lists"]:
            if item["id"] != target_id:
                lists.append(item)
                continue
            found = True
            symbols = [s for s in item["symbols"] if s != sym]
            updated_symbols[:] = symbols
            lists.append({**item, "symbols": symbols})
        if not found:
            raise ValueError(f"Unknown watchlist {target_id!r}")
        return {**state, "lists": lists}

    _update_watchlists_state(transform)
    return updated_symbols


def get_chart_layout() -> list[dict]:
    layout = load_settings().get("chart_layout")
    if isinstance(layout, list) and layout:
        return layout
    return default_chart_layout(get_watchlist())


def save_chart_layout(layout: list[dict]) -> list[dict]:
    def mutate(current: dict[str, Any]) -> None:
        current["chart_layout"] = layout

    _mutate_settings(mutate)
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
    feed = str(value or "auto").strip().lower()
    return feed if feed in VALID_ALPACA_CONFIGURED_DATA_FEEDS else "auto"


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


def alerts_config() -> dict[str, Any]:
    cfg = load_settings().get("alerts", {})
    defaults = DEFAULT_SETTINGS["alerts"]
    try:
        max_push = int(cfg.get("max_push_per_day", defaults["max_push_per_day"]))
    except (TypeError, ValueError):
        max_push = int(defaults["max_push_per_day"])
    return {
        "max_push_per_day": max(0, max_push),
        "flow_perception_alerts_enabled": bool(
            cfg.get(
                "flow_perception_alerts_enabled",
                defaults["flow_perception_alerts_enabled"],
            )
        ),
    }


def trading_config() -> dict[str, Any]:
    cfg = load_settings().get("trading", {})
    defaults = DEFAULT_SETTINGS["trading"]
    return {
        "execution_mode": str(cfg.get("execution_mode", defaults["execution_mode"])).strip()
        or defaults["execution_mode"],
        "kill_switch_active": bool(
            cfg.get("kill_switch_active", defaults["kill_switch_active"])
        ),
        "entries_halted": bool(
            cfg.get("entries_halted", defaults["entries_halted"])
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
        "max_daily_orders": max(
            0, int(cfg.get("max_daily_orders", defaults["max_daily_orders"]))
        ),
        "max_daily_loss_usd": float(
            cfg.get("max_daily_loss_usd", defaults["max_daily_loss_usd"])
        ),
        "max_daily_notional_usd": float(
            cfg.get("max_daily_notional_usd", defaults["max_daily_notional_usd"])
        ),
        "max_rebalance_run_notional_usd": float(
            cfg.get(
                "max_rebalance_run_notional_usd",
                defaults["max_rebalance_run_notional_usd"],
            )
        ),
        "allow_short_selling": bool(
            cfg.get("allow_short_selling", defaults["allow_short_selling"])
        ),
        "volatility_enabled": bool(
            cfg.get("volatility_enabled", defaults["volatility_enabled"])
        ),
        "vol_target_profile": str(
            cfg.get("vol_target_profile", defaults["vol_target_profile"])
        ).strip()
        or defaults["vol_target_profile"],
    }


# Report kinds the background scheduler can run as configured jobs. The legacy
# SPY ``morning_levels`` keeps its own dedicated path, so it is not listed here.
SCHEDULABLE_REPORT_KINDS = (
    "levels",
    "morning_brief",
    "evening_brief",
    "flow_decomp",
    "overnight_flow",
)


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
        if _settings_cache is not None:
            settings = _settings_cache
        else:
            settings = _build_settings()
            if _file_parse_failed:
                # File present but unreadable and no warm cache: the real flag
                # state is unknowable — fail CLOSED, never "defaults say False".
                return True
        cfg = settings.get("trading", {})
        defaults = DEFAULT_SETTINGS["trading"]
        return bool(cfg.get("kill_switch_active", defaults["kill_switch_active"]))


def set_kill_switch(active: bool) -> None:
    apply_settings_patch({"trading": {"kill_switch_active": active}})


def is_entries_halted() -> bool:
    """True when exposure-increasing entries are halted (the HALT state).

    Distinct from the kill switch: HALT blocks only orders that ADD exposure
    (entries/adds/short-opens); app-managed reduce-only exits keep flowing.
    Deny-by-default — if trading settings are unreadable/ambiguous for ANY
    reason, return True so entries are denied (the safe direction to fail;
    exits are gated on exposure direction elsewhere and are never affected).
    """
    try:
        with _settings_lock:
            if _settings_cache is not None:
                settings = _settings_cache
            else:
                settings = _build_settings()
                if _file_parse_failed:
                    # Unreadable file, cold cache: deny-by-default.
                    return True
        cfg = settings.get("trading")
        if not isinstance(cfg, dict):
            return True
        return bool(
            cfg.get("entries_halted", DEFAULT_SETTINGS["trading"]["entries_halted"])
        )
    except Exception:
        logger.warning(
            "entries_halted state unreadable — denying entries (deny-by-default)",
            exc_info=True,
        )
        return True


def set_entries_halted(active: bool) -> None:
    apply_settings_patch({"trading": {"entries_halted": active}})


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
