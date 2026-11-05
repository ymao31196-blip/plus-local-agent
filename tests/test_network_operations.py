"""Public-network functionality: deterministic tests without contacting the Internet."""
from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import os
import socket
from pathlib import Path

import pytest

from capabilities.capability_broker import CapabilityBroker
from capabilities.capability_registry import CapabilityRegistry
from capabilities.core_capabilities import register_core_transaction_capabilities
from mcp_runtime.mcp_client_manager import MCPClientManager
from tooling import local_tools
from tooling import network_operations as net
from tooling.internal_tool_executor import ACTION_LOCAL_TOOLS
from transactions.transaction_runtime import ActionTransactionStore


class FakeResponse:
    def __init__(self, body: bytes = b"", status=200, headers=None):
        self.status = status
        self.reason = "OK"
        self.body = io.BytesIO(body)
        self.headers = headers or {}
        self.closed = False

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, size=-1):
        return self.body.read(size)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    target = tmp_path / "workspace"
    target.mkdir()
    source = tmp_path / "repo"
    source.mkdir()
    monkeypatch.setattr(local_tools, "WORKSPACE", target)
    monkeypatch.setattr(local_tools, "PLA_ROOT", source)
    return target


@pytest.fixture
def responses(monkeypatch):
    pairs = []
    seen = []
    monkeypatch.setattr(net, "_public_addresses",
                        lambda host, port: ("93.184.215.14",))

    def set_responses(*values):
        pairs.clear()
        pairs.extend(values)

    def fake_open(host, path, address, method, timeout):
        seen.append((host, path, address, method, timeout))
        if not pairs:
            raise AssertionError("Unexpected additional HTTP call")
        return FakeConnection(), pairs.pop(0)

    monkeypatch.setattr(net, "_open_public", fake_open)
    return set_responses, seen


@pytest.mark.parametrize("url", [
    "http://example.com/", "https://127.0.0.1/", "https://[::1]/",
    "https://user:pass@example.com/", "https://example.com:8080/",
    "https://example.com/white space", "https://example.com/\r\ninjected",
    "https://example.com/#fragment", "ftp://example.com/file",
    "https://example.com:abc/", "https://example..com/",
])
def test_reject_noncompliant_urls(url):
    with pytest.raises((ValueError, UnicodeError)):
        net._url(url)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.10", "172.16.0.7", "192.168.0.5",
    "169.254.169.254", "::1", "fe80::1234", "::ffff:127.0.0.1",
    "0.0.0.0", "100.64.1.1",
])
def test_reject_private_dns_resolution(monkeypatch, address):
    monkeypatch.setattr(net.socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443)),
    ])
    with pytest.raises(PermissionError, match="non-public"):
        net._public_addresses("example.com", 443)


def test_dns_multi_answer_reports_block_without_connection(monkeypatch):
    monkeypatch.setattr(net.socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.1.1.1", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443)),
    ])
    result = net.network_diagnose("example.com")
    assert result["public_egress_allowed"] is False
    assert result["classification"] == "non_public_dns"
    with pytest.raises(PermissionError):
        net._public_addresses("example.com", 443)


def test_head_and_get_probe_return_metadata_not_body(responses):
    configure, seen = responses
    response = FakeResponse(b"secret body", headers={
        "Content-Type": "application/pdf",
        "Content-Length": "11",
        "Set-Cookie": "private",
    })
    configure(response)
    result = net.http_probe("https://EXAMPLE.com/file.pdf?test=1")
    assert result["hostname"] == "example.com"
    assert result["http_status"] == 200
    assert result["content_length"] == 11
    assert "secret" not in str(result)
    assert "Set-Cookie" not in str(result)
    assert seen[0][:4] == ("example.com", "/file.pdf?test=1",
                           "93.184.215.14", "HEAD")
    assert response.closed
    configure(FakeResponse(status=404))
    assert net.http_probe("https://example.com/x", method="GET")["http_status"] == 404
    assert seen[-1][3] == "GET"


def test_redirect_revalidates_host_and_pins_dns(responses):
    configure, seen = responses
    configure(
        FakeResponse(status=302, headers={"Location": "https://other.org/next"}),
        FakeResponse(body=b"hello", status=200),
    )
    result = net.http_probe("https://example.com/x")
    assert result["hostname"] == "other.org"
    assert result["redirect_count"] == 1
    assert [x[0] for x in seen] == ["example.com", "other.org"]


def test_redirect_to_private_address_is_rejected(responses):
    configure, seen = responses
    configure(FakeResponse(status=302, headers={
        "Location": "https://127.0.0.1/admin",
    }))
    with pytest.raises(ValueError, match="IP literal"):
        net.http_probe("https://example.com/x")
    assert len(seen) == 1


def test_redirect_to_plaintext_rejected(responses):
    configure, _ = responses
    configure(FakeResponse(status=302, headers={"Location": "http://example.com/"}))
    with pytest.raises(ValueError, match="HTTPS"):
        net.http_probe("https://example.com/")


def test_redirect_limit_rejected(responses):
    configure, _ = responses
    configure(FakeResponse(status=301, headers={"Location": "/again"}))
    with pytest.raises(ValueError, match="redirect"):
        net.http_probe("https://example.com/", max_redirects=0)


def test_download_succeeds_with_sha_and_no_tmp_left(sandbox, responses):
    configure, seen = responses
    payload = b"downloaded file"
    configure(FakeResponse(body=payload, headers={
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(payload)),
    }))
    result = net.http_download("https://example.com/ok", "new.bin")
    assert (sandbox / "new.bin").read_bytes() == payload
    assert result["bytes_downloaded"] == len(payload)
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["destination"] == "new.bin"
    assert not list(sandbox.glob(".pla-http-*"))
    assert seen[0][3] == "GET"


def test_download_does_not_clobber_existing(sandbox, responses):
    (sandbox / "exists.txt").write_text("prior")
    configure, seen = responses
    configure(FakeResponse(body=b"new"))
    with pytest.raises(FileExistsError):
        net.http_download("https://example.com/x", "exists.txt")
    assert not seen
    assert (sandbox / "exists.txt").read_text() == "prior"
    net.http_download("https://example.com/x", "exists.txt", overwrite=True)
    assert (sandbox / "exists.txt").read_bytes() == b"new"


def test_download_content_length_limit_before_writing(sandbox, responses):
    configure, _ = responses
    configure(FakeResponse(body=b"hello", headers={"Content-Length": "900"}))
    with pytest.raises(ValueError, match="Content-Length"):
        net.http_download("https://example.com/file", "new.txt", max_bytes=10)
    assert not (sandbox / "new.txt").exists()
    assert not list(sandbox.glob(".pla-http-*"))


def test_download_stream_limit_without_content_length(sandbox, responses):
    configure, _ = responses
    configure(FakeResponse(body=b"hello"*100))
    with pytest.raises(ValueError, match="size limit"):
        net.http_download("https://example.com/file", "new.txt", max_bytes=100)
    assert not (sandbox / "new.txt").exists()
    assert not list(sandbox.glob(".pla-http-*"))


def test_download_fails_on_partial_body(sandbox, responses):
    configure, _ = responses
    configure(FakeResponse(body=b"abc", headers={"Content-Length": "6"}))
    with pytest.raises(ValueError, match="body length"):
        net.http_download("https://example.com/file", "new.txt")
    assert not (sandbox / "new.txt").exists()


def test_download_non_success_http_status_never_writes(sandbox, responses):
    configure, _ = responses
    configure(FakeResponse(body=b"error", status=403))
    with pytest.raises(ValueError, match="403"):
        net.http_download("https://example.com/file", "out")
    assert not (sandbox / "out").exists()


def test_download_rejects_outside_workspace_before_network(sandbox, responses):
    configure, seen = responses
    configure(FakeResponse(body=b"abc"))
    with pytest.raises(ValueError, match="outside workspace"):
        net.http_download("https://example.com/file", "../escaped")
    assert not seen


def test_download_rejects_missing_parent_before_network(sandbox, responses):
    configure, seen = responses
    configure(FakeResponse(body=b"abc"))
    with pytest.raises(ValueError, match="Destination parent"):
        net.http_download("https://example.com/file", "newdir/out")
    assert not seen


@pytest.mark.parametrize(("host","kind","port"), [
    ("localhost", "tcp", 8080),
    ("127.0.0.1", "tcp", 8080),
    ("example.com", "tcp", 0),
    ("example.com", "dns", 443),
])
def test_network_diagnose_invalid_inputs(host, kind, port):
    with pytest.raises(ValueError):
        net.network_diagnose(host, kind=kind, port=port)


def test_dns_result_and_tcp_connectivity(monkeypatch):
    monkeypatch.setattr(net, "_public_addresses",
                        lambda host, port: ("93.184.215.14",))
    monkeypatch.setattr(net, "_resolved_addresses",
                        lambda host, port: ("93.184.215.14",))
    dns_result = net.network_diagnose("example.org")
    assert dns_result["addresses"] == ["93.184.215.14"]
    assert dns_result["public_egress_allowed"] is True

    class FakeSocket:
        def __enter__(self): return self
        def __exit__(self, *args): pass

    observed = []
    monkeypatch.setattr(net.socket, "create_connection",
                        lambda endpoint, timeout: (observed.append(endpoint), FakeSocket())[1])
    data = net.network_diagnose("example.org", kind="tcp", port=443)
    assert data["connected"]
    assert observed == [("93.184.215.14", 443)]


def test_tcp_connection_failure_returns_structured_result(monkeypatch):
    monkeypatch.setattr(net, "_public_addresses",
                        lambda host, port: ("93.184.215.14",))
    def blocked(*args, **kwargs):
        raise OSError("connection refused")
    monkeypatch.setattr(net.socket, "create_connection", blocked)
    data = net.network_diagnose("example.org", kind="tcp", port=443)
    assert data["connected"] is False
    assert data["error_types"] == ["OSError"]


def test_tls_sni_name_is_not_pinned_ip(monkeypatch):
    raw_socket = object()
    fake_ssl = type("SSL", (), {})()
    observed = []
    monkeypatch.setattr(net.socket, "create_connection",
                        lambda target, timeout: (observed.append(("tcp", target)), raw_socket)[1])
    connection = net._PinnedHTTPSConnection("example.org", "93.184.215.14", 5)
    connection._context = type("CTX", (), {"wrap_socket": lambda self, s, server_hostname: (
        observed.append(("tls", server_hostname)), fake_ssl)[1]})()
    connection.connect()
    assert observed == [("tcp", ("93.184.215.14", 443)), ("tls", "example.org")]


def test_core_is_discoverable_and_download_requires_confirmation(sandbox, monkeypatch):
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    broker = CapabilityBroker(registry, manager)
    store = ActionTransactionStore()
    try:
        register_core_transaction_capabilities(registry, broker, store)
        assert registry.describe("core.http_download")["requires_confirmation"] is True
        assert registry.describe("core.http_probe")["requires_confirmation"] is False
        assert registry.describe("core.network_diagnose")["risk_level"] == "read"
        result = registry.search("下载", provider_id="core")
        assert any(item["id"] == "core.http_download" for item in result["capabilities"])
        with pytest.raises(PermissionError, match="INVOKE"):
            asyncio.run(broker.invoke(
                "core.http_download",
                {"url":"https://example.org/file","destination":"out.bin"},
            ))
        assert "http_download" not in ACTION_LOCAL_TOOLS
        assert "http_probe" not in ACTION_LOCAL_TOOLS
    finally:
        store.close()


def test_confirmed_broker_download_writes_file(sandbox, responses):
    configure, seen = responses
    configure(FakeResponse(body=b"from broker", headers={"Content-Length": "11"}))
    registry = CapabilityRegistry()
    manager = MCPClientManager(registry)
    broker = CapabilityBroker(registry, manager)
    store = ActionTransactionStore()
    try:
        register_core_transaction_capabilities(registry, broker, store)
        result = asyncio.run(broker.invoke(
            "core.http_download",
            {"url": "https://example.org/test.bin", "destination": "broker.bin"},
            confirmation="INVOKE",
        ))
        assert result["data"]["bytes_downloaded"] == 11
        assert (sandbox / "broker.bin").read_bytes() == b"from broker"
        assert seen[0][0] == "example.org"
    finally:
        store.close()


def test_broker_probe_without_confirmation(responses):
    configure, seen = responses
    configure(FakeResponse(status=204))
    registry = CapabilityRegistry()
    broker = CapabilityBroker(registry, MCPClientManager(registry))
    store = ActionTransactionStore()
    try:
        register_core_transaction_capabilities(registry, broker, store)
        result = asyncio.run(broker.invoke(
            "core.http_probe", {"url": "https://example.org/check"},
        ))
        assert result["data"]["http_status"] == 204
        assert seen[0][3] == "HEAD"
    finally:
        store.close()


def test_download_rejects_pla_private_path_before_network(sandbox, responses):
    configure, seen = responses
    configure(FakeResponse(body=b"secret"))
    with pytest.raises(ValueError, match="Protected path"):
        net.http_download("https://example.org/file", "config/secret.key", root="pla")
    assert not seen


def test_download_rejects_download_cancellation_before_network(sandbox, responses):
    from runtime.runtime_context import CURRENT, ExecutionContext, TaskCancelled
    configure, seen = responses
    configure(FakeResponse(body=b"bytes"))
    context = ExecutionContext(observer=lambda *args: None)
    context.cancelled.set()
    token = CURRENT.set(context)
    try:
        with pytest.raises(TaskCancelled):
            net.http_download("https://example.org/file", "out.bin")
    finally:
        CURRENT.reset(token)
    assert not seen
    assert not (sandbox / "out.bin").exists()

