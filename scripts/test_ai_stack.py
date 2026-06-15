#!/usr/bin/env python3
"""End-to-end checks for the backend AI stack. Exit 0 when all pass."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LLM_PROVIDER", "mock")


def _seed_snapshot(db_path: Path) -> None:
    from brokers.base import Account, Order, Position
    from db.ai_tables import init_ai_tables
    from db.chat_sessions import init_chat_tables
    from db.sqlite import init_db, save_snapshot

    init_db(db_path)
    init_ai_tables(db_path)
    init_chat_tables(db_path)
    account = Account(
        id="test-acct",
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
    orders = [
        Order(
            id="ord-1",
            symbol="NVDA",
            qty=5,
            side="buy",
            order_type="limit",
            status="open",
            filled_qty=0,
            limit_price=120.0,
        )
    ]
    save_snapshot(account, positions, orders, broker="alpaca", db_path=db_path)


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


def _check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    msg = f"[{status}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    if not ok:
        raise AssertionError(f"{name}: {detail}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        os.environ["TRADING_DB_PATH"] = str(db_path)
        os.environ["LLM_PROVIDER"] = "mock"
        os.environ["REFLECTION_INTERVAL_SECONDS"] = "999999"

        _seed_snapshot(db_path)
        _thread, base = _start_server()

        import httpx

        client = httpx.Client(base_url=base, timeout=30.0)

        from ai.llm.base import LLMMessage
        from ai.llm.factory import get_llm, get_llm_provider_name

        _check("llm_provider", get_llm_provider_name() == "mock")

        providers = client.get("/api/ai/providers")
        _check("ai_providers", providers.status_code == 200)
        provider_ids = {p["id"] for p in providers.json()["providers"]}
        _check(
            "ai_provider_catalog",
            {"openai", "anthropic", "deepseek", "openrouter", "ollama"}.issubset(
                provider_ids
            ),
        )

        text = get_llm().complete([LLMMessage("user", "portfolio insight")])
        _check("llm_complete", len(text) > 10, text[:80])

        with client.stream("GET", "/api/insights/stream") as resp:
            _check("insights_status", resp.status_code == 200)
            events: list[tuple[str, str]] = []
            current_event = ""
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    events.append((current_event, line.split(":", 1)[1].strip()))
            event_types = [e[0] for e in events]
            _check("insights_sse_events", "done" in event_types, str(event_types))
            done_data = next(d for e, d in events if e == "done")
            done_json = json.loads(done_data)
            _check("insights_paragraph", len(done_json.get("text", "")) > 20)

        pref = client.post(
            "/api/memories",
            json={"type": "preference", "content": "Risk 1% per trade"},
        )
        _check("memory_create", pref.status_code == 200)
        lesson = client.post(
            "/api/memories",
            json={"type": "lesson", "content": "Wait for confirmation"},
        )
        _check("memory_lesson", lesson.status_code == 200)
        listed = client.get("/api/memories", params={"type": "preference"})
        _check(
            "memory_list",
            listed.status_code == 200 and len(listed.json()["memories"]) >= 1,
        )

        research = client.post(
            "/api/research/run",
            json={"topic": "AAPL momentum", "symbol": "AAPL"},
        )
        _check("research_run", research.status_code == 200, research.text)
        artifact_id = research.json().get("artifact_id")
        _check("research_artifact", artifact_id is not None)
        artifact = client.get(f"/api/research/{artifact_id}")
        _check(
            "research_get",
            artifact.status_code == 200 and "#" in artifact.json()["markdown"],
        )

        reflection = client.post("/api/reflection/run")
        _check("reflection_run", reflection.status_code == 200)
        _check("reflection_summary", len(reflection.json().get("summary", "")) > 10)

        rule_id = "test-rule-drawdown"
        create = client.post(
            "/api/rules",
            json={
                "id": rule_id,
                "name": "Has positions",
                "enabled": True,
                "conditions": {
                    "all": [
                        {
                            "fact": "position_count",
                            "operator": "greaterThan",
                            "value": 0,
                        }
                    ]
                },
                "action": {"type": "notify", "message": "Positions open"},
            },
        )
        _check("rule_create", create.status_code == 200)
        listed_rules = client.get("/api/rules")
        _check(
            "rule_list",
            any(r["id"] == rule_id for r in listed_rules.json()["rules"]),
        )
        evaluate = client.post(
            "/api/rules/evaluate",
            json={"event": {"type": "test", "facts": {"position_count": 2}}},
        )
        triggered = evaluate.json().get("triggered", [])
        _check("rule_evaluate", len(triggered) >= 1)
        delete = client.delete(f"/api/rules/{rule_id}")
        _check("rule_delete", delete.status_code == 200)

        with client.stream(
            "POST",
            "/api/chat",
            json={
                "messages": [
                    {
                        "id": "u1",
                        "role": "user",
                        "parts": [{"type": "text", "text": "Summarize my portfolio"}],
                    }
                ],
                "mode": "quick",
            },
        ) as chat_resp:
            _check("chat_status", chat_resp.status_code == 200)
            _check(
                "chat_stream_header",
                chat_resp.headers.get("x-vercel-ai-ui-message-stream") == "v1",
            )
            body = "".join(chat_resp.iter_text())
            _check("chat_done", "data: [DONE]" in body, body[-200:])

        session = client.post(
            "/api/chat/sessions", json={"mode": "standard", "title": "Test session"}
        )
        _check("chat_session_create", session.status_code == 200)
        sid = session.json()["id"]

        private = client.post("/api/chat/sessions", json={"mode": "private"})
        _check("chat_session_private", private.status_code == 200)

        listed = client.get("/api/chat/sessions")
        _check("chat_session_list", listed.status_code == 200 and len(listed.json()["sessions"]) >= 2)

        with client.stream(
            "POST",
            "/api/chat",
            json={
                "sessionId": sid,
                "mode": "standard",
                "messages": [
                    {
                        "id": "u2",
                        "role": "user",
                        "parts": [
                            {
                                "type": "text",
                                "text": "I prefer 1% risk per trade on swing setups",
                            }
                        ],
                    }
                ],
            },
        ) as persisted:
            _check("chat_persisted_status", persisted.status_code == 200)
            _ = "".join(persisted.iter_text())

        detail = client.get(f"/api/chat/sessions/{sid}")
        _check(
            "chat_messages_saved",
            detail.status_code == 200 and len(detail.json().get("messages", [])) >= 2,
        )

        memories = client.get("/api/memories", params={"type": "preference"})
        _check(
            "chat_memory_extract",
            memories.status_code == 200,
            str(memories.json()),
        )

        print("\nAll AI stack checks passed.")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
