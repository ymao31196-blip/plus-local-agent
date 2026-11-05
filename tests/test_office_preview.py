import asyncio
import base64

import pytest
from fastmcp import Client
from mcp.types import ImageContent

from providers import office_server as office
from tooling import local_tools


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9GB5AAAAAASUVORK5CYII="
)


def test_preview_returns_image_content_and_enforces_extension(monkeypatch, tmp_path):
    monkeypatch.delenv("PLA_DESKTOP_RUNTIME", raising=False)
    monkeypatch.setattr(local_tools, "safe_path", lambda path, root, access: tmp_path / path)
    (tmp_path / "preview.png").write_bytes(PNG)
    (tmp_path / "preview.txt").write_text("not an image", encoding="utf-8")

    async def verify():
        async with Client(office.create_server(tmp_path)) as client:
            result = await client.call_tool("office_preview_image", {
                "root": "workspace", "path": "preview.png"
            })
            assert result.is_error is False
            assert len(result.content) == 1
            assert isinstance(result.content[0], ImageContent)
            assert result.content[0].mime_type == "image/png"

    asyncio.run(verify())
    with pytest.raises(ValueError, match="Only PNG or JPEG"):
        office._read_authorized_image("workspace", "preview.txt")


def test_preview_limits_file_size(monkeypatch, tmp_path):
    monkeypatch.delenv("PLA_DESKTOP_RUNTIME", raising=False)
    monkeypatch.setattr(local_tools, "safe_path", lambda path, root, access: tmp_path / path)
    (tmp_path / "oversize.png").write_bytes(PNG + b"0" * 5_000_000)
    with pytest.raises(ValueError, match="5 MB"):
        office._read_authorized_image("workspace", "oversize.png")
