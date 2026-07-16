"""Minimal FastAPI server exposing ONLY the orderflow router.

Used by frontend/scripts/screenshot_orderflow.mjs for headless screenshot
testing.  Does NOT import api.main — avoids the heavy lifespan (broker
polling, db init, etc.) that would fail without a full env.

Run from the repo root:
    PYTHONIOENCODING=utf-8 python scripts/minimal_orderflow_server.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from api.orderflow_routes import router

app = FastAPI(title="Minimal OrderFlow Dev Server")

# Allow requests from the Next.js dev server (port 3001) and build server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("ORDERFLOW_SERVER_PORT", "8000"))
    host = os.environ.get("ORDERFLOW_SERVER_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port, log_level="info")
