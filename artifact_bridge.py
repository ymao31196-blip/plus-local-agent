"""Immutable Artifact Bridge and store governance for PLA.

Artifacts are immutable snapshots of files inside named PLA roots. Metadata is
persisted beside each payload so artifacts survive PLA HTTP restarts.
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import local_tools
from artifact_policy import normalize_mime_type


DEFAULT_TTL_SECONDS = 600
MAX_TTL_SECONDS = 3600
DEFAULT_CHUNK_BYTES = 6_000_000
MAX_CHUNK_BYTES = 6_500_000
STORE_DIRNAME = ".artifact_bridge"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _store_root() -> Path:
    root = local_tools.WORKSPACE / STORE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _artifact_dir(artifact_id: str) -> Path:
    if not artifact_id or any(
        ch not in
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for ch in artifact_id
    ):
        raise ValueError("Invalid artifact_id")
    return _store_root() / artifact_id


def _metadata_path(artifact_id: str) -> Path:
    return _artifact_dir(artifact_id) / "metadata.json"


def _load_metadata(artifact_id: str) -> dict[str, Any]:
    path = _metadata_path(artifact_id)
    if not path.is_file():
        raise ValueError("Artifact not found or revoked")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Artifact metadata is invalid") from exc
    if value.get("artifact_id") != artifact_id:
        raise ValueError("Artifact metadata does not match artifact_id")
    return value


def _expiry(metadata: dict[str, Any]) -> datetime:
    try:
        expires_at = datetime.fromisoformat(metadata["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Artifact expiry metadata is invalid") from exc
    if expires_at.tzinfo is None:
        raise ValueError("Artifact expiry metadata is invalid")
    return expires_at


def _require_live(metadata: dict[str, Any]) -> None:
    if _utcnow() >= _expiry(metadata):
        raise ValueError("Artifact has expired")


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with source.open("rb") as src, tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        ) as tmp:
            shutil.copyfileobj(src, tmp, length=1024 * 1024)
            tmp.flush()
            os.fsync(tmp.fileno())
            temporary_name = tmp.name
        os.replace(temporary_name, destination)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _write_metadata(metadata: dict[str, Any]) -> None:
    local_tools._atomic_write_text(
        _metadata_path(metadata["artifact_id"]),
        json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def export_artifact(
    path: str,
    root: str = "workspace",
    mime_type: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    if (
        type(ttl_seconds) is not int
        or not 1 <= ttl_seconds <= MAX_TTL_SECONDS
    ):
        raise ValueError(
            f"ttl_seconds must be between 1 and {MAX_TTL_SECONDS}"
        )

    source = local_tools.safe_path(path, root, "read")
    if not source.exists():
        raise ValueError(f"File does not exist: {path}")
    if not source.is_file():
        raise ValueError(f"Not a file: {path}")

    artifact_id = secrets.token_urlsafe(24)
    artifact_dir = _artifact_dir(artifact_id)
    artifact_dir.mkdir(parents=False, exist_ok=False)
    snapshot = artifact_dir / "payload"

    try:
        _atomic_copy(source, snapshot)
        sha256 = local_tools._file_sha256(snapshot)
        stat = snapshot.stat()
        detected_mime = normalize_mime_type(
            mime_type
            or mimetypes.guess_type(source.name)[0]
            or "application/octet-stream"
        )
        created_at = _utcnow()
        expires_at = created_at + timedelta(seconds=ttl_seconds)
        metadata = {
            "artifact_id": artifact_id,
            "name": source.name,
            "mime_type": detected_mime,
            "size": stat.st_size,
            "sha256": sha256,
            "source_root": root,
            "source_path": local_tools.root_policy()
            .resolve(root, str(source))
            .relative,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        _write_metadata(metadata)
        return metadata
    except Exception:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise


def normalize_artifact_id(reference: str) -> str:
    if not isinstance(reference, str) or not reference:
        raise ValueError(
            "Artifact reference must be a non-empty string"
        )
    prefix = "artifact://bridge/"
    artifact_id = (
        reference[len(prefix):]
        if reference.startswith(prefix)
        else reference
    )
    if "/" in artifact_id:
        raise ValueError(
            "Chunk resource references are not valid artifact inputs"
        )
    _artifact_dir(artifact_id)
    return artifact_id


def artifact_metadata(artifact_id: str) -> dict[str, Any]:
    artifact_id = normalize_artifact_id(artifact_id)
    metadata = _load_metadata(artifact_id)
    _require_live(metadata)
    return metadata


def _validated_snapshot(
    artifact_id: str,
    *,
    require_live: bool = True,
) -> tuple[dict[str, Any], Path]:
    artifact_id = normalize_artifact_id(artifact_id)
    metadata = _load_metadata(artifact_id)
    if require_live:
        _require_live(metadata)

    snapshot = _artifact_dir(artifact_id) / "payload"
    if not snapshot.is_file():
        raise ValueError("Artifact payload is missing")
    stat = snapshot.stat()
    expected_size = metadata.get("size")
    if type(expected_size) is not int or expected_size < 0:
        raise ValueError("Artifact size metadata is invalid")
    if stat.st_size != expected_size:
        raise ValueError("Artifact size integrity check failed")
    expected_sha256 = metadata.get("sha256")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
    ):
        raise ValueError("Artifact SHA-256 metadata is invalid")
    actual_sha256 = local_tools._file_sha256(snapshot)
    if actual_sha256 != expected_sha256:
        raise ValueError("Artifact integrity check failed")
    mime_type = metadata.get("mime_type")
    if not isinstance(mime_type, str):
        raise ValueError("Artifact MIME metadata is invalid")
    normalize_mime_type(mime_type)
    return metadata, snapshot


def artifact_snapshot_path(
    artifact_id: str,
) -> tuple[dict[str, Any], Path]:
    """Return validated metadata and immutable payload path."""
    return _validated_snapshot(artifact_id)


def read_artifact_bytes(artifact_id: str) -> bytes:
    _metadata, snapshot = _validated_snapshot(artifact_id)
    return snapshot.read_bytes()


def materialize_artifact(
    artifact_id: str,
    destination_path: str,
    root: str = "workspace",
    overwrite: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Persist one live artifact into a writable PLA root.

    Existing destinations are protected by default. Replacing an existing file
    requires both ``overwrite=True`` and the current destination SHA-256 so the
    write remains guarded by PLA's optimistic-concurrency model.
    """
    metadata, snapshot = _validated_snapshot(artifact_id)
    if not isinstance(destination_path, str) or not destination_path.strip():
        raise ValueError("destination_path must be a non-empty string")

    target = local_tools.safe_path(destination_path, root, "write")
    artifact_store = _store_root().resolve()
    try:
        target.resolve().relative_to(artifact_store)
    except ValueError:
        pass
    else:
        raise ValueError("Destination cannot be inside the artifact store")

    existed = target.exists()
    previous_sha256: str | None = None
    if existed:
        if not target.is_file():
            raise ValueError(f"Destination is not a file: {destination_path}")
        previous_sha256 = local_tools._file_sha256(target)
        if not overwrite:
            raise FileExistsError(
                "Destination already exists; set overwrite=True to replace it"
            )
        if expected_sha256 is None:
            raise PermissionError(
                "Replacing an existing destination requires expected_sha256"
            )
        local_tools._validate_expected_sha256(
            target, expected_sha256, root,
        )
    elif expected_sha256 is not None:
        local_tools._validate_expected_sha256(
            target, expected_sha256, root,
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with snapshot.open("rb") as src, tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        ) as tmp:
            shutil.copyfileobj(src, tmp, length=1024 * 1024)
            tmp.flush()
            os.fsync(tmp.fileno())
            temporary_name = tmp.name

        temporary_path = Path(temporary_name)
        if temporary_path.stat().st_size != metadata["size"]:
            raise IOError("Materialized artifact size verification failed")
        temporary_sha256 = local_tools._file_sha256(temporary_path)
        if temporary_sha256 != metadata["sha256"]:
            raise IOError("Materialized artifact SHA-256 verification failed")
        os.replace(temporary_path, target)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)

    relative = local_tools.root_policy().resolve(
        root, str(target), "read",
    ).relative
    return {
        "artifact_id": metadata["artifact_id"],
        "artifact_name": metadata["name"],
        "root": root,
        "path": relative,
        "mime_type": metadata["mime_type"],
        "size": metadata["size"],
        "sha256": metadata["sha256"],
        "overwritten": existed,
        "previous_sha256": previous_sha256,
    }


def import_internal_artifact(
    source: Path,
    *,
    name: str | None = None,
    mime_type: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    source_provider: str | None = None,
    source_capability: str | None = None,
    parent_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    source = source.resolve()
    workspace = local_tools.WORKSPACE.resolve()
    try:
        relative = source.relative_to(workspace).as_posix()
    except ValueError as exc:
        raise ValueError(
            "Provider output is outside the PLA workspace"
        ) from exc

    normalized_parents: list[str] = []
    provenance_parents: list[dict[str, Any]] = []
    for reference in parent_artifacts or []:
        parent_id = normalize_artifact_id(reference)
        if parent_id in normalized_parents:
            continue
        parent_metadata, _parent_snapshot = _validated_snapshot(
            parent_id
        )
        normalized_parents.append(parent_id)
        provenance_parents.append({
            "artifact_id": parent_id,
            "sha256": parent_metadata["sha256"],
            "size": parent_metadata["size"],
            "name": parent_metadata["name"],
            "mime_type": parent_metadata["mime_type"],
        })

    metadata = export_artifact(
        relative,
        "workspace",
        mime_type,
        ttl_seconds,
    )
    metadata = dict(metadata)
    if name is not None:
        safe_name = Path(name).name
        if not safe_name:
            raise ValueError(
                "Artifact name must be a valid file name"
            )
        metadata["name"] = safe_name
    metadata["source_provider"] = source_provider
    metadata["source_capability"] = source_capability
    metadata["parent_artifacts"] = normalized_parents
    metadata["provenance"] = {
        "source_provider": source_provider,
        "source_capability": source_capability,
        "parents": provenance_parents,
    }
    _write_metadata(metadata)
    return metadata


def prepare_chunked_artifact(
    path: str,
    root: str = "workspace",
    mime_type: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> dict[str, Any]:
    if (
        type(chunk_bytes) is not int
        or not 1 <= chunk_bytes <= MAX_CHUNK_BYTES
    ):
        raise ValueError(
            f"chunk_bytes must be between 1 and {MAX_CHUNK_BYTES}"
        )
    metadata = export_artifact(
        path,
        root,
        mime_type,
        ttl_seconds,
    )
    part_count = (
        metadata["size"] + chunk_bytes - 1
    ) // chunk_bytes
    metadata = dict(metadata)
    metadata["chunk_bytes"] = chunk_bytes
    metadata["part_count"] = part_count
    _write_metadata(metadata)
    return metadata


def read_artifact_chunk(
    artifact_id: str,
    part_index: int,
) -> bytes:
    metadata, snapshot = _validated_snapshot(artifact_id)
    chunk_bytes = metadata.get("chunk_bytes")
    part_count = metadata.get("part_count")
    if type(chunk_bytes) is not int or type(part_count) is not int:
        raise ValueError(
            "Artifact is not configured for chunked export"
        )
    if (
        type(part_index) is not int
        or not 0 <= part_index < part_count
    ):
        raise ValueError(
            "part_index is outside the artifact chunk range"
        )
    with snapshot.open("rb") as handle:
        handle.seek(part_index * chunk_bytes)
        return handle.read(chunk_bytes)


def verify_artifact_provenance(
    artifact_id: str,
    *,
    recursive: bool = True,
) -> dict[str, Any]:
    root_id = normalize_artifact_id(artifact_id)
    issues: list[dict[str, Any]] = []
    checked: set[str] = set()
    active: set[str] = set()

    def visit(current_id: str) -> None:
        if current_id in active:
            issues.append({
                "artifact_id": current_id,
                "type": "provenance_cycle",
            })
            return
        if current_id in checked:
            return

        active.add(current_id)
        try:
            metadata, _snapshot = _validated_snapshot(
                current_id,
                require_live=False,
            )
        except Exception as exc:
            issues.append({
                "artifact_id": current_id,
                "type": "artifact_invalid",
                "message": str(exc),
            })
            active.discard(current_id)
            checked.add(current_id)
            return

        parent_ids = metadata.get("parent_artifacts", [])
        if not isinstance(parent_ids, list) or not all(
            isinstance(item, str) for item in parent_ids
        ):
            issues.append({
                "artifact_id": current_id,
                "type": "parent_metadata_invalid",
            })
            parent_ids = []

        provenance = metadata.get("provenance")
        if provenance is not None:
            if not isinstance(provenance, dict):
                issues.append({
                    "artifact_id": current_id,
                    "type": "provenance_metadata_invalid",
                })
                recorded_parents = []
            else:
                recorded_parents = provenance.get("parents", [])
                if not isinstance(recorded_parents, list):
                    issues.append({
                        "artifact_id": current_id,
                        "type": "provenance_metadata_invalid",
                    })
                    recorded_parents = []

            recorded_ids = [
                item.get("artifact_id")
                for item in recorded_parents
                if isinstance(item, dict)
            ]
            if recorded_ids != parent_ids:
                issues.append({
                    "artifact_id": current_id,
                    "type": "parent_list_mismatch",
                })

            for record in recorded_parents:
                if not isinstance(record, dict):
                    continue
                parent_id = record.get("artifact_id")
                if not isinstance(parent_id, str):
                    continue
                try:
                    parent_meta, _parent_snapshot = (
                        _validated_snapshot(
                            parent_id,
                            require_live=False,
                        )
                    )
                except Exception as exc:
                    issues.append({
                        "artifact_id": current_id,
                        "parent_artifact_id": parent_id,
                        "type": "parent_missing_or_invalid",
                        "message": str(exc),
                    })
                    continue
                for key in ("sha256", "size", "name", "mime_type"):
                    if record.get(key) != parent_meta.get(key):
                        issues.append({
                            "artifact_id": current_id,
                            "parent_artifact_id": parent_id,
                            "type": "parent_snapshot_mismatch",
                            "field": key,
                        })
                if recursive:
                    visit(parent_id)
        elif parent_ids:
            issues.append({
                "artifact_id": current_id,
                "type": "legacy_provenance_missing",
            })
            if recursive:
                for parent_id in parent_ids:
                    visit(parent_id)

        active.discard(current_id)
        checked.add(current_id)

    visit(root_id)
    return {
        "artifact_id": root_id,
        "valid": not issues,
        "recursive": recursive,
        "checked_artifacts": len(checked),
        "issues": issues,
    }


def artifact_gc(
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    store = _store_root()
    now = _utcnow()
    metadata_by_id: dict[str, dict[str, Any]] = {}
    invalid: list[dict[str, str]] = []

    for path in sorted(store.iterdir()):
        if not path.is_dir():
            continue
        artifact_id = path.name
        try:
            normalize_artifact_id(artifact_id)
            metadata_by_id[artifact_id] = _load_metadata(
                artifact_id
            )
        except Exception as exc:
            invalid.append({
                "artifact_id": artifact_id,
                "error": str(exc),
            })

    live: set[str] = set()
    expired: set[str] = set()
    parents: dict[str, list[str]] = {}

    for artifact_id, metadata in metadata_by_id.items():
        try:
            if now < _expiry(metadata):
                live.add(artifact_id)
            else:
                expired.add(artifact_id)
        except Exception as exc:
            invalid.append({
                "artifact_id": artifact_id,
                "error": str(exc),
            })
            continue
        raw_parents = metadata.get("parent_artifacts", [])
        if isinstance(raw_parents, list):
            parents[artifact_id] = [
                item
                for item in raw_parents
                if isinstance(item, str)
            ]
        else:
            parents[artifact_id] = []

    protected = set(live)
    stack = list(live)
    while stack:
        current = stack.pop()
        for parent_id in parents.get(current, []):
            if parent_id in protected:
                continue
            if parent_id in metadata_by_id:
                protected.add(parent_id)
                stack.append(parent_id)

    candidates = sorted(expired - protected)
    reclaimed_bytes = 0
    removed = 0
    for artifact_id in candidates:
        payload = _artifact_dir(artifact_id) / "payload"
        if payload.is_file():
            reclaimed_bytes += payload.stat().st_size
        if not dry_run:
            shutil.rmtree(
                _artifact_dir(artifact_id),
                ignore_errors=False,
            )
            removed += 1

    preserved_ancestors = protected - live
    return {
        "dry_run": dry_run,
        "artifact_count": len(metadata_by_id),
        "live_count": len(live),
        "expired_count": len(expired),
        "protected_ancestor_count": len(
            preserved_ancestors
        ),
        "candidate_count": len(candidates),
        "reclaimable_bytes": reclaimed_bytes,
        "removed_count": removed,
        "candidates": candidates[:100],
        "candidates_truncated": len(candidates) > 100,
        "invalid_entries": invalid[:100],
        "invalid_entries_truncated": len(invalid) > 100,
    }


def revoke_artifact(
    artifact_id: str,
) -> dict[str, Any]:
    artifact_dir = _artifact_dir(artifact_id)
    existed = artifact_dir.is_dir()
    if existed:
        shutil.rmtree(artifact_dir)
    return {
        "artifact_id": artifact_id,
        "revoked": existed,
    }
