"""Transactional multi-file changesets with best-effort rollback, not OS atomicity."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
from typing_extensions import TypedDict

import local_tools as local
from runtime_context import checkpoint, observe

MAX_FILES = 32
MAX_TOTAL_PATCH = 1_000_000
MAX_TOTAL_BYTES = 16_000_000


class ChangeRequest(TypedDict):
    path: str
    expected_sha256: str
    patch: str


@dataclass
class Candidate:
    path: str
    target: Path
    original: bytes
    updated: bytes
    before: str
    after: str
    stage: Path | None = None
    backup: Path | None = None


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stage(target: Path, raw: bytes) -> Path:
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".changeset-", suffix=".tmp", delete=False) as handle:
            name = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return name
    except Exception:
        if name:
            name.unlink(missing_ok=True)
        raise


def _check(candidate: Candidate, expected: str) -> None:
    if local.safe_path(candidate.path) != candidate.target:
        raise local.FileChangedSinceRead(f"Resolved path changed: {candidate.path}")
    local._validate_expected_sha256(candidate.target, expected)


def apply_changeset(changes: list[ChangeRequest]) -> dict:
    candidates: list[Candidate] = []
    applied: list[Candidate] = []
    restored: list[str] = []
    rollback_errors: list[dict] = []
    cleanup_errors: list[dict] = []
    phase = "preflight"
    path = None
    result = {"status": "error", "files_requested": len(changes) if isinstance(changes, list) else 0,
              "files_applied": 0, "rolled_back": False, "rollback_attempted": False,
              "restored_paths": restored, "rollback_errors": rollback_errors,
              "cleanup_errors": cleanup_errors, "recovery_files": [], "results": []}
    with local.WORKSPACE_MUTATION_LOCK:
        try:
            checkpoint()
            observe("changeset_preflight")
            if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_FILES:
                raise ValueError(f"changes must contain 1–{MAX_FILES} files")
            total_patch = 0
            total_bytes = 0
            seen = set()
            identities = set()
            for change in changes:
                if not isinstance(change, dict) or set(change) != {"path", "patch", "expected_sha256"}:
                    raise ValueError("Each change requires exactly path, patch, expected_sha256")
                path = change["path"]
                target = local.safe_path(path)
                if target in seen:
                    raise ValueError(f"Duplicate target path: {path}")
                seen.add(target)
                if not target.is_file():
                    raise ValueError(f"Existing file required: {path}")
                stat = target.stat()
                identity = (stat.st_dev, stat.st_ino)
                if stat.st_ino and identity in identities:
                    raise ValueError(f"Duplicate file identity: {path}")
                identities.add(identity)
                patch = change["patch"]
                expected = change["expected_sha256"]
                if not isinstance(expected, str) or not local.SHA256_PATTERN.fullmatch(expected):
                    raise ValueError("expected_sha256 is required and must be 64 hexadecimal characters")
                if not isinstance(patch, str):
                    raise ValueError("patch must be a string")
                total_patch += len(patch)
                if total_patch > MAX_TOTAL_PATCH:
                    raise ValueError("Total patch size exceeds 1,000,000 characters")
                if total_bytes + target.stat().st_size > MAX_TOTAL_BYTES:
                    raise ValueError("Total source size exceeds 16,000,000 bytes")
                # Bound reads even when an external writer grows a file after stat.
                with target.open("rb") as handle:
                    original = handle.read(MAX_TOTAL_BYTES + 1)
                total_bytes += len(original)
                if total_bytes > MAX_TOTAL_BYTES:
                    raise ValueError("Source file exceeds size limit")
                before = _sha(original)
                if before != expected.lower():
                    raise local.FileChangedSinceRead(f"File hash precondition failed for {path}")
                updated = local._apply_unified_hunks(original.decode("utf-8"), patch, local._relative_path(target)).encode("utf-8")
                candidates.append(Candidate(path, target, original, updated, before, _sha(updated)))
            phase = "stage"
            observe("changeset_stage", files=len(candidates))
            for candidate in candidates:
                checkpoint()
                path = candidate.path
                _check(candidate, candidate.before)
                candidate.stage = _stage(candidate.target, candidate.updated)
                candidate.backup = _stage(candidate.target, candidate.original)
            # Recheck the entire set after staging, before the first official write.
            for candidate in candidates:
                path = candidate.path
                _check(candidate, candidate.before)
            checkpoint()
            phase = "commit"
            observe("changeset_commit", files=len(candidates))
            # Cancellation is deferred through commit/rollback to leave a known outcome.
            for candidate in candidates:
                path = candidate.path
                _check(candidate, candidate.before)
                os.replace(candidate.stage, candidate.target)
                applied.append(candidate)
                local._remove_cached_bytecode(candidate.target)
            result.update(status="completed", files_applied=len(applied),
                          results=[{"path": c.path, "before_sha256": c.before, "after_sha256": c.after} for c in candidates])
        except Exception as exc:
            result.update(error={"type": type(exc).__name__, "message": str(exc)[:20_000]},
                          failed_phase=phase, path=path)
            if applied:
                result["rollback_attempted"] = True
                for candidate in reversed(applied):
                    try:
                        # Never overwrite an intervening external edit during rollback.
                        _check(candidate, candidate.after)
                        os.replace(candidate.backup, candidate.target)
                        restored.append(candidate.path)
                    except Exception as rollback_exc:
                        rollback_errors.append({"path": candidate.path, "type": type(rollback_exc).__name__,
                                                "message": str(rollback_exc)[:20_000]})
                result["rolled_back"] = not rollback_errors
            result["files_applied"] = len(applied) - len(restored)
        finally:
            result["files_replaced_before_rollback"] = len(applied)
            result["unrestored_paths"] = [c.path for c in applied if c.path not in restored] if result["status"] == "error" else []
            for candidate in candidates:
                for temporary in (candidate.stage, candidate.backup):
                    if temporary is not None:
                        if (temporary == candidate.backup and candidate.path in result["unrestored_paths"]):
                            result["recovery_files"].append({"path": candidate.path, "backup_path": local._relative_path(temporary), "before_sha256": candidate.before})
                            continue
                        try:
                            # Cleanup only exact temporary files created by this call.
                            local.safe_path(str(temporary))
                            temporary.unlink(missing_ok=True)
                        except Exception as cleanup_exc:
                            cleanup_errors.append({"path": str(temporary), "message": str(cleanup_exc)[:20_000]})
    return result
