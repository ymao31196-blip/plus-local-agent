"""Regression tests for the opt-in Web Invocation Interface.

Uses a tiny stdlib ASGI test client so the Web API needs no extra test/runtime
dependencies. Tests are also discoverable by pytest.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from fastmcp import FastMCP

from runtime.web_api import register_web_api


class _FakeStore:
    def __init__(self):
        self.submitted = []

    def submit(self, request):
        self.submitted.append(request)
        return {"task_id": "a" * 32, "status": "queued"}

    def cancel(self, task_id):
        return {"task_id": task_id, "status": "cancelled"}

    def get(self, task_id, cursor=None):
        return {
            "task_id": task_id,
            "status": "completed",
            "result": {"tool": "list_directory", "ok": True, "result": {"result": []}},
            "error": None,
            "cursor": cursor,
        }


async def _asgi_request(app, method, url, *, token=None, origin=None, body=None, host="127.0.0.1:8766"):
    parsed = urlsplit(url)
    headers = [(b"host", host.encode())]
    if token is not None:
        headers.append((b"authorization", ("Bearer " + token).encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if body is not None:
        headers.append((b"content-type", b"application/json"))
        body = json.dumps(body).encode()
    else:
        body = b""
    if method == "OPTIONS":
        headers.append((b"access-control-request-method", b"POST"))
        headers.append((b"access-control-request-headers", b"authorization, content-type"))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode(),
        "query_string": parsed.query.encode(),
        "root_path": "",
        "headers": headers,
        "server": ("127.0.0.1", 8766),
        "client": ("127.0.0.1", 12345),
    }
    messages = []
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status = next(m for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    fields = {k.decode(): v.decode() for k, v in status["headers"]}
    return status["status"], fields, raw


class WebApiTests(unittest.TestCase):
    TOKEN = "a-local-web-test-token-with-40-characters"

    def setUp(self):
        self.store = _FakeStore()
        self.mcp = FastMCP("web-api-regression")
        register_web_api(
            self.mcp,
            self.store,
            token=self.TOKEN,
            host_port=8766,
            allowed_tools=frozenset({"list_directory", "read_text", "run_process", "run_powershell"}),
        )
        self.app = self.mcp.http_app(path="/mcp")

    def request(self, method, url, **kwargs):
        return asyncio.run(_asgi_request(self.app, method, url, **kwargs))

    def test_auth_origin_and_host_are_enforced(self):
        code, _, _ = self.request("GET", "/web/v1/tools")
        self.assertEqual(code, 401)
        code, _, _ = self.request("GET", "/web/v1/tools", token=self.TOKEN, origin="https://attacker.example")
        self.assertEqual(code, 403)
        code, _, _ = self.request("GET", "/web/v1/tools", token=self.TOKEN, host="attacker.example:8766")
        self.assertEqual(code, 403)
        code, headers, raw = self.request(
            "GET", "/web/v1/tools", token=self.TOKEN, origin="http://127.0.0.1:8766"
        )
        self.assertEqual(code, 200)
        self.assertEqual(headers["access-control-allow-origin"], "http://127.0.0.1:8766")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn("run_process", [t["name"] for t in json.loads(raw)["tools"]])

    def test_preflight_and_submitted_parameters(self):
        code, _, _ = self.request(
            "OPTIONS", "/web/v1/tasks", origin="http://127.0.0.1:8766"
        )
        self.assertEqual(code, 200)
        payload = {
            "tool": "run_process",
            "arguments": {
                "program": "python",
                "args": ["-c", "print(1 + 1)"],
                "root": "workspace",
            },
        }
        code, _, data = self.request(
            "POST", "/web/v1/tasks", token=self.TOKEN,
            origin="http://127.0.0.1:8766", body=payload,
        )
        self.assertEqual(code, 202)
        self.assertEqual(json.loads(data)["task_id"], "a" * 32)
        self.assertEqual(self.store.submitted[0]["arguments"], payload["arguments"])
        self.assertEqual(self.store.submitted[0]["web_origin"], "http://127.0.0.1:8766")
        code, _, raw = self.request(
            "GET", "/web/v1/tasks/" + "a" * 32 + "?cursor=0", token=self.TOKEN
        )
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["status"], "completed")
        code, _, raw = self.request(
            "POST", "/web/v1/tasks/" + "a" * 32 + "/cancel", token=self.TOKEN
        )
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["status"], "cancelled")

    def test_batch_validation_and_root_policy(self):
        code, _, raw = self.request(
            "POST", "/web/v1/tasks", token=self.TOKEN,
            body={"tool": "read_text", "arguments": {"root": "D", "path": "secret.txt"}},
        )
        self.assertEqual(code, 400)
        self.assertEqual(json.loads(raw)["error"], "InvalidTask")
        code, _, _ = self.request(
            "POST", "/web/v1/tasks", token=self.TOKEN,
            body={"actions": [{"tool": "read_text", "arguments": {"path": "x"}},
                              {"tool": "run_powershell", "arguments": {"command": "Get-Location"}}]},
        )
        self.assertEqual(code, 400)
        self.assertEqual(self.store.submitted, [])
        code, _, _ = self.request(
            "POST", "/web/v1/tasks", token=self.TOKEN,
            body={"actions": [{"tool": "list_directory", "arguments": {"path": "."}},
                              {"tool": "read_text", "arguments": {"path": "note.txt"}}]},
        )
        self.assertEqual(code, 202)
        self.assertEqual(len(self.store.submitted[0]["actions"]), 2)

    def test_artifact_only_bounded_images(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "plot.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nplot-test")
            with patch("runtime.web_api.safe_path", return_value=image):
                code, headers, content = self.request(
                    "GET", "/web/v1/artifact?path=plots/plot.png", token=self.TOKEN
                )
                self.assertEqual(code, 200)
                self.assertEqual(headers["content-type"], "image/png")
                self.assertEqual(content, image.read_bytes())
                code, _, _ = self.request(
                    "GET", "/web/v1/artifact?path=plots/a.svg", token=self.TOKEN
                )
                self.assertEqual(code, 415)
                code, _, _ = self.request(
                    "GET", "/web/v1/artifact?root=D&path=plot.png", token=self.TOKEN
                )
                self.assertEqual(code, 400)

    def test_real_task_store_dispatches_existing_tool(self):
        from runtime.task_store import TaskStore

        with tempfile.TemporaryDirectory() as folder:
            store = TaskStore(db_path=Path(folder) / "tasks.sqlite3")
            try:
                mcp = FastMCP("real-task-web-adapter")
                register_web_api(
                    mcp, store, token=self.TOKEN, host_port=8766,
                    allowed_tools=frozenset({"list_directory"}),
                )
                app = mcp.http_app(path="/mcp")
                status, _, raw = asyncio.run(_asgi_request(
                    app, "POST", "/web/v1/tasks", token=self.TOKEN,
                    body={"tool": "list_directory", "arguments": {"path": ".", "root": "workspace"}},
                ))
                self.assertEqual(status, 202)
                task_id = json.loads(raw)["task_id"]
                store.get(task_id, wait_seconds=5)
                status, _, raw = asyncio.run(_asgi_request(
                    app, "GET", "/web/v1/tasks/" + task_id, token=self.TOKEN
                ))
                self.assertEqual(status, 200)
                task = json.loads(raw)
                self.assertEqual(task["status"], "completed")
                self.assertTrue(task["result"]["ok"])
                self.assertEqual(task["result"]["tool"], "list_directory")
            finally:
                store.close()

    def test_token_config_rejects_short_or_missing(self):
        with self.assertRaisesRegex(ValueError, "PLA_WEB_API_TOKEN"):
            register_web_api(FastMCP("invalid"), self.store, token="short", host_port=8766)

    def test_origin_configuration_is_loopback_only_and_exact(self):
        for origin in (
            "https://attacker.example",
            "http://127.0.0.1.evil:3000",
            "http://127.0.0.1:3000/",
            "file://localhost/page.html",
            "http://127.0.0.1:80",
        ):
            with self.subTest(origin=origin):
                with self.assertRaisesRegex(ValueError, "localhost/127.0.0.1"):
                    register_web_api(
                        FastMCP("invalid-origin"), self.store, token=self.TOKEN,
                        host_port=8766, allowed_origins=frozenset({origin}),
                    )
        register_web_api(
            FastMCP("valid-origin"), self.store, token=self.TOKEN,
            host_port=8766, allowed_origins=frozenset({"http://127.0.0.1:3000"}),
        )


if __name__ == "__main__":
    unittest.main()
