"""Per-invocation artifact staging for capability providers."""

from __future__ import annotations

import mimetypes
import secrets
import shutil
from pathlib import Path
from typing import Any

import local_tools
from artifact_bridge import (
    artifact_snapshot_path,
    import_internal_artifact,
    normalize_artifact_id,
)
from artifact_policy import (
    mime_matches,
    normalize_artifact_policy,
    normalize_mime_type,
    validate_artifact_content,
)


class ArtifactInvocation:
    def __init__(
        self,
        provider_id: str,
        capability_id: str,
        *,
        policy: dict[str, Any] | None = None,
    ) -> None:
        token = secrets.token_urlsafe(18)
        self.provider_id = provider_id
        self.capability_id = capability_id
        self.policy = normalize_artifact_policy(policy)
        self.root = (local_tools.WORKSPACE / ".capability_io" / token).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self._parents: list[str] = []
        self._replacements: dict[str, str] = {}
        self._output_count = 0
        self._output_bytes = 0

    def _register_replacement_variants(
        self,
        temporary: str,
        artifact_id: str,
    ) -> None:
        variants = {temporary}
        if "\\" in temporary:
            current = temporary
            for _ in range(5):
                current = current.replace("\\", "\\\\")
                variants.add(current)
            variants.add(temporary.replace("\\", "/"))
        for variant in variants:
            if variant:
                self._replacements[variant] = artifact_id

    def _register_path_replacements(
        self,
        path: Path,
        artifact_id: str,
    ) -> None:
        resolved = path.resolve()
        self._register_replacement_variants(str(resolved), artifact_id)
        self._register_replacement_variants(resolved.as_posix(), artifact_id)
        try:
            self._register_replacement_variants(resolved.as_uri(), artifact_id)
        except ValueError:
            pass

    def _check_input_policy(
        self,
        metadata: dict[str, Any],
        argument_name: str,
    ) -> None:
        size = metadata.get("size")
        if type(size) is not int or size < 0:
            raise ValueError("Artifact input size metadata is invalid")
        if size > self.policy["max_input_bytes"]:
            raise ValueError(
                f"Artifact input {argument_name!r} exceeds max_input_bytes "
                f"({size} > {self.policy['max_input_bytes']})"
            )
        allowed = self.policy["allowed_input_mime_types"]
        if allowed:
            mime_type = metadata.get("mime_type")
            if not isinstance(mime_type, str) or not mime_matches(mime_type, allowed):
                raise ValueError(
                    f"Artifact input {argument_name!r} MIME type "
                    f"{mime_type!r} is not allowed"
                )

    def stage_input(
        self,
        reference: str,
        argument_name: str,
        *,
        transport: str = "local_path",
    ) -> str:
        artifact_id = normalize_artifact_id(reference)
        metadata, snapshot = artifact_snapshot_path(artifact_id)
        self._check_input_policy(metadata, argument_name)
        validate_artifact_content(snapshot, metadata["mime_type"])
        safe_arg = "".join(
            ch if ch.isalnum() or ch in "_-" else "_"
            for ch in argument_name
        )
        filename = Path(metadata["name"]).name or f"{safe_arg}.bin"
        destination = self.root / "inputs" / safe_arg / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot, destination)
        resolved_path = destination.resolve()
        resolved = str(resolved_path)
        self._parents.append(artifact_id)
        self._register_path_replacements(resolved_path, artifact_id)
        if transport == "local_path":
            return resolved
        if transport == "file_uri":
            uri = resolved_path.as_uri()
            self._register_replacement_variants(uri, artifact_id)
            return uri
        raise ValueError(f"Unsupported artifact input transport: {transport}")

    def allocate_output(self, argument_name: str, filename: str) -> str:
        if (
            not isinstance(filename, str)
            or not filename
            or Path(filename).name != filename
        ):
            raise ValueError("Managed output filename must be a plain file name")
        safe_arg = "".join(
            ch if ch.isalnum() or ch in "_-" else "_"
            for ch in argument_name
        )
        destination = self.root / "outputs" / safe_arg / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        return str(destination.resolve())

    def _check_output_policy(
        self,
        path: Path,
        mime_type: str,
    ) -> None:
        size = path.stat().st_size
        if size > self.policy["max_output_bytes"]:
            raise ValueError(
                f"Provider output exceeds max_output_bytes "
                f"({size} > {self.policy['max_output_bytes']})"
            )
        if self._output_count + 1 > self.policy["max_output_artifacts"]:
            raise ValueError(
                "Provider output exceeds max_output_artifacts "
                f"({self._output_count + 1} > "
                f"{self.policy['max_output_artifacts']})"
            )
        if (
            self._output_bytes + size
            > self.policy["max_total_output_bytes"]
        ):
            raise ValueError(
                "Provider output exceeds max_total_output_bytes "
                f"({self._output_bytes + size} > "
                f"{self.policy['max_total_output_bytes']})"
            )
        allowed = self.policy["allowed_output_mime_types"]
        if allowed and not mime_matches(mime_type, allowed):
            raise ValueError(
                f"Provider output MIME type {mime_type!r} is not allowed"
            )

    def import_output(
        self,
        path_value: str,
        *,
        name: str | None = None,
        mime_type: str | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        path = Path(path_value).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(
                "Provider output path escapes invocation workspace"
            ) from exc
        if not path.is_file():
            raise ValueError(
                f"Provider output file does not exist: {path.name}"
            )

        resolved_mime = normalize_mime_type(
            mime_type
            or mimetypes.guess_type(name or path.name)[0]
            or "application/octet-stream"
        )
        self._check_output_policy(path, resolved_mime)
        validate_artifact_content(path, resolved_mime)
        effective_ttl = (
            self.policy["output_ttl_seconds"]
            if ttl_seconds is None
            else ttl_seconds
        )
        if effective_ttl > self.policy["output_ttl_seconds"]:
            raise ValueError(
                "Provider output TTL exceeds capability artifact policy"
            )

        metadata = import_internal_artifact(
            path,
            name=name or path.name,
            mime_type=resolved_mime,
            ttl_seconds=effective_ttl,
            source_provider=self.provider_id,
            source_capability=self.capability_id,
            parent_artifacts=list(dict.fromkeys(self._parents)),
        )
        self._output_count += 1
        self._output_bytes += metadata["size"]
        self._register_path_replacements(path, metadata["artifact_id"])
        return metadata

    def sanitize(self, value: Any) -> Any:
        if isinstance(value, str):
            sanitized = value
            for temporary, artifact_id in sorted(
                self._replacements.items(),
                key=lambda item: len(item[0]),
                reverse=True,
            ):
                sanitized = sanitized.replace(temporary, artifact_id)
            return sanitized
        if isinstance(value, dict):
            return {
                str(k): self.sanitize(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.sanitize(item) for item in value]
        if isinstance(value, tuple):
            return [self.sanitize(item) for item in value]
        return value

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "ArtifactInvocation":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
