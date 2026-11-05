"""One-time, reviewed migration for a user-owned legacy Office Provider.

Dry-run by default. Does not start providers, touch credentials, or update enable
preferences. Only three reviewed Office files are replaced; originals are backed
up and returned if an on-disk write fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

REPOSITORY = Path(__file__).resolve().parents[1]
FILES = (
    "provider_manifests/office.json",
    "provider_specs/office.txt",
    "providers/office_server.py",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migration_preview(components: Path, source: Path = REPOSITORY) -> dict:
    if not components.is_dir() or components.is_symlink():
        raise ValueError("Office component directory is missing or linked")
    changes = []
    for relative in FILES:
        previous = components / relative
        proposed = source / relative
        if not previous.is_file() or previous.is_symlink():
            raise ValueError(f"Expected original Office component file: {relative}")
        if not proposed.is_file() or proposed.is_symlink():
            raise ValueError(f"Missing reviewed source: {relative}")
        changes.append({
            "path": relative,
            "previous_sha256": _digest(previous),
            "proposed_sha256": _digest(proposed),
            "changed": _digest(previous) != _digest(proposed),
        })
    existing = json.loads((components / FILES[0]).read_text(encoding="utf-8"))
    proposed = json.loads((source / FILES[0]).read_text(encoding="utf-8"))
    if existing.get("id") != "office" or proposed.get("id") != "office":
        raise ValueError("Provider ID must remain office")
    if proposed.get("tool_allowlist") is not None or proposed.get("autostart") is not False:
        raise ValueError("Reviewed Office manifest has unexpected trust or startup properties")
    return {"provider_id": "office", "changes": changes,
            "expected_sha256": changes[0]["previous_sha256"], "applied": False}


def migrate(components: Path, source: Path, expected_sha256: str, backup_base: Path) -> dict:
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256.lower()):
        raise ValueError("Expected SHA-256 must contain 64 hexadecimal characters")
    preview = migration_preview(components, source)
    if preview["expected_sha256"] != expected_sha256.lower():
        raise ValueError("Legacy Office manifest changed; preview again before migration")
    if not components.is_relative_to(backup_base.parent.resolve()):
        raise ValueError("Office component and backup must be in the same app data root")
    saved = backup_base / ("office-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    saved.mkdir(parents=True, exist_ok=False)
    staged: list[tuple[Path, Path]] = []
    for relative in FILES:
        old = components / relative
        assert old.is_file() and not old.is_symlink()
        backup = saved / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(old.read_bytes())
        staged.append((old, source / relative))
    if any(_digest(components / entry["path"]) != entry["previous_sha256"] for entry in preview["changes"]):
        raise ValueError("Office files changed while creating backup; no files replaced")
    replaced: list[Path] = []
    try:
        for old, new in staged:
            temp = old.with_name(old.name + ".office-stage-" + uuid4().hex)
            try:
                temp.write_bytes(new.read_bytes())
                os.replace(temp, old)
                replaced.append(old)
            finally:
                temp.unlink(missing_ok=True)
    except Exception:
        for path in reversed(replaced):
            backup = saved / path.relative_to(components)
            temp = path.with_name(path.name + ".office-rollback-" + uuid4().hex)
            temp.write_bytes(backup.read_bytes())
            os.replace(temp, path)
        raise
    return {**preview, "applied": True, "backup_path": str(saved)}


def main():
    parser = argparse.ArgumentParser(description="Review or migrate existing Office MCP component")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256", default="")
    args = parser.parse_args()
    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        raise SystemExit("This migration is only for Windows Desktop installations")
    data = Path(os.environ["LOCALAPPDATA"]) / "io.pla.desktop"
    components = data / "components"
    preview = migration_preview(components, REPOSITORY)
    if args.apply:
        if not args.expected_sha256:
            parser.error("--apply requires --expected-sha256 from a fresh dry run")
        preview = migrate(components.resolve(), REPOSITORY, args.expected_sha256,
                          data.resolve() / "office-migration-backups")
    print(json.dumps(preview, indent=2))


if __name__ == "__main__":
    main()
