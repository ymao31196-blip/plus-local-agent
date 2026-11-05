"""Bounded public HTTPS and public-network diagnostics for PLA.

Public-address validation is performed before every redirect, and connections
are pinned to a validated DNS answer while TLS verifies the original hostname.
No implicit proxy, HTTP plaintext, private/loopback target, script, or shell.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import os
import re
import socket
import ssl
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from runtime.runtime_context import checkpoint
from tooling.file_operations import _resolve, _is_link
from tooling.proxy_dns_transport import (
    LoopbackProxy, PinnedProxyHTTPSConnection,
    system_loopback_proxy, public_addresses_via_proxy, resolve_public_via_proxy,
)

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_TIMEOUT = 30
BLOCK_SIZE = 64 * 1024
_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$", re.I)
_USER_AGENT = "PLA-PublicHTTP/1.0"


def _host(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError("Invalid hostname")
    if any(ch.isspace() or ord(ch) < 33 or ord(ch) == 127 for ch in value):
        raise ValueError("Invalid hostname")
    host = value.rstrip(".").encode("idna").decode("ascii").lower()
    if not host or ".." in host or not _HOST_RE.fullmatch(host):
        raise ValueError("Invalid hostname")
    # Restrict to qualified public DNS names; internal suffixes do not belong
    # in a public egress tool (even when DNS temporarily returns public IPs).
    if "." not in host or host.endswith((".localhost", ".local", ".internal", ".home.arpa")):
        raise ValueError("Non-public hostname is not supported")
    return host


def _url(value: str) -> tuple[str, str, str]:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError("URL must be a non-empty bounded string")
    if any(ord(ch) < 33 or ord(ch) == 127 for ch in value):
        raise ValueError("URL contains control or whitespace")
    parts = urlsplit(value)
    if parts.scheme.lower() != "https" or not parts.netloc:
        raise ValueError("Only public HTTPS URLs are supported")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL credentials are forbidden")
    if parts.fragment:
        raise ValueError("URL fragments are not supported")
    try:
        port = parts.port
        hostname = parts.hostname
    except ValueError as exc:
        raise ValueError("Invalid HTTPS port") from exc
    if port not in (None, 443):
        raise ValueError("Only HTTPS port 443 is permitted")
    if not hostname:
        raise ValueError("URL hostname is required")
    host = _host(hostname)
    # IP literals are intentionally rejected: the policy is based on DNS names.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP literal URLs are not permitted")
    return host, (parts.path or "/") + (("?" + parts.query) if parts.query else ""), value


def _resolved_addresses(host: str, port: int) -> tuple[str, ...]:
    checkpoint()
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = sorted({record[4][0] for record in records})
    if not addresses:
        raise OSError("No DNS addresses returned")
    return tuple(addresses)


def _is_public(address: str) -> bool:
    return ipaddress.ip_address(address.split("%", 1)[0]).is_global


def _public_addresses(host: str, port: int) -> tuple[str, ...]:
    addresses = _resolved_addresses(host, port)
    if not all(_is_public(address) for address in addresses):
        raise PermissionError("DNS resolved to a non-public address")
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TCP peer is the validated IP, TLS identity is still the DNS hostname."""

    def __init__(self, hostname: str, address: str, timeout: float):
        super().__init__(hostname, port=443, timeout=timeout,
                         context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def _open_public(host: str, path: str, address: str, method: str, timeout: float):
    connection = _PinnedHTTPSConnection(host, address, timeout)
    try:
        connection.request(method, path, headers={
            "Host": host,
            "User-Agent": _USER_AGENT,
            "Accept-Encoding": "identity",
            "Connection": "close",
        })
        return connection, connection.getresponse()
    except BaseException:
        connection.close()
        raise


def _open_proxy_public(host: str, path: str, address: str, method: str,
                       timeout: float, proxy: LoopbackProxy):
    connection = PinnedProxyHTTPSConnection(host, address, proxy, timeout)
    try:
        connection.request(method, path, headers={
            "Host": host,
            "User-Agent": _USER_AGENT,
            "Accept-Encoding": "identity",
            "Connection": "close",
        })
        return connection, connection.getresponse()
    except BaseException:
        connection.close()
        raise


def _transport(value: str) -> str:
    if value not in {"direct", "system_loopback_proxy"}:
        raise ValueError("Unsupported network transport")
    return value


def _retryable_connection_failure(exc: BaseException) -> bool:
    # GET and HEAD are idempotent. A single retry is permitted only before
    # response headers are received; never retry certificate validation errors.
    return isinstance(exc, (
        ssl.SSLEOFError, ConnectionResetError, ConnectionAbortedError,
        BrokenPipeError, TimeoutError,
    )) and not isinstance(exc, ssl.SSLCertVerificationError)


def _positive_timeout(timeout: int) -> int:
    if type(timeout) is not int or not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    return timeout


def _send(url: str, method: str, timeout: int, *,
          max_redirects: int = MAX_REDIRECTS, transport: str = "direct"):
    if type(max_redirects) is not int or not 0 <= max_redirects <= MAX_REDIRECTS:
        raise ValueError("max_redirects must be between 0 and 3")
    deadline = time.monotonic() + _positive_timeout(timeout)
    transport = _transport(transport)
    # Proxy is opt-in per request and must be an OS-configured loopback
    # HTTP proxy; credentials, remote hosts and system PAC are forbidden.
    proxy = system_loopback_proxy() if transport == "system_loopback_proxy" else None
    current = url
    started = time.monotonic()
    diagnostics = {"dns": 0, "proxy_connect": 0, "tls_handshake": 0,
                   "response_headers": 0, "dns_cache_hits": 0,
                   "dns_retries": 0, "connect_retries": 0}
    for hop in range(max_redirects + 1):
        checkpoint()
        host, path, _ = _url(current)
        dns_started = time.monotonic()
        if proxy is not None:
            addresses, info = resolve_public_via_proxy(host, proxy, deadline)
            diagnostics["dns"] += info["dns_ms"]
            diagnostics["dns_retries"] += info["retries"]
            diagnostics["dns_cache_hits"] += int(info["cache_hit"])
        else:
            try:
                addresses = _public_addresses(host, 443)
            except BaseException as exc:
                exc.network_stage = "dns"
                raise
            diagnostics["dns"] += round((time.monotonic() - dns_started) * 1000)
        for attempt in range(2 if proxy is not None else 1):
            checkpoint()
            left = deadline - time.monotonic()
            if left <= 0:
                exc = TimeoutError("HTTP request time budget exceeded")
                exc.network_stage = "response_headers"
                raise exc
            open_started = time.monotonic()
            try:
                if proxy is None:
                    connection, response = _open_public(
                        host, path, addresses[0], method, left,
                    )
                else:
                    connection, response = _open_proxy_public(
                        host, path, addresses[0], method, left, proxy,
                    )
                break
            except BaseException as exc:
                if not getattr(exc, "network_stage", None):
                    exc.network_stage = "response_headers"
                if (attempt != 0 or proxy is None
                        or not _retryable_connection_failure(exc)
                        or deadline - time.monotonic() <= 1):
                    raise
                diagnostics["connect_retries"] += 1
        open_ms = round((time.monotonic() - open_started) * 1000)
        transport_ms = getattr(connection, "timing_ms", {})
        proxy_ms = transport_ms.get("proxy_connect", 0)
        tls_ms = transport_ms.get("tls_handshake", 0)
        diagnostics["proxy_connect"] += proxy_ms
        diagnostics["tls_handshake"] += tls_ms
        diagnostics["response_headers"] += max(0, open_ms - proxy_ms - tls_ms)
        if response.status in {301, 302, 303, 307, 308}:
            location = response.getheader("Location")
            try:
                if not location or hop >= max_redirects:
                    raise ValueError("HTTP redirect limit reached or Location absent")
                next_url = urljoin(current, location)
                _url(next_url)
                current = next_url
            finally:
                response.close()
                connection.close()
            continue
        diagnostics["total_before_body"] = round((time.monotonic() - started) * 1000)
        return connection, response, host, hop, deadline, diagnostics
    raise ValueError("HTTP redirect limit exceeded")


def _metadata(response, host: str, redirects: int) -> dict:
    value = {
        "status": "completed",
        "hostname": host,
        "http_status": int(response.status),
        "reason": str(response.reason)[:120],
        "redirect_count": redirects,
        "content_type": (response.getheader("Content-Type") or "")[:200],
        "content_length": None,
    }
    length = response.getheader("Content-Length")
    if length is not None and length.isdigit():
        value["content_length"] = int(length)
    return value


def http_probe(url: str, method: str = "HEAD", timeout: int = 10,
               max_redirects: int = MAX_REDIRECTS,
               transport: str = "direct") -> dict:
    """Return status and a limited set of headers, never a response body."""
    if method not in {"HEAD", "GET"}:
        raise ValueError("Only GET and HEAD methods are supported")
    connection, response, host, hops, _, timings = _send(
        url, method, timeout, max_redirects=max_redirects, transport=transport,
    )
    try:
        timings["total"] = timings.pop("total_before_body")
        return {**_metadata(response, host, hops), "method": method,
                "transport": transport, "timings_ms": timings}
    finally:
        response.close()
        connection.close()


def http_download(url: str, destination: str, root: str = "workspace",
                  overwrite: bool = False, timeout: int = 20,
                  max_bytes: int = MAX_DOWNLOAD_BYTES,
                  max_redirects: int = MAX_REDIRECTS,
                  transport: str = "direct") -> dict:
    """Download a verified public HTTPS response to an authorized local file."""
    if type(overwrite) is not bool:
        raise TypeError("overwrite must be a boolean")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_DOWNLOAD_BYTES:
        raise ValueError(f"max_bytes must be between 1 and {MAX_DOWNLOAD_BYTES}")
    target = _resolve(destination, root, "write")
    base = _resolve(".", root, "write")
    if target == base:
        raise ValueError("Destination must be a file inside the selected root")
    parent = _resolve(str(target.parent), root, "write")
    if not parent.is_dir() or _is_link(parent):
        raise ValueError("Destination parent must be an existing directory")
    if target.exists():
        if not target.is_file() or _is_link(target) or not overwrite:
            raise FileExistsError("Destination already exists")
    connection, response, host, hops, deadline, timings = _send(
        url, "GET", timeout, max_redirects=max_redirects, transport=transport,
    )
    body_started = time.monotonic()
    temporary = None
    try:
        meta = _metadata(response, host, hops)
        if not 200 <= response.status < 300 or response.status == 204:
            raise ValueError(f"Download HTTP status: {response.status}")
        if meta["content_length"] is not None and meta["content_length"] > max_bytes:
            raise ValueError("HTTP Content-Length exceeds download size limit")
        total = 0
        digest = hashlib.sha256()
        with tempfile.NamedTemporaryFile(
            dir=parent, prefix=".pla-http-", suffix=".part", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            while True:
                checkpoint()
                if time.monotonic() >= deadline:
                    exc = TimeoutError("Download time budget exceeded")
                    exc.network_stage = "body"
                    raise exc
                try:
                    piece = response.read(BLOCK_SIZE)
                except (OSError, TimeoutError) as exc:
                    exc.network_stage = "body"
                    raise
                if not piece:
                    break
                total += len(piece)
                if total > max_bytes:
                    raise ValueError("Download exceeded size limit")
                handle.write(piece)
                digest.update(piece)
        if meta["content_length"] is not None and total != meta["content_length"]:
            raise ValueError("HTTP body length does not match Content-Length")
        # Re-check the root/target immediately before publishing the file.
        if _resolve(destination, root, "write") != target:
            raise PermissionError("Destination path changed")
        if _resolve(str(parent), root, "write") != parent:
            raise PermissionError("Destination parent changed")
        if target.exists() and (not overwrite or not target.is_file() or _is_link(target)):
            raise FileExistsError("Destination already exists")
        if overwrite:
            os.replace(temporary, target)
        else:
            # An atomic fail-if-exists publication without a race-prone exists-check.
            os.link(temporary, target)
        temporary.unlink(missing_ok=True)
        temporary = None
        timings["body"] = round((time.monotonic() - body_started) * 1000)
        timings["total"] = timings.pop("total_before_body") + timings["body"]
        return {
            **meta,
            "destination": str(target.relative_to(base).as_posix()),
            "root": root,
            "bytes_downloaded": total,
            "sha256": digest.hexdigest(),
            "transport": transport,
            "timings_ms": timings,
        }
    finally:
        response.close()
        connection.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def network_diagnose(host: str, kind: str = "dns",
                     port: int | None = None, timeout: int = 5) -> dict:
    """Public DNS lookup or public TCP connectivity test. No LAN/loopback access."""
    if kind not in {"dns", "tcp"}:
        raise ValueError("Only dns or tcp diagnostics are supported")
    timeout = _positive_timeout(timeout)
    hostname = _host(host)
    if kind == "tcp":
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535 for tcp")
    elif port is not None:
        raise ValueError("port is only supported for tcp diagnosis")
    # Avoid accepting literal IPs; all public networking goes through DNS policy.
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("IP literal targets are not permitted")
    if kind == "dns":
        # DNS-only mode does not connect to resolved addresses. Report whether
        # HTTP/TCP egress would reject them (e.g., VPN/TUN Fake-IP), so callers
        # can diagnose the environment without bypassing the network guard.
        addresses = _resolved_addresses(hostname, 443)
        allowed = all(_is_public(address) for address in addresses)
        return {"status": "completed", "kind": kind,
                "hostname": hostname, "addresses": list(addresses),
                "public_egress_allowed": allowed,
                "classification": "public" if allowed else "non_public_dns"}
    addresses = _public_addresses(hostname, port)
    start = time.monotonic()
    deadline = start + timeout
    attempts = []
    for address in addresses[:4]:
        checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with socket.create_connection((address, port), timeout=remaining):
                return {
                    "status": "completed", "kind": kind,
                    "hostname": hostname, "port": port,
                    "connected": True, "address": address,
                    "elapsed_ms": round((time.monotonic() - start) * 1000),
                }
        except (OSError, TimeoutError) as exc:
            attempts.append(type(exc).__name__)
    return {"status": "completed", "kind": kind,
            "hostname": hostname, "port": port,
            "connected": False, "attempts": len(attempts),
            "error_types": attempts,
            "elapsed_ms": round((time.monotonic() - start) * 1000)}
