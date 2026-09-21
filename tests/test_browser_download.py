import asyncio
import json
from types import SimpleNamespace
from urllib import parse as urllib_parse

import pytest

import browser_download


def test_fixed_metadata_code_accepts_only_semantic_refs():
    code = browser_download._fixed_metadata_code("f2e172")
    assert 'const target = "f2e172";' in code
    assert "browser_run_code_unsafe" not in code

    with pytest.raises(ValueError, match="semantic element reference"):
        browser_download._fixed_metadata_code(
            "e172'); require('child_process').exec('calc'); //"
        )


def test_private_and_loopback_download_targets_are_rejected():
    for url in (
        "http://127.0.0.1/file.pdf",
        "http://[::1]/file.pdf",
        "http://localhost/file.pdf",
    ):
        with pytest.raises(ValueError):
            browser_download._validate_public_http_url(url)


def test_helper_result_parses_metadata_without_exposing_code():
    payload = {
        "url": "https://example.com/file.pdf",
        "referer": "https://example.com/page",
        "userAgent": "Test Browser",
        "cookies": [{"name": "session", "value": "secret"}],
        "suggestedFilename": "paper.pdf",
    }
    result = SimpleNamespace(
        content=[
            SimpleNamespace(
                text=(
                    "### Result\n"
                    + json.dumps(payload)
                    + "\n### Ran Playwright code\n"
                )
            )
        ]
    )

    parsed = browser_download._parse_helper_result(result)

    assert parsed["url"] == payload["url"]
    assert parsed["referer"] == payload["referer"]
    assert parsed["user_agent"] == payload["userAgent"]
    assert parsed["cookies"] == payload["cookies"]
    assert parsed["suggested_filename"] == "paper.pdf"


class _FakeHeaders(dict):
    def get_content_type(self):
        return self.get("Content-Type", "application/octet-stream").split(";", 1)[0]


class _FakeResponse:
    def __init__(
        self,
        data,
        *,
        declared_length=None,
        content_type="application/pdf",
        filename="paper.pdf",
    ):
        self._data = data
        self._offset = 0
        if declared_length is None:
            declared_length = len(data)
        self.headers = _FakeHeaders(
            {
                "Content-Type": content_type,
                "Content-Length": str(declared_length),
                "Content-Disposition": (
                    f'attachment; filename="{filename}"'
                ),
            }
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return "https://files.example.com/paper.pdf"

    def read(self, size=-1):
        if self._offset >= len(self._data):
            return b""
        if size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk


class _FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None

    def open(self, request, timeout):
        self.request = request
        assert timeout == 90
        return self.response


def test_controlled_downloader_reuses_session_headers_and_writes_pdf(
    tmp_path,
    monkeypatch,
):
    payload = b"%PDF-1.4\nbody\n%%EOF\n"
    response = _FakeResponse(payload)
    opener = _FakeOpener(response)

    monkeypatch.setattr(browser_download, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(
        browser_download,
        "_validate_public_http_url",
        lambda url: urllib_parse.urlsplit(url),
    )
    monkeypatch.setattr(
        browser_download.urllib_request,
        "build_opener",
        lambda *_handlers: opener,
    )

    metadata = {
        "url": "https://files.example.com/paper.pdf",
        "referer": "https://example.com/article",
        "user_agent": "Test Browser",
        "cookies": [{"name": "session", "value": "abc123"}],
        "suggested_filename": None,
    }

    path, filename, mime_type, size = browser_download._download_file_sync(
        metadata,
        max_bytes=1024,
    )
    try:
        assert path.read_bytes() == payload
        assert filename == "paper.pdf"
        assert mime_type == "application/pdf"
        assert size == len(payload)
        assert opener.request.get_header("User-agent") == "Test Browser"
        assert opener.request.get_header("Referer") == "https://example.com/article"
        assert opener.request.get_header("Cookie") == "session=abc123"
    finally:
        path.unlink(missing_ok=True)


def test_controlled_downloader_rejects_short_content_length(
    tmp_path,
    monkeypatch,
):
    payload = b"%PDF-1.4\nbody\n%%EOF\n"
    response = _FakeResponse(
        payload,
        declared_length=len(payload) + 10,
    )
    opener = _FakeOpener(response)

    monkeypatch.setattr(browser_download, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(
        browser_download,
        "_validate_public_http_url",
        lambda url: urllib_parse.urlsplit(url),
    )
    monkeypatch.setattr(
        browser_download.urllib_request,
        "build_opener",
        lambda *_handlers: opener,
    )

    metadata = {
        "url": "https://files.example.com/paper.pdf",
        "referer": "",
        "user_agent": "Test Browser",
        "cookies": [],
        "suggested_filename": None,
    }

    with pytest.raises(ValueError, match="Content-Length"):
        browser_download._download_file_sync(
            metadata,
            max_bytes=1024,
        )

    assert list(tmp_path.iterdir()) == []


def test_controlled_downloader_rejects_incomplete_pdf(
    tmp_path,
    monkeypatch,
):
    payload = b"%PDF-1.4\nbody without eof\n"
    response = _FakeResponse(payload)
    opener = _FakeOpener(response)

    monkeypatch.setattr(browser_download, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(
        browser_download,
        "_validate_public_http_url",
        lambda url: urllib_parse.urlsplit(url),
    )
    monkeypatch.setattr(
        browser_download.urllib_request,
        "build_opener",
        lambda *_handlers: opener,
    )

    metadata = {
        "url": "https://files.example.com/paper.pdf",
        "referer": "",
        "user_agent": "Test Browser",
        "cookies": [],
        "suggested_filename": None,
    }

    with pytest.raises(ValueError, match="incomplete PDF"):
        browser_download._download_file_sync(
            metadata,
            max_bytes=1024,
        )

    assert list(tmp_path.iterdir()) == []


def test_controlled_downloader_enforces_size_limit(tmp_path, monkeypatch):
    payload = b"%PDF-1.4\n" + b"x" * 128 + b"%%EOF\n"
    response = _FakeResponse(payload)
    opener = _FakeOpener(response)

    monkeypatch.setattr(browser_download, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(
        browser_download,
        "_validate_public_http_url",
        lambda url: urllib_parse.urlsplit(url),
    )
    monkeypatch.setattr(
        browser_download.urllib_request,
        "build_opener",
        lambda *_handlers: opener,
    )

    metadata = {
        "url": "https://files.example.com/paper.pdf",
        "referer": "",
        "user_agent": "Test Browser",
        "cookies": [],
        "suggested_filename": None,
    }

    with pytest.raises(ValueError, match="max_output_bytes"):
        browser_download._download_file_sync(metadata, max_bytes=32)

    assert list(tmp_path.iterdir()) == []


def test_download_browser_target_uses_only_fixed_hidden_helper(
    tmp_path,
    monkeypatch,
):
    payload = b"%PDF-1.4\nbody\n%%EOF\n"
    helper_payload = {
        "url": "https://example.com/paper.pdf",
        "referer": "https://example.com/article",
        "userAgent": "Test Browser",
        "cookies": [],
        "suggestedFilename": "paper.pdf",
    }

    class FakeManager:
        def __init__(self):
            self.calls = []

        async def call_tool(self, provider_id, remote_name, arguments):
            self.calls.append((provider_id, remote_name, arguments))
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=(
                            "### Result\n"
                            + json.dumps(helper_payload)
                            + "\n### Ran Playwright code\n"
                        )
                    )
                ]
            )

    def fake_download(metadata, *, max_bytes):
        path = tmp_path / "paper.pdf"
        path.write_bytes(payload)
        return path, "paper.pdf", "application/pdf", len(payload)

    monkeypatch.setattr(browser_download, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(browser_download, "_download_file_sync", fake_download)

    import local_tools
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace)

    manager = FakeManager()
    artifact, info = asyncio.run(
        browser_download.download_browser_target(
            manager,
            target="e172",
            policy={
                "max_output_bytes": 1024,
                "max_total_output_bytes": 1024,
                "max_output_artifacts": 1,
                "output_ttl_seconds": 600,
                "allowed_output_mime_types": [],
            },
        )
    )

    assert len(manager.calls) == 1
    provider_id, remote_name, arguments = manager.calls[0]
    assert provider_id == "browser"
    assert remote_name == "browser_run_code_unsafe"
    assert set(arguments) == {"code"}
    assert 'const target = "e172";' in arguments["code"]
    assert artifact["name"] == "paper.pdf"
    assert info["filename"] == "paper.pdf"
    assert not (tmp_path / "paper.pdf").exists()


def test_proxy_mode_allows_public_hostname_with_fake_ip_dns(monkeypatch):
    monkeypatch.setattr(
        browser_download.urllib_request,
        "getproxies",
        lambda: {"https": "http://127.0.0.1:7897"},
    )
    monkeypatch.setattr(
        browser_download.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (None, None, None, "", ("198.18.0.85", 443))
        ],
    )

    parsed = browser_download._validate_public_http_url(
        "https://repository.library.noaa.gov/file.pdf"
    )

    assert parsed.hostname == "repository.library.noaa.gov"


def test_no_proxy_rejects_hostname_resolving_to_non_public_address(monkeypatch):
    monkeypatch.setattr(
        browser_download.urllib_request,
        "getproxies",
        lambda: {},
    )
    monkeypatch.setattr(
        browser_download.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (None, None, None, "", ("192.168.1.10", 443))
        ],
    )

    with pytest.raises(ValueError, match="non-public network address"):
        browser_download._validate_public_http_url(
            "https://example.com/file.pdf"
        )


def test_local_style_hostnames_are_rejected_even_with_proxy(monkeypatch):
    monkeypatch.setattr(
        browser_download.urllib_request,
        "getproxies",
        lambda: {"https": "http://127.0.0.1:7897"},
    )

    for url in (
        "https://printer.local/file.pdf",
        "https://intranet/file.pdf",
        "https://service.internal/file.pdf",
        "https://router.lan/file.pdf",
    ):
        with pytest.raises(ValueError, match="local network hostname"):
            browser_download._validate_public_http_url(url)
