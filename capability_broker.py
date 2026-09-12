"""Capability invocation broker for PLA v0.13.

The broker validates availability, confirmation policy, JSON arguments, and
optional artifact contracts before delegating a single provider call. It never
plans follow-up calls.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
from typing import Any, Callable
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from artifact_runtime import ArtifactInvocation
from capability_registry import CapabilityRegistry
from event_runtime import EventStore
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


_SEMANTIC_FAILURE_STATUSES = {
    "failed",
    "error",
    "timeout",
    "precondition_failed",
    "blocked",
    "not_ready",
}


def _stable_sha256(value: Any) -> str:
    serialized = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _semantic_result_status(result: dict[str, Any]) -> str:
    if result.get("is_error") is True:
        return "failed"
    top = result.get("status")
    data = result.get("data")
    if isinstance(data, dict):
        nested = data.get("status")
        if isinstance(nested, str) and nested:
            return nested
    return str(top or "completed")


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
        event_store: EventStore | None = None,
    ) -> None:
        self._registry = registry
        self._mcp_clients = mcp_clients
        self._event_store = event_store
        self._internal_handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}

    def register_internal_handler(
        self,
        capability_id: str,
        handler: Callable[[dict[str, Any]], Any],
    ) -> None:
        if not isinstance(capability_id, str) or not capability_id:
            raise ValueError("capability_id must be a non-empty string")
        if not callable(handler):
            raise TypeError("handler must be callable")
        if capability_id in self._internal_handlers:
            raise ValueError(f"Internal handler already registered: {capability_id}")
        self._internal_handlers[capability_id] = handler

    def _validated_descriptor_and_arguments(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        confirmation: str | None,
        transaction_context: bool = False,
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
        if descriptor.get("requires_transaction") and not transaction_context:
            raise PermissionError(
                f"Capability {capability_id} requires a transaction context"
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

    def validate_invocation(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
        transaction_context: bool = False,
    ) -> dict[str, Any]:
        """Validate one capability call without invoking the provider."""
        descriptor, _ = self._validated_descriptor_and_arguments(
            capability_id,
            arguments,
            confirmation,
            transaction_context,
        )
        return descriptor

    def _emit_runtime_event(
        self,
        event_type: str,
        descriptor: dict[str, Any],
        capability_id: str,
        *,
        correlation_id: str,
        causation_id: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self._event_store is None:
            return None
        if "event-control" in set(descriptor.get("tags") or []):
            return None
        try:
            return self._event_store.emit(
                event_type,
                source="capability_broker",
                subject=capability_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
                capability_id=capability_id,
                provider_id=descriptor.get("provider_id"),
                payload=payload,
            )
        except Exception:
            # The event plane is observability, not the source of capability
            # correctness. Event persistence failures must not alter the
            # selected capability's execution semantics.
            return None

    async def invoke(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
        transaction_context: bool = False,
    ) -> dict[str, Any]:
        descriptor, provider_arguments = self._validated_descriptor_and_arguments(
            capability_id,
            arguments,
            confirmation,
            transaction_context,
        )
        correlation_id = uuid4().hex
        arguments_sha256 = _stable_sha256(provider_arguments)
        before = self._emit_runtime_event(
            "capability.before_invoke",
            descriptor,
            capability_id,
            correlation_id=correlation_id,
            causation_id=None,
            payload={
                "arguments_sha256": arguments_sha256,
                "argument_keys": sorted(provider_arguments),
                "risk_level": descriptor.get("risk_level"),
                "requires_confirmation": bool(
                    descriptor.get("requires_confirmation")
                ),
                "confirmation_supplied": confirmation == "INVOKE",
                "requires_transaction": bool(
                    descriptor.get("requires_transaction")
                ),
                "transaction_context": bool(transaction_context),
            },
        )
        causation_id = before.get("event_id") if before else None

        try:
            result = await self._invoke_without_events(
                capability_id,
                provider_arguments,
                confirmation=confirmation,
                transaction_context=transaction_context,
            )
        except Exception as exc:
            self._emit_runtime_event(
                "capability.failed",
                descriptor,
                capability_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
                payload={
                    "arguments_sha256": arguments_sha256,
                    "exception_type": type(exc).__name__,
                    "message_sha256": hashlib.sha256(
                        str(exc).encode("utf-8", errors="replace")
                    ).hexdigest(),
                    "message_length": len(str(exc)),
                },
            )
            raise

        semantic_status = _semantic_result_status(result)
        failed = (
            result.get("is_error") is True
            or semantic_status in _SEMANTIC_FAILURE_STATUSES
        )
        self._emit_runtime_event(
            "capability.failed" if failed else "capability.succeeded",
            descriptor,
            capability_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload={
                "arguments_sha256": arguments_sha256,
                "result_sha256": _stable_sha256(result),
                "semantic_status": semantic_status,
                "is_error": bool(result.get("is_error")),
                "artifact_ids": [
                    item.get("artifact_id")
                    for item in result.get("artifacts", [])
                    if isinstance(item, dict) and item.get("artifact_id")
                ],
            },
        )
        return result

    async def _invoke_without_events(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
        transaction_context: bool = False,
    ) -> dict[str, Any]:
        descriptor, provider_arguments = self._validated_descriptor_and_arguments(
            capability_id, arguments, confirmation, transaction_context
        )
        contract = descriptor.get("artifact_contract") or {}
        internal_handler = self._internal_handlers.get(capability_id)
        if internal_handler is not None:
            if contract:
                raise ValueError(
                    f"Internal capability cannot declare an artifact contract: {capability_id}"
                )
            value = internal_handler(provider_arguments)
            if inspect.isawaitable(value):
                value = await value
            json_data = _jsonable(value)
            return {
                "status": "completed",
                "provider_id": descriptor["provider_id"],
                "capability_id": capability_id,
                "remote_name": descriptor["remote_name"],
                "data": json_data,
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            json_data,
                            ensure_ascii=False,
                            default=repr,
                            separators=(",", ":"),
                        ),
                    }
                ],
                "artifacts": [],
                "is_error": False,
                "meta": {"internal": True},
            }

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
