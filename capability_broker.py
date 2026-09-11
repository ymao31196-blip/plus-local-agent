"""Capability invocation broker for PLA v0.13.

The broker validates availability, confirmation policy, JSON arguments, and
optional artifact contracts before delegating a single provider call. It never
plans follow-up calls.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from artifact_runtime import ArtifactInvocation
from capability_registry import CapabilityRegistry
from mcp_client_manager import MCPClientManager


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json", by_alias=True)
    return repr(value)


def _public_artifact(metadata: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "artifact_id",
        "name",
        "mime_type",
        "size",
        "sha256",
        "created_at",
        "expires_at",
        "source_provider",
        "source_capability",
        "parent_artifacts",
        "provenance",
    )
    return {key: metadata.get(key) for key in keys}


class CapabilityBroker:
    def __init__(
        self,
        registry: CapabilityRegistry,
        mcp_clients: MCPClientManager,
    ) -> None:
        self._registry = registry
        self._mcp_clients = mcp_clients

    def _validated_descriptor_and_arguments(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        confirmation: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(arguments, dict):
            raise TypeError("arguments must be an object")

        descriptor = self._registry.describe(capability_id)
        if not descriptor["available"]:
            raise ValueError(f"Capability is unavailable: {capability_id}")

        if descriptor["requires_confirmation"] and confirmation != "INVOKE":
            raise PermissionError(
                f"Capability {capability_id} requires confirmation='INVOKE'"
            )

        contract = descriptor.get("artifact_contract") or {}
        managed_output_paths = contract.get("output_paths", {})
        if managed_output_paths and not isinstance(managed_output_paths, dict):
            raise ValueError(f"Invalid artifact output path contract: {capability_id}")
        for field in managed_output_paths:
            if field in arguments:
                raise ValueError(
                    f"Argument {field!r} for {capability_id} is managed by PLA"
                )

        for field in contract.get("inputs", []):
            if field not in arguments:
                raise ValueError(
                    f"Artifact argument {field!r} is required for {capability_id}"
                )

        validation_schema = deepcopy(descriptor["input_schema"])
        required = validation_schema.get("required")
        if isinstance(required, list) and managed_output_paths:
            validation_schema["required"] = [
                field for field in required if field not in managed_output_paths
            ]

        try:
            validator = Draft202012Validator(validation_schema)
        except SchemaError as exc:
            raise ValueError(
                f"Capability input schema is invalid: {capability_id}"
            ) from exc

        errors = sorted(
            validator.iter_errors(arguments),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path) or "<root>"
            raise ValueError(
                f"Invalid arguments for {capability_id} at {location}: {first.message}"
            )
        return descriptor, dict(arguments)

    async def invoke(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        descriptor, provider_arguments = self._validated_descriptor_and_arguments(
            capability_id, arguments, confirmation
        )
        contract = descriptor.get("artifact_contract") or {}
        if not contract:
            result = await self._mcp_clients.call_tool(
                descriptor["provider_id"],
                descriptor["remote_name"],
                provider_arguments,
            )
            return self._normalize_plain_result(descriptor, capability_id, result)

        transport = contract.get("transport")
        if transport not in {"local_path", "file_uri"}:
            raise ValueError(
                f"Unsupported artifact transport for {capability_id}: "
                f"{transport!r}"
            )

        input_fields = contract.get("inputs", [])
        output_fields = contract.get("outputs", [])
        output_paths = contract.get("output_paths", {})
        if (
            not isinstance(input_fields, list)
            or not isinstance(output_fields, list)
            or not isinstance(output_paths, dict)
        ):
            raise ValueError(f"Invalid artifact contract for {capability_id}")

        with ArtifactInvocation(
            descriptor["provider_id"],
            capability_id,
            policy=contract.get("policy"),
        ) as invocation:
            for field in input_fields:
                reference = provider_arguments[field]
                if not isinstance(reference, str):
                    raise ValueError(
                        f"Artifact argument {field} for {capability_id} must be a string reference"
                    )
                provider_arguments[field] = invocation.stage_input(
                    reference,
                    field,
                    transport=transport,
                )

            managed_output_locations: dict[str, tuple[str, dict[str, str]]] = {}
            for argument_name, specification in output_paths.items():
                if not isinstance(specification, dict):
                    raise ValueError(
                        f"Invalid managed output specification for {capability_id}"
                    )
                filename = specification.get("filename")
                if not isinstance(filename, str) or not filename:
                    raise ValueError(
                        f"Managed output {argument_name!r} for {capability_id} "
                        "is missing a filename"
                    )
                output_path = invocation.allocate_output(argument_name, filename)
                provider_arguments[argument_name] = output_path
                managed_output_locations[argument_name] = (
                    output_path,
                    specification,
                )

            result = await self._mcp_clients.call_tool(
                descriptor["provider_id"],
                descriptor["remote_name"],
                provider_arguments,
            )

            raw_data = result.data
            if raw_data is None:
                raw_data = result.structured_content
            json_data = _jsonable(raw_data)

            artifacts: list[dict[str, Any]] = []
            artifact_outputs: dict[str, str] = {}

            if not result.is_error and output_fields:
                if not isinstance(json_data, dict):
                    raise ValueError(
                        f"Artifact-producing capability {capability_id} returned non-object data"
                    )
                for field in output_fields:
                    path_value = json_data.get(field)
                    if not isinstance(path_value, str) or not path_value:
                        raise ValueError(
                            f"Artifact-producing capability {capability_id} did not return "
                            f"a valid path in field {field!r}"
                        )
                    metadata = invocation.import_output(path_value)
                    public = _public_artifact(metadata)
                    artifacts.append(public)
                    artifact_outputs[field] = public["artifact_id"]

            if not result.is_error:
                for argument_name, (
                    output_path,
                    specification,
                ) in managed_output_locations.items():
                    metadata = invocation.import_output(
                        output_path,
                        name=specification.get("filename"),
                        mime_type=specification.get("mime_type"),
                    )
                    public = _public_artifact(metadata)
                    artifacts.append(public)
                    artifact_outputs[argument_name] = public["artifact_id"]

            normalized_data = invocation.sanitize(json_data)
            for field in output_fields:
                if isinstance(normalized_data, dict) and field in artifact_outputs:
                    normalized_data[field] = artifact_outputs[field]

            raw_content = [
                item.model_dump(mode="json", by_alias=True)
                if hasattr(item, "model_dump")
                else _jsonable(item)
                for item in result.content
            ]
            produces_artifacts = bool(output_fields or output_paths)
            if produces_artifacts:
                normalized_content = []
                content_omitted = True
            else:
                normalized_content = invocation.sanitize(_jsonable(raw_content))
                content_omitted = False

            return {
                "status": "failed" if result.is_error else "completed",
                "provider_id": descriptor["provider_id"],
                "capability_id": capability_id,
                "remote_name": descriptor["remote_name"],
                "data": normalized_data,
                "content": normalized_content,
                "content_omitted_for_artifact_contract": content_omitted,
                "artifacts": artifacts,
                "artifact_outputs": artifact_outputs,
                "meta": _jsonable(result.meta or {}),
                "is_error": bool(result.is_error),
            }

    @staticmethod
    def _normalize_plain_result(
        descriptor: dict[str, Any],
        capability_id: str,
        result: Any,
    ) -> dict[str, Any]:
        content = [
            item.model_dump(mode="json", by_alias=True)
            if hasattr(item, "model_dump")
            else _jsonable(item)
            for item in result.content
        ]
        data = result.data
        if data is None:
            data = result.structured_content
        return {
            "status": "failed" if result.is_error else "completed",
            "provider_id": descriptor["provider_id"],
            "capability_id": capability_id,
            "remote_name": descriptor["remote_name"],
            "data": _jsonable(data),
            "content": _jsonable(content),
            "artifacts": [],
            "meta": _jsonable(result.meta or {}),
            "is_error": bool(result.is_error),
        }
