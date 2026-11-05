#!/usr/bin/env python3
"""Generate Tauri v2 stable updater JSON from an NSIS .exe and matching .exe.sig.

For createUpdaterArtifacts=true the Windows installer itself is the signed
updater artifact. No private signing key is ever accessed by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote

REPO = "ymao31196-blip/plus-local-agent"


def version_parts(version: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("A stable X.Y.Z version is required")
    return tuple(map(int, version.split(".")))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(version: str, installer: Path, signature_path: Path, output: Path) -> dict:
    version_parts(version)
    expected = f"PLA.Desktop_{version}_x64-setup.exe"
    if installer.name != expected or not installer.is_file() or installer.stat().st_size < 1:
        raise ValueError("Unexpected or missing stable Windows NSIS installer")
    if not signature_path.is_file() or signature_path.name != expected + ".sig":
        raise ValueError("Missing Tauri updater signature for this exact installer")
    signature = signature_path.read_text(encoding="utf-8").strip()
    # Copy the official Tauri/minisign data verbatim. This is not a
    # cryptographic check; the updater validates the signature before install.
    if not 40 <= len(signature) <= 4096 or "\x00" in signature:
        raise ValueError("Invalid or empty Tauri signature")
    url = f"https://github.com/{REPO}/releases/download/desktop-v{version}/{quote(expected)}"
    payload = {
        "version": version,
        "platforms": {
            "windows-x86_64": {"url": url, "signature": signature},
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "version": version,
        "installer": expected,
        "sha256": sha256_file(installer),
        "metadata": output.name,
        "signed_updater": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--signature", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = write_manifest(args.version, args.installer, args.signature, args.output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
