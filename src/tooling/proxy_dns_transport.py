"""Public HTTPS via opt-in OS loopback proxy and TLS-verified DoH.

DoH results are cached only after every answer has passed public-IP checks.
Short TTLs, proxy-scoped cache keys, bounded retries and request deadlines.
All CONNECT destinations are pinned public IPs, not user-controlled hostnames.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import http.client
import ipaddress
import json
import socket
import ssl
from threading import Lock
import time
import urllib.request
from urllib.parse import urlencode, urlsplit

from runtime.runtime_context import checkpoint

DOH_HOST = "cloudflare-dns.com"
MAX_DOH_RESPONSE = 16 * 1024
MAX_DOH_ADDRESSES = 24
MAX_QUERY_SECONDS = 4.0
MAX_QUERY_RETRIES = 1
RETRY_DELAY_SECONDS = 0.10
MAX_CACHE_TTL = 30
MAX_CACHE_ENTRIES = 64


@dataclass(frozen=True)
class LoopbackProxy:
    host: str
    port: int


@dataclass(frozen=True)
class DoHAnswer:
    addresses: tuple[str, ...]
    ttl: int


_cache_lock = Lock()
# (hostname, proxy_host, proxy_port) -> (monotonic expiration, addresses)
_dns_cache: OrderedDict[tuple[str, str, int], tuple[float, tuple[str, ...]]] = OrderedDict()


def _cache_get(key: tuple[str, str, int]) -> tuple[str, ...] | None:
    now = time.monotonic()
    with _cache_lock:
        entry = _dns_cache.get(key)
        if entry is None:
            return None
        expires, addresses = entry
        if expires <= now or not all(ipaddress.ip_address(a).is_global for a in addresses):
            _dns_cache.pop(key, None)
            return None
        _dns_cache.move_to_end(key)
        return addresses


def _cache_put(key: tuple[str, str, int], addresses: tuple[str, ...], ttl: int) -> None:
    if not addresses or ttl <= 0:
        return
    if not all(ipaddress.ip_address(a).is_global for a in addresses):
        raise PermissionError("Refusing to cache non-public DoH answer")
    with _cache_lock:
        _dns_cache[key] = (time.monotonic() + min(MAX_CACHE_TTL, ttl), addresses)
        _dns_cache.move_to_end(key)
        while len(_dns_cache) > MAX_CACHE_ENTRIES:
            _dns_cache.popitem(last=False)


def system_loopback_proxy() -> LoopbackProxy:
    """Use only OS-configured, literal loopback, unauthenticated HTTP proxies."""
    raw = urllib.request.getproxies().get("https")
    if not isinstance(raw, str) or not raw:
        raise PermissionError("No system HTTPS proxy is configured")
    parts = urlsplit(raw if "://" in raw else "http://" + raw)
    if parts.scheme.lower() != "http" or parts.username is not None or parts.password is not None:
        raise PermissionError("Only unauthenticated loopback HTTP proxies are allowed")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise PermissionError("Proxy URL must not contain a path, query or fragment")
    try:
        ip = ipaddress.ip_address(parts.hostname or "")
        port = parts.port
    except ValueError as exc:
        raise PermissionError("Proxy must have a literal loopback IP and valid port") from exc
    if not ip.is_loopback or type(port) is not int or not 1 <= port <= 65535:
        raise PermissionError("Proxy must have a literal loopback IP and valid port")
    return LoopbackProxy(str(ip), port)


def _connect_proxy_tunnel(proxy: LoopbackProxy, dest: str, port: int,
                          timeout: float) -> socket.socket:
    """CONNECT only to a vetted endpoint; no ambient Proxy-Authorization."""
    connection = http.client.HTTPConnection(proxy.host, proxy.port, timeout=timeout)
    literal = f"[{dest}]" if ":" in dest else dest
    connection.set_tunnel(literal, port)
    try:
        connection.connect()
        sock = connection.sock
        connection.sock = None
        if sock is None:
            raise OSError("HTTP proxy did not establish a tunnel")
        return sock
    finally:
        connection.close()


class PinnedProxyHTTPSConnection(http.client.HTTPSConnection):
    """CONNECT to public IP, validate TLS for the requested hostname."""

    def __init__(self, hostname: str, public_ip: str, proxy: LoopbackProxy,
                 timeout: float):
        super().__init__(hostname, port=443, timeout=timeout,
                         context=ssl.create_default_context())
        ip = ipaddress.ip_address(public_ip)
        if not ip.is_global:
            raise PermissionError("Proxy tunnel destination is not public")
        self._pinned_ip = str(ip)
        self._proxy = proxy
        self.timing_ms: dict[str, int] = {}

    def connect(self) -> None:
        started = time.monotonic()
        try:
            sock = _connect_proxy_tunnel(self._proxy, self._pinned_ip, 443, self.timeout)
        except (OSError, TimeoutError) as exc:
            exc.network_stage = "proxy_connect"
            raise
        self.timing_ms["proxy_connect"] = round((time.monotonic() - started) * 1000)
        started = time.monotonic()
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException as exc:
            sock.close()
            exc.network_stage = "tls_handshake"
            raise
        self.timing_ms["tls_handshake"] = round((time.monotonic() - started) * 1000)


def _doh_question(hostname: str, query_type: str, proxy: LoopbackProxy,
                  timeout: float) -> DoHAnswer:
    """One bounded DoH exchange; no HTTP redirects or unrestricted egress."""
    checkpoint()
    if query_type not in {"A", "AAAA"}:
        raise ValueError("Unsupported DNS query type")
    deadline = time.monotonic() + timeout
    raw = _connect_proxy_tunnel(proxy, DOH_HOST, 443, timeout)
    try:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("DoH tunnel exhausted query budget")
        raw.settimeout(left)
        tls = ssl.create_default_context().wrap_socket(raw, server_hostname=DOH_HOST)
    except BaseException:
        raw.close()
        raise

    connection = http.client.HTTPConnection(DOH_HOST, port=443, timeout=timeout)
    connection.sock = tls
    try:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("DoH TLS exhausted query budget")
        tls.settimeout(left)
        query = urlencode({"name": hostname, "type": query_type})
        connection.request(
            "GET", "/dns-query?" + query,
            headers={"Host": DOH_HOST, "Accept": "application/dns-json",
                     "Accept-Encoding": "identity", "Connection": "close"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise OSError(f"DoH status: {response.status}")
        body = response.read(MAX_DOH_RESPONSE + 1)
        if len(body) > MAX_DOH_RESPONSE:
            raise ValueError("DoH response exceeded limit")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Malformed DoH JSON response") from exc
        if type(payload) is not dict or payload.get("Status") != 0:
            raise OSError("DoH failed to resolve hostname")
        answers = payload.get("Answer", [])
        if not isinstance(answers, list) or len(answers) > MAX_DOH_ADDRESSES * 3:
            raise ValueError("Invalid DoH answer set")
        kind = 1 if query_type == "A" else 28
        values: list[str] = []
        ttls: list[int] = []
        for item in answers:
            if not isinstance(item, dict):
                raise ValueError("Invalid DNS answer")
            if item.get("type") != kind:
                continue
            value = item.get("data")
            if not isinstance(value, str):
                raise ValueError("Invalid DNS address")
            try:
                ip = ipaddress.ip_address(value)
            except ValueError as exc:
                raise ValueError("Invalid DNS address") from exc
            if ip.version != (4 if kind == 1 else 6):
                raise ValueError("DNS address family mismatch")
            ttl = item.get("TTL")
            # Missing/invalid TTL means the answer can be used once, but is
            # never cached. 0 is honored rather than extended artificially.
            ttl = ttl if type(ttl) is int and ttl >= 0 else 0
            ttls.append(ttl)
            values.append(str(ip))
        return DoHAnswer(tuple(values), min(ttls, default=MAX_CACHE_TTL))
    finally:
        connection.close()


def _retryable(exc: BaseException) -> bool:
    # Certificate failures and malformed/unsafe responses must fail closed.
    return (
        isinstance(exc, ssl.SSLEOFError)
        or (isinstance(exc, (OSError, TimeoutError))
            and not isinstance(exc, (ssl.SSLError, PermissionError)))
    )


def _resolve_question(hostname: str, kind: str, proxy: LoopbackProxy,
                      deadline: float) -> tuple[list[str], int, int]:
    retries = 0
    while True:
        checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("DoH request time budget exceeded")
        try:
            answer = _doh_question(hostname, kind, proxy,
                                   min(MAX_QUERY_SECONDS, remaining))
            if isinstance(answer, DoHAnswer):
                return list(answer.addresses), answer.ttl, retries
            # Existing tests and alternate resolver implementations may return
            # a list. Treat such output as uncached by default.
            return list(answer), 0, retries
        except Exception as exc:
            if not _retryable(exc) or retries >= MAX_QUERY_RETRIES:
                exc.network_stage = "dns"
                raise
            retries += 1
            remaining = deadline - time.monotonic()
            if remaining <= RETRY_DELAY_SECONDS:
                exc.network_stage = "dns"
                raise
            time.sleep(RETRY_DELAY_SECONDS)


def resolve_public_via_proxy(hostname: str, proxy: LoopbackProxy,
                             deadline: float) -> tuple[tuple[str, ...], dict]:
    """Return public addresses and bounded diagnostics; never cache failures."""
    checkpoint()
    key = (hostname.casefold(), proxy.host, proxy.port)
    started = time.monotonic()
    cached = _cache_get(key)
    if cached is not None:
        return cached, {"cache_hit": True, "retries": 0,
                        "dns_ms": round((time.monotonic() - started) * 1000)}
    values = []
    ttls = []
    retries = 0
    try:
        for family in ("A", "AAAA"):
            family_values, ttl, used = _resolve_question(hostname, family, proxy, deadline)
            values.extend(family_values)
            ttls.append(ttl)
            retries += used
        addresses = tuple(sorted(set(values), key=lambda a: (
            ipaddress.ip_address(a).version != 4, a)))
        if not addresses or len(addresses) > MAX_DOH_ADDRESSES:
            raise OSError("DoH returned no bounded set of addresses")
        if not all(ipaddress.ip_address(a).is_global for a in addresses):
            raise PermissionError("DoH returned a non-public address")
        _cache_put(key, addresses, min(ttls))
        return addresses, {"cache_hit": False, "retries": retries,
                           "dns_ms": round((time.monotonic() - started) * 1000)}
    except BaseException as exc:
        if not getattr(exc, "network_stage", None):
            exc.network_stage = "dns"
        raise


def public_addresses_via_proxy(hostname: str, proxy: LoopbackProxy,
                               deadline: float) -> tuple[str, ...]:
    """Compatibility entrypoint for existing callers."""
    return resolve_public_via_proxy(hostname, proxy, deadline)[0]
