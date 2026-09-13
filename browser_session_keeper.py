"""Independent Browser Session Keeper for PLA v1.3.

This process owns no browser actions. It keeps one reviewed MCP HTTP session open so
Playwright MCP's shared browser context survives PLA HTTP client reconnects.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


PROJECT_ROOT = Path(__file__).resolve().parent
STATE_DIR = PROJECT_ROOT / "state" / "browser_runtime"
READY_PATH = STATE_DIR / "keeper_ready.json"
ENDPOINT = "http://localhost:8931/mcp"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clear_ready() -> None:
    try:
        READY_PATH.unlink()
    except FileNotFoundError:
        pass


def _write_ready() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp = READY_PATH.with_suffix(".tmp")
    temp.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "connected_at": _utcnow_iso(),
                "endpoint": ENDPOINT,
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temp, READY_PATH)


async def main() -> None:
    _clear_ready()
    transport = StreamableHttpTransport(ENDPOINT)
    try:
        async with Client(transport, mode="auto") as client:
            # Complete initialize + tools/list before declaring readiness.
            await client.list_tools()
            _write_ready()
            await asyncio.Event().wait()
    finally:
        _clear_ready()


if __name__ == "__main__":
    asyncio.run(main())
