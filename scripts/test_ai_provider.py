#!/usr/bin/env python3
"""Smoke test a real LLM provider (requires API key in settings or env).

Usage:
  LLM_PROVIDER=openai python3 scripts/test_ai_provider.py
  python3 scripts/test_ai_provider.py --provider anthropic

Skips gracefully when the provider is mock or credentials are missing.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    msg = f"[{status}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    if not ok:
        raise AssertionError(f"{name}: {detail}")


def _seed_snapshot(db_path: Path) -> None:
    from brokers.base import Account, Order, Position
    from db.ai_tables import init_ai_tables
    from db.chat_sessions import init_chat_tables
    from db.sqlite import init_db, save_snapshot

    init_db(db_path)
    init_ai_tables(db_path)
    init_chat_tables(db_path)
    account = Account(
        id="smoke-acct",
        status="ACTIVE",
        cash=50000.0,
        buying_power=100000.0,
        portfolio_value=75000.0,
    )
    positions = [
        Position(
            symbol="AAPL",
            qty=10,
            side="long",
            market_value=2000.0,
            unrealized_pl=150.0,
            avg_entry_price=185.0,
            current_price=200.0,
        )
    ]
    save_snapshot(account, positions, [], broker="alpaca", db_path=db_path)


def _start_server() -> tuple[threading.Thread, str]:
    import uvicorn
    from api.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)

    def run() -> None:
        import asyncio

        asyncio.run(server.serve())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    for _ in range(100):
        if server.started and server.servers:
            break
        time.sleep(0.1)

    port = server.servers[0].sockets[0].getsockname()[1]
    return thread, f"http://127.0.0.1:{port}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test a real LLM provider")
    parser.add_argument(
        "--provider",
        default=os.environ.get("LLM_PROVIDER", "").strip().lower(),
        help="openai | anthropic | deepseek | openrouter | ollama",
    )
    args = parser.parse_args()
    provider = (args.provider or "").strip().lower()

    if not provider or provider == "mock":
        print(
            "Set LLM_PROVIDER (or --provider) to a real provider, e.g. openai or ollama."
        )
        return 1

    os.environ["LLM_PROVIDER"] = provider
    os.environ["REFLECTION_INTERVAL_SECONDS"] = "999999"

    from ai.llm.config import provider_credentials
    from ai.llm.factory import clear_llm_cache, get_llm, get_llm_provider_name
    from config.ai_providers import VALID_PROVIDERS

    if provider not in VALID_PROVIDERS:
        print(f"Unknown provider: {provider}")
        return 1

    creds = provider_credentials(provider)
    if provider != "ollama" and not creds.get("api_key"):
        print(
            f"No API key found for {provider}. "
            "Set it in Settings → AI or via environment variables."
        )
        return 1

    clear_llm_cache()
    _check("provider_active", get_llm_provider_name() == provider)

    from ai.llm.base import LLMMessage

    llm = get_llm()
    text = llm.complete(
        [LLMMessage("user", "Reply with exactly: provider smoke test ok")],
        max_tokens=40,
    )
    _check("llm_complete", len(text.strip()) > 3, text[:80])

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "smoke.db"
        os.environ["TRADING_DB_PATH"] = str(db_path)
        _seed_snapshot(db_path)
        _thread, base = _start_server()

        import httpx

        client = httpx.Client(base_url=base, timeout=120.0)

        research = client.post(
            "/api/research/run",
            json={"topic": "Smoke test market scan", "symbol": "AAPL", "depth": "quick"},
        )
        _check("research_run", research.status_code == 200, research.text[:120])
        _check("research_has_html", bool(research.json().get("html")))

        with client.stream(
            "POST",
            "/api/chat",
            json={
                "messages": [
                    {
                        "id": "smoke-1",
                        "role": "user",
                        "parts": [{"type": "text", "text": "Say hello in one sentence."}],
                    }
                ],
                "mode": "quick",
            },
        ) as chat_resp:
            _check("chat_status", chat_resp.status_code == 200)
            body = "".join(chat_resp.iter_text())
            _check("chat_stream", "data:" in body, body[-120:])

    print(f"\nProvider smoke test passed for {provider}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
