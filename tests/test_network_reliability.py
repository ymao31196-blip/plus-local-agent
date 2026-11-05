"""Bounded DoH cache/retry/timing regression tests (no live network)."""
from __future__ import annotations

import io
import ssl
import time

import pytest

from tooling import proxy_dns_transport as proxy
from tooling import network_operations as net
from runtime.failure_diagnostics import exception_diagnostic, failure_projection


@pytest.fixture(autouse=True)
def empty_dns_cache():
    with proxy._cache_lock:
        proxy._dns_cache.clear()
    yield
    with proxy._cache_lock:
        proxy._dns_cache.clear()


@pytest.fixture
def p():
    return proxy.LoopbackProxy("127.0.0.1", 7897)


def _answer(kind: str, ttl: int) -> proxy.DoHAnswer:
    return proxy.DoHAnswer(("104.20.26.136",), ttl) if kind == "A" else (
        proxy.DoHAnswer(("2606:4700::6814:1a88",), ttl)
    )


def test_dns_cache_hit_and_authoritative_ttl_expiry(monkeypatch, p):
    calls = []
    now = [100.0]
    monkeypatch.setattr(proxy.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(proxy, "_doh_question", lambda h, q, x, t: (
        calls.append((h, q)), _answer(q, 8))[1])
    a1, x1 = proxy.resolve_public_via_proxy("example.org", p, 130.0)
    assert len(calls) == 2
    assert x1["cache_hit"] is False
    now[0] += 4.0
    a2, x2 = proxy.resolve_public_via_proxy("example.org", p, 130.0)
    assert a1 == a2 and x2["cache_hit"] is True
    assert len(calls) == 2
    now[0] += 4.1
    a3, x3 = proxy.resolve_public_via_proxy("example.org", p, 130.0)
    assert x3["cache_hit"] is False
    assert len(calls) == 4


def test_cache_ttl_capped_at_thirty_seconds(monkeypatch, p):
    now = [100.]
    monkeypatch.setattr(proxy.time, "monotonic", lambda: now[0])
    calls = []
    monkeypatch.setattr(proxy, "_doh_question", lambda h, q, x, t: (
        calls.append(q), _answer(q, 300))[1])
    proxy.resolve_public_via_proxy("example.org", p, 140)
    now[0] += 30.01
    proxy.resolve_public_via_proxy("example.org", p, 140)
    assert len(calls) == 4


def test_zero_ttl_does_not_cache(monkeypatch, p):
    calls = []
    monkeypatch.setattr(proxy, "_doh_question", lambda h, q, x, t: (
        calls.append(q), _answer(q, 0))[1])
    for _ in range(2):
        _, meta = proxy.resolve_public_via_proxy("example.org", p, time.monotonic()+5)
        assert not meta["cache_hit"]
    assert len(calls) == 4


def test_cache_keys_include_proxy_endpoint(monkeypatch, p):
    calls=[]
    monkeypatch.setattr(proxy, "_doh_question", lambda h, q, x, t: (
        calls.append((x.port, q)), _answer(q, 20))[1])
    first = proxy.resolve_public_via_proxy("example.org", p, time.monotonic()+5)
    second = proxy.resolve_public_via_proxy(
        "example.org", proxy.LoopbackProxy("127.0.0.1", 7898),
        time.monotonic()+5)
    assert not first[1]["cache_hit"]
    assert not second[1]["cache_hit"]
    assert len(calls)==4


def test_no_cache_for_private_doh_addresses(monkeypatch, p):
    calls=[]
    monkeypatch.setattr(proxy, "_doh_question", lambda h, q, x, t: (
        calls.append(q), proxy.DoHAnswer(
            ("169.254.169.254",) if q=="A" else (), 20))[1])
    for _ in range(2):
        with pytest.raises(PermissionError, match="non-public"):
            proxy.resolve_public_via_proxy("blocked.example", p, time.monotonic()+5)
    assert len(calls)==4
    assert not proxy._dns_cache


def test_retry_only_one_transient_failure(monkeypatch, p):
    calls=[]
    def flaky(host, kind, _proxy, timeout):
        calls.append(kind)
        if kind=="A" and calls.count("A")==1:
            raise TimeoutError("test transient")
        return _answer(kind, 5)
    monkeypatch.setattr(proxy, "_doh_question", flaky)
    monkeypatch.setattr(proxy, "RETRY_DELAY_SECONDS", 0)
    _, meta = proxy.resolve_public_via_proxy("example.org", p, time.monotonic()+10)
    assert calls==["A", "A", "AAAA"]
    assert meta["retries"]==1
    assert meta["cache_hit"] is False


def test_two_transient_failures_are_bounded_and_unstored(monkeypatch, p):
    calls=[]
    def timeout(host, kind, _px, duration):
        calls.append((kind, duration))
        raise TimeoutError("DoH")
    monkeypatch.setattr(proxy, "_doh_question", timeout)
    monkeypatch.setattr(proxy, "RETRY_DELAY_SECONDS", 0)
    with pytest.raises(TimeoutError) as err:
        proxy.resolve_public_via_proxy("example.org", p, time.monotonic()+9)
    assert err.value.network_stage == "dns"
    assert [x[0] for x in calls] == ["A", "A"]
    assert all(0 < x[1] <= 4 for x in calls)
    assert not proxy._dns_cache


def test_cert_error_never_retried(monkeypatch, p):
    calls=[]
    def cert_error(*args):
        calls.append(1)
        raise ssl.SSLCertVerificationError("invalid certificate")
    monkeypatch.setattr(proxy, "_doh_question", cert_error)
    with pytest.raises(ssl.SSLError):
        proxy.resolve_public_via_proxy("example.org", p, time.monotonic()+9)
    assert len(calls)==1


def test_cache_entry_limit_and_lru(p):
    for i in range(80):
        proxy._cache_put((f"x{i}.example",p.host,p.port),("104.20.26.136",),15)
    assert len(proxy._dns_cache) == proxy.MAX_CACHE_ENTRIES
    assert proxy._cache_get(("x0.example",p.host,p.port)) is None
    assert proxy._cache_get(("x79.example",p.host,p.port)) == ("104.20.26.136",)


def test_cache_never_publishes_private_address(p):
    with pytest.raises(PermissionError):
        proxy._cache_put(("bad.example",p.host,p.port),("127.0.0.1",),30)
    assert not proxy._dns_cache


def test_cache_success_timing_and_header_timing(monkeypatch, p):
    class Resp:
        status=200
        reason="OK"
        def getheader(self, n): return None
        def close(self): pass
    class Conn:
        timing_ms={"proxy_connect":3,"tls_handshake":4}
        def close(self): pass
    monkeypatch.setattr(net,"system_loopback_proxy",lambda:p)
    monkeypatch.setattr(net,"resolve_public_via_proxy",lambda h,px,d:(
        ("104.20.26.136",), {"dns_ms":7,"cache_hit":True,"retries":0}))
    monkeypatch.setattr(net,"_open_proxy_public",lambda *args:(Conn(),Resp()))
    result=net.http_probe("https://example.org/",transport="system_loopback_proxy")
    timing=result["timings_ms"]
    assert timing["dns"]==7
    assert timing["dns_cache_hits"]==1
    assert timing["proxy_connect"]==3
    assert timing["tls_handshake"]==4
    assert timing["response_headers"]>=0


@pytest.mark.parametrize("stage",[
    "dns","proxy_connect","tls_handshake","response_headers","body"
])
def test_network_stage_safe_log_projection(stage):
    err=TimeoutError("secret-host.example/private")
    err.network_stage=stage
    result=exception_diagnostic(err)
    assert result=={"failure_class":"timeout","network_stage":stage}
    assert failure_projection(result)==result
    assert "secret" not in str(result)


def test_unsafe_network_stage_not_projected():
    assert failure_projection({
        "failure_class":"timeout",
        "network_stage":"url=https://private.example",
    })=={"failure_class":"timeout"}


def test_doh_retry_policy_rejects_permissions_and_certificates():
    assert not proxy._retryable(ssl.SSLCertVerificationError())
    assert not proxy._retryable(PermissionError("denied"))


def test_download_stage_errors_are_not_reported_as_raw_messages(monkeypatch, tmp_path):
    # File-output paths cannot reach a socket until root policy checks succeed.
    # Validate the public log vocabulary independently.
    exc = TimeoutError("download body stalled for secret.example")
    exc.network_stage="body"
    assert failure_projection(exception_diagnostic(exc))["network_stage"]=="body"



def test_retry_once_after_tls_eof_then_success(monkeypatch, p):
    class Response:
        status = 200
        reason = "OK"
        def getheader(self, field): return None
        def close(self): pass
    class Connection:
        timing_ms = {"proxy_connect": 1, "tls_handshake": 2}
        def close(self): pass

    monkeypatch.setattr(net, "system_loopback_proxy", lambda: p)
    monkeypatch.setattr(net, "resolve_public_via_proxy",
        lambda h,px,d:(("104.20.26.136",),{"dns_ms":0,"cache_hit":True,"retries":0}))
    calls=[]
    def flaky(*args):
        calls.append(1)
        if len(calls)==1:
            exc=ssl.SSLEOFError("VPN tunnel interrupted")
            exc.network_stage="tls_handshake"
            raise exc
        return Connection(),Response()
    monkeypatch.setattr(net, "_open_proxy_public", flaky)
    result=net.http_probe("https://example.org/",transport="system_loopback_proxy")
    assert result["http_status"]==200
    assert result["timings_ms"]["connect_retries"]==1
    assert len(calls)==2


def test_cert_validation_fails_without_connect_retry(monkeypatch, p):
    monkeypatch.setattr(net,"system_loopback_proxy",lambda:p)
    monkeypatch.setattr(net,"resolve_public_via_proxy",
        lambda h,px,d:(("104.20.26.136",),{"dns_ms":0,"cache_hit":True,"retries":0}))
    calls=[]
    def bad_cert(*args):
        calls.append(1)
        raise ssl.SSLCertVerificationError("invalid")
    monkeypatch.setattr(net,"_open_proxy_public",bad_cert)
    with pytest.raises(ssl.SSLCertVerificationError):
        net.http_probe("https://example.org/",transport="system_loopback_proxy")
    assert len(calls)==1


def test_two_tls_disconnects_fail_after_one_retry(monkeypatch, p):
    monkeypatch.setattr(net,"system_loopback_proxy",lambda:p)
    monkeypatch.setattr(net,"resolve_public_via_proxy",
        lambda h,px,d:(("104.20.26.136",),{"dns_ms":0,"cache_hit":True,"retries":0}))
    calls=[]
    def fail(*args):
        calls.append(1)
        raise ssl.SSLEOFError("disconnect")
    monkeypatch.setattr(net,"_open_proxy_public",fail)
    with pytest.raises(ssl.SSLEOFError):
        net.http_probe("https://example.org/",transport="system_loopback_proxy")
    assert len(calls)==2


def test_transient_doh_ssl_eof_retries_once(monkeypatch, p):
    calls=[]
    monkeypatch.setattr(proxy, "RETRY_DELAY_SECONDS", 0)
    def recover(host, kind, px, timeout):
        calls.append(kind)
        if len(calls)==1:
            raise ssl.SSLEOFError("temporary EOF")
        return _answer(kind, 8)
    monkeypatch.setattr(proxy, "_doh_question", recover)
    _, meta = proxy.resolve_public_via_proxy(
        "recovery.example", p, time.monotonic()+10)
    assert meta["retries"]==1
    assert calls==["A", "A", "AAAA"]


def test_direct_transport_does_not_retry_network_failures(monkeypatch):
    monkeypatch.setattr(net,"_public_addresses",lambda h,p:("104.20.26.136",))
    calls=[]
    def fail(*args):
        calls.append(1)
        raise TimeoutError("direct connection timeout")
    monkeypatch.setattr(net,"_open_public",fail)
    with pytest.raises(TimeoutError):
        net.http_probe("https://example.org/")
    assert len(calls)==1
