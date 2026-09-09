"""Opt-in, local-only diagnostics for ChatGPT MCP sampling E2E runs."""

from __future__ import annotations

import itertools
import json
import os
import sys
from dataclasses import dataclass
from typing import Any


_REQUEST_SEQUENCE = itertools.count(1)


def enabled() -> bool:
    return os.environ.get("CHATGPT_E2E_DEBUG") == "1"


def protocol_mode(ctx: Any) -> str:
    request_context = getattr(ctx, "request_context", None)
    version = getattr(request_context, "protocol_version", None)
    if not isinstance(version, str):
        return "unknown"
    if version >= "2026-07-28":
        return "mrtr"
    return "backchannel"


@dataclass
class E2EDebugTrace:
    request_sequence: int
    protocol: str
    tool: str

    @classmethod
    def start(cls, ctx: Any, tool: str) -> "E2EDebugTrace":
        trace = cls(next(_REQUEST_SEQUENCE), protocol_mode(ctx), tool)
        trace.emit("request_started")
        return trace

    def emit(self, event: str, **details: Any) -> None:
        if not enabled():
            return
        record = {
            "component": "inner_sampling_model",
            "event": event,
            "request_sequence": self.request_sequence,
            "protocol_mode": self.protocol,
            "outer_mcp_client": "current_mcp_client",
            "tool": self.tool,
            **details,
        }
        print(
            "CHATGPT_E2E " + json.dumps(record, ensure_ascii=False, default=str),
            file=sys.stderr,
            flush=True,
        )
