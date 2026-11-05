"""Fake-IP loopback proxy egress safety checks; Internet-free unit tests."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import ssl
import time

import pytest

from tooling import network_operations as net
from tooling import proxy_dns_transport as proxy
from tooling import local_tools
from capabilities.capability_broker import CapabilityBroker
from capabilities.capability_registry import CapabilityRegistry
from capabilities.core_capabilities import register_core_transaction_capabilities
from mcp_runtime.mcp_client_manager import MCPClientManager
from transactions.transaction_runtime import ActionTransactionStore


@pytest.mark.parametrize("raw", [
    "http://example.com:7897", "http://192.168.1.10:7897",
    "http://127.0.0.1:7897/path", "http://127.0.0.1:7897?x=1",
    "http://user:password@127.0.0.1:7897", "socks5://127.0.0.1:7897",
    "http://localhost:7897", "https://127.0.0.1:7897",
    "http://127.0.0.1:0", "http://127.0.0.1:65536",
])
def test_system_proxy_rejects_untrusted_or_malformed_config(monkeypatch, raw):
    monkeypatch.setattr(proxy.urllib.request, "getproxies", lambda: {"https": raw})
    with pytest.raises(PermissionError):
        proxy.system_loopback_proxy()


def test_loopback_system_proxy_requires_explicit_mode(monkeypatch):
    monkeypatch.setattr(proxy.urllib.request, "getproxies", lambda: {"https": "http://127.0.0.1:7897"})
    def refuse(*args):
        raise AssertionError("Must not silently use proxy")
    monkeypatch.setattr(proxy, "system_loopback_proxy", refuse)
    # Default direct mode must try ordinary public DNS, never system proxy.
    monkeypatch.setattr(net, "_public_addresses", lambda host, port: ("93.184.215.14",))
    class Response:
        status=200
        reason="OK"
        def getheader(self, name): return None
        def close(self): pass
    class Connection:
        def close(self): pass
    monkeypatch.setattr(net, "_open_public",
                        lambda host, path, addr, method, timeout: (Connection(), Response()))
    result = net.http_probe("https://example.com/")
    assert result["transport"] == "direct"


def test_system_proxy_detection_is_loopback_and_sanitized(monkeypatch):
    monkeypatch.setattr(proxy.urllib.request, "getproxies",
                        lambda: {"https": "http://127.0.0.1:7897"})
    chosen = proxy.system_loopback_proxy()
    assert chosen == proxy.LoopbackProxy("127.0.0.1", 7897)


@pytest.mark.parametrize("answer", [
    ["127.0.0.1"], ["169.254.169.254"], ["198.18.0.1"],
    ["10.0.0.7", "8.8.8.8"], ["2001:2::5b"],
])
def test_doh_rejects_private_and_fake_ip_even_when_proxy_is_available(monkeypatch, answer):
    monkeypatch.setattr(proxy, "_doh_question",
                        lambda host, typ, pxy, timeout: answer if typ == "A" else [])
    with pytest.raises(PermissionError, match="non-public"):
        proxy.public_addresses_via_proxy(
            "example.com", proxy.LoopbackProxy("127.0.0.1", 7897),
            time.monotonic() + 5,
        )


def test_doh_accepts_only_global_answers_and_ipv4_preference(monkeypatch):
    def response(host, typ, *_):
        return ["2606:4700::6814:1a88"] if typ == "AAAA" else ["104.20.26.136"]
    monkeypatch.setattr(proxy, "_doh_question", response)
    addresses = proxy.public_addresses_via_proxy(
        "example.org", proxy.LoopbackProxy("127.0.0.1", 7897),
        time.monotonic() + 5,
    )
    assert addresses == ("104.20.26.136", "2606:4700::6814:1a88")


def test_doh_requires_both_responses_before_connection(monkeypatch):
    def fail(host, typ, *_):
        if typ == "AAAA":
            raise OSError("resolver unavailable")
        return ["104.20.26.136"]
    monkeypatch.setattr(proxy, "_doh_question", fail)
    with pytest.raises(OSError, match="resolver unavailable"):
        proxy.public_addresses_via_proxy(
            "example.org", proxy.LoopbackProxy("127.0.0.1", 7897),
            time.monotonic() + 5,
        )


def test_proxy_pinned_tls_uses_original_server_hostname(monkeypatch):
    endpoint=[]
    class Socket:
        def close(self): endpoint.append(("close",))
    raw=Socket()
    monkeypatch.setattr(proxy, "_connect_proxy_tunnel",
                        lambda p, dest, port, timeout: (
                            endpoint.append(("tunnel", dest, port)), raw)[1])
    conn=proxy.PinnedProxyHTTPSConnection(
        "example.org", "104.20.26.136", proxy.LoopbackProxy("127.0.0.1",7897), 5
    )
    conn._context=type("Context", (), {
        "wrap_socket":lambda self,s,server_hostname:(
            endpoint.append(("tls",server_hostname)), s)[1]
    })()
    conn.connect()
    assert endpoint == [("tunnel","104.20.26.136",443), ("tls","example.org")]


def test_proxy_pinned_tls_rejects_private_ip():
    with pytest.raises(PermissionError, match="not public"):
        proxy.PinnedProxyHTTPSConnection(
            "example.org", "127.0.0.1", proxy.LoopbackProxy("127.0.0.1",7897), 5
        )


class Response:
    status=200
    reason="OK"
    def __init__(self,status=200,headers=None,body=b""):
        from io import BytesIO
        self.status=status
        self.headers=headers or {}
        self.body=BytesIO(body)
    def getheader(self,name): return self.headers.get(name)
    def read(self,size): return self.body.read(size)
    def close(self): pass


class Connection:
    def close(self): pass


def test_public_proxy_redirect_revalidates_each_hostname(monkeypatch):
    redirects=[
        Response(302,headers={"Location":"https://safe-two.example/resource"}),
        Response(200)
    ]
    verified=[]
    proxy_mode=proxy.LoopbackProxy("127.0.0.1",7897)
    monkeypatch.setattr(net,"system_loopback_proxy",lambda:proxy_mode)
    def dns(host, px, deadline):
        verified.append(host)
        return ("104.20.26.136",), {"dns_ms": 1, "cache_hit": False, "retries": 0}
    monkeypatch.setattr(net,"resolve_public_via_proxy",dns)
    def open_connection(host,path,address,method,timeout,px):
        assert host in verified
        assert address == "104.20.26.136"
        assert px == proxy_mode
        return Connection(),redirects.pop(0)
    monkeypatch.setattr(net,"_open_proxy_public",open_connection)
    result=net.http_probe("https://safe-one.example/initial",transport="system_loopback_proxy")
    assert result["redirect_count"] == 1
    assert verified == ["safe-one.example","safe-two.example"]


def test_proxy_redirect_to_private_dns_blocked(monkeypatch):
    proxy_mode=proxy.LoopbackProxy("127.0.0.1",7897)
    monkeypatch.setattr(net,"system_loopback_proxy",lambda:proxy_mode)
    def dns(host,px,deadline):
        if host == "safe-two.example":
            raise PermissionError("DoH returned a non-public address")
        return ("104.20.26.136",), {"dns_ms": 1, "cache_hit": False, "retries": 0}
    monkeypatch.setattr(net,"resolve_public_via_proxy",dns)
    called=[]
    def open_connection(host,*args):
        called.append(host)
        return Connection(),Response(302,headers={"Location":"https://safe-two.example/"})
    monkeypatch.setattr(net,"_open_proxy_public",open_connection)
    with pytest.raises(PermissionError,match="non-public"):
        net.http_probe("https://safe-one.example/",transport="system_loopback_proxy")
    assert called == ["safe-one.example"]


def test_proxy_mode_download_requires_broker_confirmation(monkeypatch,tmp_path):
    monkeypatch.setattr(local_tools,"WORKSPACE",tmp_path)
    registry=CapabilityRegistry()
    broker=CapabilityBroker(registry,MCPClientManager(registry))
    store=ActionTransactionStore()
    try:
        register_core_transaction_capabilities(registry,broker,store)
        args={"url":"https://example.org/","destination":"test.html",
              "transport":"system_loopback_proxy"}
        with pytest.raises(PermissionError,match="INVOKE"):
            asyncio.run(broker.invoke("core.http_download",args))
        descriptor=registry.describe("core.http_download")
        assert descriptor["input_schema"]["properties"]["transport"]["enum"]==[
            "direct","system_loopback_proxy"
        ]
    finally:
        store.close()


@pytest.mark.parametrize("value", ["auto", "proxy", "system", "", None, 1])
def test_transport_modes_do_not_auto_fallback(value):
    with pytest.raises(ValueError,match="transport"):
        net._transport(value)
