"""Read-only stable GitHub Release discovery for PLA Desktop.

A release being newer is not permission to execute its installer. Full
in-app installation remains disabled until a trusted Tauri signing key and
a signed release channel are configured.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

REPOSITORY = "ymao31196-blip/plus-local-agent"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
_MAX_RESPONSE = 512_000


def _stable_version(value: str):
    if not isinstance(value, str):
        return None
    matched = re.fullmatch(r"(?:desktop-v)?(\d+)\.(\d+)\.(\d+)", value)
    if not matched:
        return None
    return tuple(int(number) for number in matched.groups())


def check_stable_release(current_version: str, *, open_url=None):
    current = _stable_version(current_version)
    if current is None:
        return {"status": "unsupported_version", "current_version": current_version}
    request = urllib.request.Request(
        API_URL,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"PLA-Desktop/{current_version}"},
    )
    try:
        if open_url is None:
            open_url = urllib.request.urlopen
        with open_url(request, timeout=8) as response:
            raw = response.read(_MAX_RESPONSE + 1)
            if len(raw) > _MAX_RESPONSE:
                raise ValueError("Release metadata exceeds limit")
        release = json.loads(raw)
    except (OSError, urllib.error.URLError, ValueError, TimeoutError):
        return {"status": "unavailable", "current_version": current_version,
                "message": "无法获取 GitHub 稳定版信息；检查网络或代理后重试"}
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        return {"status": "invalid_release", "current_version": current_version}
    tag = release.get("tag_name")
    latest = _stable_version(tag)
    if latest is None or not isinstance(tag, str) or not tag.startswith("desktop-v"):
        return {"status": "invalid_release", "current_version": current_version}
    latest_version = ".".join(map(str, latest))
    if latest <= current:
        return {"status": "latest", "current_version": current_version,
                "latest_version": latest_version}
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        assets = []
    expected_installer = f"PLA.Desktop_{latest_version}_x64-setup.exe"
    available = any(isinstance(asset, dict) and asset.get("name") == expected_installer
                    for asset in assets)
    return {"status": "available_manual" if available else "pending_installer",
            "current_version": current_version, "latest_version": latest_version,
            "release_url": f"{RELEASES_URL}/tag/{tag}",
            "automatic_install": False}
