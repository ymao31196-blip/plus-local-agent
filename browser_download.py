"""Narrow browser-session download capability for PLA.

The browser provider is used only to resolve a semantic target and obtain the
session metadata required for an HTTP download. Arbitrary Playwright code is
never accepted from the caller. The file transfer itself is performed by PLA
under explicit URL, size, and Artifact Plane policy checks.
"""

from __future__ import annotations

import asyncio
from email.message import Message
import ipaddress
import json
import mimetypes
from pathlib import Path
import re
import socket
import tempfile
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from artifact_policy import normalize_artifact_policy
from artifact_runtime import ArtifactInvocation
from capability_models import CapabilityDescriptor


PROVIDER_ID = "browser"
CAPABILITY_ID = "browser.download"
REMOTE_HELPER_TOOL = "browser_run_code_unsafe"
PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = PROJECT_ROOT / "state" / "browser"
_TARGET_RE = re.compile(r"^(?:f\d+)?e\d+$")
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def browser_download_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=CAPABILITY_ID,
        provider_id=PROVIDER_ID,
        remote_name="pla_browser_download",
        title="Download Browser File",
        description=(
            "Download a file referenced by one semantic browser element while "
            "reusing the managed browser session. Use a target reference from "
            "browser.inspect. Native browser download clicks are avoided."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "pattern": r"^(?:f\d+)?e\d+$",
                    "description": (
                        "Exact semantic element reference from browser.inspect, "
                        "for example e172 or f1e42."
                    ),
                }
            },
            "required": ["target"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        artifact_outputs=True,
        artifact_contract={
            "policy": {
                "max_output_bytes": 67_108_864,
                "max_total_output_bytes": 67_108_864,
                "max_output_artifacts": 1,
                "output_ttl_seconds": 600,
                "allowed_output_mime_types": [],
            }
        },
        risk_level="write_local",
        requires_confirmation=False,
        requires_transaction=False,
        tags=("browser", "download", "artifact", "session"),
    )


def register_browser_download_extension(manager: Any) -> None:
    manager.add_capability_extension(PROVIDER_ID, browser_download_descriptor())


def _fixed_metadata_code(target: str) -> str:
    if not _TARGET_RE.fullmatch(target):
        raise ValueError("Browser download target must be a semantic element reference")
    target_literal = json.dumps(target)
    return f"""async (page) => {{
  const target = {target_literal};
  const element = page.locator('aria-ref=' + target);
  await element.waitFor({{ state: 'attached' }});
  const resolved = await element.evaluate(node => {{
    const direct =
      node.getAttribute('data-file-url') ||
      node.getAttribute('href') ||
      node.getAttribute('formaction');
    const anchor = node.closest('a[href]');
    const rawUrl =
      direct ||
      (anchor ? anchor.getAttribute('href') : null) ||
      (node.form && node.form.action ? node.form.action : null);
    if (!rawUrl)
      return null;
    const url = new URL(rawUrl, document.baseURI);
    const suggested =
      node.getAttribute('download') ||
      (anchor ? anchor.getAttribute('download') : null) ||
      null;
    return {{
      url: url.href,
      protocol: url.protocol,
      suggestedFilename: suggested
    }};
  }});
  if (!resolved)
    throw new Error('No direct download URL found on target element');
  if (!['http:', 'https:'].includes(resolved.protocol))
    throw new Error('Resolved download URL must use http or https');
  const cookies = await page.context().cookies(resolved.url);
  const userAgent = await page.evaluate(() => navigator.userAgent);
  return {{
    url: resolved.url,
    suggestedFilename: resolved.suggestedFilename,
    referer: page.url(),
    userAgent,
    cookies: cookies.map(cookie => ({{
      name: cookie.name,
      value: cookie.value
    }}))
  }};
}}"""


def _text_content(result: Any) -> str:
    fragments: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            fragments.append(text)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            fragments.append(item["text"])
    return "\n".join(fragments)


def _parse_helper_result(result: Any) -> dict[str, Any]:
    text = _text_content(result)
    marker = "### Result\n"
    start = text.find(marker)
    if start < 0:
        raise ValueError("Browser helper did not return download metadata")
    payload = text[start + len(marker):].lstrip()
    try:
        value, _end = json.JSONDecoder().raw_decode(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Browser helper returned invalid download metadata") from exc
    if not isinstance(value, dict):
        raise ValueError("Browser helper returned invalid download metadata")

    url = value.get("url")
    referer = value.get("referer")
    user_agent = value.get("userAgent")
    cookies = value.get("cookies", [])
    if not isinstance(url, str) or not url:
        raise ValueError("Browser helper did not resolve a download URL")
    if not isinstance(referer, str):
        referer = ""
    if not isinstance(user_agent, str) or not user_agent:
        raise ValueError("Browser helper did not return a user agent")
    if not isinstance(cookies, list):
        raise ValueError("Browser helper returned invalid cookie metadata")
    for cookie in cookies:
        if (
            not isinstance(cookie, dict)
            or not isinstance(cookie.get("name"), str)
            or not isinstance(cookie.get("value"), str)
        ):
            raise ValueError("Browser helper returned invalid cookie metadata")
    suggested = value.get("suggestedFilename")
    if suggested is not None and not isinstance(suggested, str):
        suggested = None
    return {
        "url": url,
        "referer": referer,
        "user_agent": user_agent,
        "cookies": cookies,
        "suggested_filename": suggested,
    }


def _validate_public_http_url(url: str) -> urllib_parse.SplitResult:
    parsed = urllib_parse.urlsplit(url)
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise ValueError("Download URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Download URL must not contain embedded credentials")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Download URL is missing a hostname")
    normalized_host = hostname.rstrip(".").casefold()

    if (
        normalized_host == "localhost"
        or "." not in normalized_host
        or normalized_host.endswith(
            (".localhost", ".local", ".lan", ".internal", ".home", ".home.arpa")
        )
    ):
        raise ValueError("Download URL may not target a local network hostname")

    try:
        literal_ip = ipaddress.ip_address(
            normalized_host.split("%", 1)[0]
        )
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if not literal_ip.is_global:
            raise ValueError(
                "Download URL may not target a non-public network address"
            )
        return parsed

    proxies = urllib_request.getproxies()
    proxy_configured = bool(proxies.get(scheme))
    if proxy_configured:
        # Local/TUN proxies commonly return synthetic benchmark-range addresses
        # (for example 198.18.0.0/15) for legitimate public hostnames. The
        # request is sent to the configured proxy rather than directly to that
        # synthetic address, so local DNS publicness is not authoritative here.
        return parsed

    try:
        addresses = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ValueError("Download hostname could not be resolved") from exc

    resolved: set[str] = {
        str(item[4][0]).split("%", 1)[0]
        for item in addresses
        if item and len(item) >= 5 and item[4]
    }
    if not resolved:
        raise ValueError("Download hostname did not resolve to an address")
    for raw_ip in resolved:
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            raise ValueError("Download hostname resolved to an invalid address") from exc
        if not ip.is_global:
            raise ValueError(
                "Download URL resolved to a non-public network address"
            )
    return parsed


class _SafeRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validated = _validate_public_http_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        original_host = urllib_parse.urlsplit(req.full_url).hostname
        if validated.hostname != original_host:
            for header in ("Cookie", "Authorization", "Proxy-Authorization"):
                redirected.remove_header(header)
        return redirected


def _safe_filename(value: str | None, fallback: str = "download.bin") -> str:
    name = (value or "").replace("\\", "/")
    name = Path(name).name
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip().rstrip(". ")
    if not name:
        name = fallback
    stem = Path(name).stem.upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        name = "_" + name
    return name[:240]


def _content_disposition_filename(header: str | None) -> str | None:
    if not header:
        return None
    message = Message()
    message["content-disposition"] = header
    filename = message.get_filename()
    return filename if isinstance(filename, str) and filename else None


def _choose_filename(
    headers: Any,
    final_url: str,
    suggested: str | None,
) -> str:
    from_header = _content_disposition_filename(
        headers.get("Content-Disposition")
    )
    from_url = Path(
        urllib_parse.unquote(urllib_parse.urlsplit(final_url).path)
    ).name
    return _safe_filename(from_header or suggested or from_url or None)


def _download_file_sync(
    metadata: dict[str, Any],
    *,
    max_bytes: int,
) -> tuple[Path, str, str, int]:
    initial = _validate_public_http_url(metadata["url"])
    headers = {
        "User-Agent": metadata["user_agent"],
        "Accept": "*/*",
    }
    if metadata["referer"]:
        headers["Referer"] = metadata["referer"]
    if metadata["cookies"]:
        headers["Cookie"] = "; ".join(
            f'{cookie["name"]}={cookie["value"]}'
            for cookie in metadata["cookies"]
        )

    request = urllib_request.Request(metadata["url"], headers=headers)
    opener = urllib_request.build_opener(_SafeRedirectHandler())

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with opener.open(request, timeout=90) as response:
            final_url = response.geturl()
            final = _validate_public_http_url(final_url)
            if final.hostname != initial.hostname:
                # Redirect handler strips cookies for cross-host redirects. This
                # check documents the intended session-boundary behavior.
                pass

            declared: int | None = None
            raw_length = response.headers.get("Content-Length")
            if raw_length:
                try:
                    declared = int(raw_length)
                except ValueError:
                    declared = None
                if declared is not None and declared > max_bytes:
                    raise ValueError("Browser download exceeds max_output_bytes")

            filename = _choose_filename(
                response.headers,
                final_url,
                metadata.get("suggested_filename"),
            )
            suffix = Path(filename).suffix[:16]
            fd, temp_name = tempfile.mkstemp(
                prefix="pla-browser-download-",
                suffix=suffix,
                dir=OUTPUT_ROOT,
            )
            temporary = Path(temp_name)
            total = 0
            with open(fd, "wb", closefd=True) as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(
                            "Browser download exceeds max_output_bytes"
                        )
                    handle.write(chunk)

            if total <= 0:
                raise ValueError("Browser download returned an empty file")
            if declared is not None and total != declared:
                raise ValueError(
                    "Browser download ended before Content-Length was satisfied"
                )

            mime_type = (
                response.headers.get_content_type()
                if hasattr(response.headers, "get_content_type")
                else None
            )
            if not isinstance(mime_type, str) or "/" not in mime_type:
                mime_type = (
                    mimetypes.guess_type(filename)[0]
                    or "application/octet-stream"
                )

        with temporary.open("rb") as handle:
            first_five = handle.read(5)
            handle.seek(max(0, total - 8192))
            tail = handle.read()

        pdf_expected = (
            mime_type.casefold() == "application/pdf"
            or Path(filename).suffix.casefold() == ".pdf"
            or first_five == b"%PDF-"
        )
        if pdf_expected:
            if first_five != b"%PDF-" or b"%%EOF" not in tail:
                raise ValueError("Browser download produced an incomplete PDF")
            mime_type = "application/pdf"

        return temporary, filename, mime_type, total
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


async def download_browser_target(
    manager: Any,
    *,
    target: str,
    policy: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized_policy = normalize_artifact_policy(policy)
    code = _fixed_metadata_code(target)
    result = await manager.call_tool(
        PROVIDER_ID,
        REMOTE_HELPER_TOOL,
        {"code": code},
    )
    metadata = _parse_helper_result(result)

    max_bytes = int(normalized_policy["max_output_bytes"])
    temporary, filename, mime_type, size = await asyncio.to_thread(
        _download_file_sync,
        metadata,
        max_bytes=max_bytes,
    )
    with ArtifactInvocation(
        PROVIDER_ID,
        CAPABILITY_ID,
        policy=normalized_policy,
    ) as invocation:
        artifact = invocation.import_discovered_output(
            temporary,
            OUTPUT_ROOT,
            name=filename,
            mime_type=mime_type,
            remove_source=True,
        )

    host = urllib_parse.urlsplit(metadata["url"]).hostname or ""
    return artifact, {
        "filename": filename,
        "mime_type": mime_type,
        "size": size,
        "source_host": host,
    }
