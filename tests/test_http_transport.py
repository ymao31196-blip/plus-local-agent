import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

from fastmcp import Client


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _free_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_for_listener(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise AssertionError("HTTP MCP server did not start")


async def _discover(url: str) -> list[str]:
    async with Client(url) as client:
        return [tool.name for tool in await client.list_tools()]


def test_streamable_http_endpoint_serves_tool_catalog():
    port = _free_loopback_port()
    process = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT_ROOT / "server.py"),
            "--http",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--path", "/mcp",
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_listener(port)
        names = asyncio.run(_discover(f"http://127.0.0.1:{port}/mcp"))
    finally:
        process.terminate()
        process.wait(timeout=10)

    assert {
        "diagnose_client", "probe_sampling", "run_agent_task",
        "list_directory", "read_text", "extract_document_text", "write_text", "replace_text",
        "search_text", "run_process", "run_powershell", "apply_patch",
        "execute_actions", "submit_task", "task_result",
    } <= set(names)


def test_tunnel_config_targets_dedicated_loopback_http_endpoint():
    config = (PROJECT_ROOT / "config" / "tunnel.yaml").read_text(encoding="utf-8")
    assert 'listen_addr: "127.0.0.1:18081"' in config
    assert 'url: "http://127.0.0.1:8766/mcp"' in config
    assert "engineering-bridge" not in config.lower()
    assert 'tunnel_id: "' in config
    assert "api_key: \"file:" in config
