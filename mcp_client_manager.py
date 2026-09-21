"""External MCP discovery manager for PLA v0.11.

This module owns MCP connection/discovery only. It does not invoke capabilities or
make agent decisions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import time
from typing import Any

from fastmcp import Client
from fastmcp.exceptions import ToolError

from artifact_policy import normalize_artifact_policy, normalize_mime_type
from capability_models import CapabilityDescriptor, PROVIDER_ID_RE
from capability_registry import CapabilityRegistry


_INVALID_SUFFIX = re.compile(r"[^a-z0-9_-]+")
_REPEAT_UNDERSCORE = re.compile(r"_+")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _capability_suffix(remote_name: str) -> str:
    if not isinstance(remote_name, str) or not remote_name.strip():
        raise ValueError("Remote MCP tool name must be a non-empty string")
    value = remote_name.strip().casefold()
    value = _INVALID_SUFFIX.sub("_", value)
    value = _REPEAT_UNDERSCORE.sub("_", value).strip("_-")
    if not value:
        raise ValueError(f"Remote MCP tool name cannot form a capability id: {remote_name!r}")
    return value


@dataclass(frozen=True)
class MCPProviderState:
    provider_id: str
    state: str
    enabled: bool
    tool_count: int
    last_discovered_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_latency_ms: float | None = None
    consecutive_failures: int = 0
    retry_after: str | None = None
    error_type: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class MCPClientManager:
    """Register MCP endpoints and discover their tools into a CapabilityRegistry."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        discovery_timeout: float = 10.0,
        invoke_timeout: float = 60.0,
        retry_base_seconds: float = 2.0,
        retry_max_seconds: float = 60.0,
        discovery_concurrency: int = 4,
    ) -> None:
        if discovery_timeout <= 0:
            raise ValueError("discovery_timeout must be positive")
        if invoke_timeout <= 0:
            raise ValueError("invoke_timeout must be positive")
        if retry_base_seconds <= 0:
            raise ValueError("retry_base_seconds must be positive")
        if retry_max_seconds < retry_base_seconds:
            raise ValueError("retry_max_seconds must be >= retry_base_seconds")
        if not isinstance(discovery_concurrency, int) or discovery_concurrency < 1:
            raise ValueError("discovery_concurrency must be a positive integer")
        self._registry = registry
        self._discovery_timeout = float(discovery_timeout)
        self._invoke_timeout = float(invoke_timeout)
        self._retry_base_seconds = float(retry_base_seconds)
        self._retry_max_seconds = float(retry_max_seconds)
        self._discovery_concurrency = discovery_concurrency
        self._sources: dict[str, Any] = {}
        self._enabled: dict[str, bool] = {}
        self._modes: dict[str, str] = {}
        self._routing_authorities: dict[str, str] = {}
        self._tool_overrides: dict[str, dict[str, dict[str, Any]]] = {}
        self._tool_allowlists: dict[str, set[str] | None] = {}
        self._discovery_timeouts: dict[str, float] = {}
        self._invoke_timeouts: dict[str, float] = {}
        self._persistent_session_enabled: dict[str, bool] = {}
        self._persistent_clients: dict[str, Client] = {}
        self._persistent_locks: dict[str, asyncio.Lock] = {}
        self._capability_extensions: dict[str, dict[str, CapabilityDescriptor]] = {}
        self._states: dict[str, MCPProviderState] = {}

    def has_provider(self, provider_id: str) -> bool:
        return provider_id in self._sources

    async def _drop_persistent_client(self, provider_id: str) -> None:
        client = self._persistent_clients.pop(provider_id, None)
        if client is None:
            return
        try:
            await client.close()
        except Exception:
            # Cleanup must not mask the original transport/discovery failure.
            pass

    async def close_provider_session(self, provider_id: str) -> None:
        lock = self._persistent_locks.get(provider_id)
        if lock is None:
            await self._drop_persistent_client(provider_id)
            return
        async with lock:
            await self._drop_persistent_client(provider_id)

    async def close_all_persistent_sessions(self) -> None:
        for provider_id in sorted(list(self._persistent_clients)):
            await self.close_provider_session(provider_id)

    async def _ensure_persistent_client(self, provider_id: str) -> Client:
        client = self._persistent_clients.get(provider_id)
        if client is not None:
            return client
        client = Client(
            self._sources[provider_id],
            mode=self._modes[provider_id],
        )
        await client.__aenter__()
        self._persistent_clients[provider_id] = client
        return client

    async def _list_tools_for_provider(self, provider_id: str) -> list[Any]:
        if not self._persistent_session_enabled.get(provider_id, False):
            return await self._list_tools(
                self._sources[provider_id],
                self._modes[provider_id],
            )
        async with self._persistent_locks[provider_id]:
            client = await self._ensure_persistent_client(provider_id)
            return list(await client.list_tools())

    async def _call_tool_for_provider(
        self,
        provider_id: str,
        remote_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        if not self._persistent_session_enabled.get(provider_id, False):
            return await self._call_tool(
                self._sources[provider_id],
                remote_name,
                arguments,
                self._modes[provider_id],
            )
        async with self._persistent_locks[provider_id]:
            client = await self._ensure_persistent_client(provider_id)
            return await client.call_tool(remote_name, arguments)

    def add_capability_extension(
        self,
        provider_id: str,
        descriptor: CapabilityDescriptor,
    ) -> None:
        """Attach one PLA-local capability to an external provider lifecycle."""
        if not isinstance(provider_id, str) or not PROVIDER_ID_RE.fullmatch(provider_id):
            raise ValueError(f"Invalid provider id: {provider_id!r}")
        if not isinstance(descriptor, CapabilityDescriptor):
            raise TypeError("descriptor must be a CapabilityDescriptor")
        if descriptor.provider_id != provider_id:
            raise ValueError("Capability extension provider_id does not match")
        bucket = self._capability_extensions.setdefault(provider_id, {})
        existing = bucket.get(descriptor.id)
        if existing is not None and existing != descriptor:
            raise ValueError(f"Capability extension already registered: {descriptor.id}")
        bucket[descriptor.id] = descriptor

    def add_provider(
        self,
        provider_id: str,
        source: Any,
        *,
        enabled: bool = True,
        mode: str = "auto",
        routing_authority: str = "recommendation",
        tool_overrides: dict[str, dict[str, Any]] | None = None,
        tool_allowlist: list[str] | tuple[str, ...] | set[str] | None = None,
        discovery_timeout: float | None = None,
        invoke_timeout: float | None = None,
        persistent_session: bool = False,
    ) -> None:
        if not isinstance(provider_id, str) or not PROVIDER_ID_RE.fullmatch(provider_id):
            raise ValueError(f"Invalid provider id: {provider_id!r}")
        if source is None:
            raise ValueError("source is required")
        if mode not in {"auto", "legacy"}:
            raise ValueError("mode must be 'auto' or 'legacy'")
        if routing_authority not in {
            "recommendation",
            "preferred",
            "enforced",
        }:
            raise ValueError(
                "routing_authority must be recommendation, preferred, or enforced"
            )
        if tool_overrides is not None and not isinstance(tool_overrides, dict):
            raise TypeError("tool_overrides must be an object")
        if discovery_timeout is not None and discovery_timeout <= 0:
            raise ValueError("provider discovery_timeout must be positive")
        if invoke_timeout is not None and invoke_timeout <= 0:
            raise ValueError("provider invoke_timeout must be positive")
        if not isinstance(persistent_session, bool):
            raise TypeError("persistent_session must be boolean")
        if provider_id in self._persistent_clients:
            raise RuntimeError(
                f"Close persistent provider session before reconfiguration: {provider_id}"
            )
        if provider_id in self._sources:
            self._set_registry_available(provider_id, False)
        if tool_allowlist is not None:
            if not isinstance(tool_allowlist, (list, tuple, set)):
                raise TypeError("tool_allowlist must be an array of tool names")
            if any(not isinstance(item, str) or not item for item in tool_allowlist):
                raise ValueError("tool_allowlist must contain non-empty strings")
            normalized_allowlist = set(tool_allowlist)
        else:
            normalized_allowlist = None
        self._sources[provider_id] = source
        self._enabled[provider_id] = bool(enabled)
        self._modes[provider_id] = mode
        self._routing_authorities[provider_id] = routing_authority
        self._tool_overrides[provider_id] = dict(tool_overrides or {})
        self._tool_allowlists[provider_id] = normalized_allowlist
        self._discovery_timeouts[provider_id] = float(
            discovery_timeout
            if discovery_timeout is not None
            else self._discovery_timeout
        )
        self._invoke_timeouts[provider_id] = float(
            invoke_timeout if invoke_timeout is not None else self._invoke_timeout
        )
        self._persistent_session_enabled[provider_id] = persistent_session
        self._persistent_locks[provider_id] = asyncio.Lock()
        self._states[provider_id] = MCPProviderState(
            provider_id=provider_id,
            state="configured",
            enabled=bool(enabled),
            tool_count=0,
        )

    def _set_registry_available(self, provider_id: str, available: bool) -> None:
        try:
            self._registry.set_provider_enabled(provider_id, available)
        except ValueError:
            # A configured provider does not enter the registry until discovery succeeds.
            pass

    def remove_provider(self, provider_id: str) -> None:
        if provider_id not in self._sources:
            raise ValueError(f"Unknown MCP provider: {provider_id}")
        if provider_id in self._persistent_clients:
            raise RuntimeError(
                f"Close persistent provider session before removal: {provider_id}"
            )
        self._set_registry_available(provider_id, False)
        try:
            self._registry.remove_provider(provider_id)
        except ValueError:
            pass
        self._sources.pop(provider_id, None)
        self._enabled.pop(provider_id, None)
        self._modes.pop(provider_id, None)
        self._tool_overrides.pop(provider_id, None)
        self._tool_allowlists.pop(provider_id, None)
        self._discovery_timeouts.pop(provider_id, None)
        self._invoke_timeouts.pop(provider_id, None)
        self._persistent_session_enabled.pop(provider_id, None)
        self._persistent_locks.pop(provider_id, None)
        self._states.pop(provider_id, None)

    def _next_retry_after(self, failure_count: int) -> str:
        delay = min(
            self._retry_max_seconds,
            self._retry_base_seconds * (2 ** max(0, failure_count - 1)),
        )
        return (
            datetime.now(timezone.utc) + timedelta(seconds=delay)
        ).isoformat()

    def _retry_blocked(self, state: MCPProviderState) -> bool:
        if not state.retry_after:
            return False
        try:
            retry_at = datetime.fromisoformat(state.retry_after)
        except ValueError:
            return False
        return datetime.now(timezone.utc) < retry_at

    def set_provider_enabled(self, provider_id: str, enabled: bool) -> None:
        if provider_id not in self._sources:
            raise ValueError(f"Unknown MCP provider: {provider_id}")
        self._enabled[provider_id] = bool(enabled)
        state = self._states[provider_id]
        self._states[provider_id] = MCPProviderState(
            provider_id=provider_id,
            state=state.state,
            enabled=bool(enabled),
            tool_count=state.tool_count,
            last_discovered_at=state.last_discovered_at,
            last_success_at=state.last_success_at,
            last_failure_at=state.last_failure_at,
            last_latency_ms=state.last_latency_ms,
            consecutive_failures=state.consecutive_failures,
            retry_after=state.retry_after,
            error_type=state.error_type,
            error_message=state.error_message,
        )
        self._set_registry_available(
            provider_id,
            bool(enabled) and state.state == "ready",
        )

    async def discover_provider(
        self,
        provider_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        if provider_id not in self._sources:
            raise ValueError(f"Unknown MCP provider: {provider_id}")

        previous = self._states[provider_id]
        if not force and self._retry_blocked(previous):
            raise RuntimeError(
                f"Provider retry backoff is active: {provider_id} until "
                f"{previous.retry_after}"
            )

        started = time.perf_counter()
        try:
            tools = await asyncio.wait_for(
                self._list_tools_for_provider(provider_id),
                timeout=self._discovery_timeouts[provider_id],
            )
            allowlist = self._tool_allowlists.get(provider_id)
            if allowlist is not None:
                available_names = {tool.name for tool in tools}
                missing = sorted(allowlist - available_names)
                if missing:
                    raise ValueError(
                        "Configured MCP tools are missing from provider "
                        f"{provider_id}: {', '.join(missing)}"
                    )
                tools = [tool for tool in tools if tool.name in allowlist]
            descriptors = self._map_tools(
                provider_id,
                tools,
                self._tool_overrides.get(provider_id, {}),
                self._routing_authorities.get(
                    provider_id,
                    "recommendation",
                ),
            )
            extensions = list(
                self._capability_extensions.get(provider_id, {}).values()
            )
            remote_ids = {descriptor.id for descriptor in descriptors}
            conflicts = sorted(
                descriptor.id
                for descriptor in extensions
                if descriptor.id in remote_ids
            )
            if conflicts:
                raise ValueError(
                    "Capability extension conflicts with discovered MCP tools: "
                    + ", ".join(conflicts)
                )
            descriptors.extend(
                sorted(extensions, key=lambda descriptor: descriptor.id)
            )
            self._registry.register_provider(
                provider_id,
                descriptors,
                enabled=self._enabled[provider_id],
            )
            now = _utcnow_iso()
            state = MCPProviderState(
                provider_id=provider_id,
                state="ready",
                enabled=self._enabled[provider_id],
                tool_count=len(descriptors),
                last_discovered_at=now,
                last_success_at=now,
                last_failure_at=previous.last_failure_at,
                last_latency_ms=round(
                    (time.perf_counter() - started) * 1000,
                    3,
                ),
                consecutive_failures=0,
                retry_after=None,
            )
            self._states[provider_id] = state
            return state.as_dict()
        except Exception as exc:
            if self._persistent_session_enabled.get(provider_id, False):
                await self._drop_persistent_client(provider_id)
            now = _utcnow_iso()
            failure_count = previous.consecutive_failures + 1
            state = MCPProviderState(
                provider_id=provider_id,
                state="error",
                enabled=self._enabled[provider_id],
                tool_count=previous.tool_count,
                last_discovered_at=now,
                last_success_at=previous.last_success_at,
                last_failure_at=now,
                last_latency_ms=round(
                    (time.perf_counter() - started) * 1000,
                    3,
                ),
                consecutive_failures=failure_count,
                retry_after=self._next_retry_after(failure_count),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            self._states[provider_id] = state
            self._set_registry_available(provider_id, False)
            raise

    async def discover_all(self) -> dict[str, dict[str, Any]]:
        provider_ids = sorted(self._sources)
        semaphore = asyncio.Semaphore(self._discovery_concurrency)

        async def discover_one(
            provider_id: str,
        ) -> tuple[str, dict[str, Any]]:
            async with semaphore:
                try:
                    state = await self.discover_provider(provider_id)
                except Exception:
                    state = self._states[provider_id].as_dict()
                return provider_id, state

        discovered = await asyncio.gather(
            *(discover_one(provider_id) for provider_id in provider_ids)
        )
        return dict(discovered)

    def provider_status(self, provider_id: str | None = None) -> dict[str, Any]:
        if provider_id is not None:
            if provider_id not in self._states:
                raise ValueError(f"Unknown MCP provider: {provider_id}")
            return self._states[provider_id].as_dict()
        return {
            key: self._states[key].as_dict()
            for key in sorted(self._states)
        }

    async def call_tool(
        self,
        provider_id: str,
        remote_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        if provider_id not in self._sources:
            raise ValueError(f"Unknown MCP provider: {provider_id}")
        if not self._enabled.get(provider_id, False):
            raise ValueError(f"MCP provider is disabled: {provider_id}")
        state = self._states[provider_id]
        if state.state != "ready":
            raise ValueError(f"MCP provider is not ready: {provider_id}")
        if not isinstance(remote_name, str) or not remote_name:
            raise ValueError("remote_name must be a non-empty string")
        if not isinstance(arguments, dict):
            raise TypeError("arguments must be an object")

        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self._call_tool_for_provider(
                    provider_id,
                    remote_name,
                    arguments,
                ),
                timeout=self._invoke_timeouts[provider_id],
            )
        except ToolError:
            # Remote tool/business errors do not imply provider transport failure.
            raise
        except Exception as exc:
            if self._persistent_session_enabled.get(provider_id, False):
                await self._drop_persistent_client(provider_id)
            previous = self._states[provider_id]
            now = _utcnow_iso()
            failure_count = previous.consecutive_failures + 1
            self._states[provider_id] = MCPProviderState(
                provider_id=provider_id,
                state="degraded",
                enabled=self._enabled[provider_id],
                tool_count=previous.tool_count,
                last_discovered_at=previous.last_discovered_at,
                last_success_at=previous.last_success_at,
                last_failure_at=now,
                last_latency_ms=round(
                    (time.perf_counter() - started) * 1000,
                    3,
                ),
                consecutive_failures=failure_count,
                retry_after=self._next_retry_after(failure_count),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            self._set_registry_available(provider_id, False)
            raise

        previous = self._states[provider_id]
        now = _utcnow_iso()
        self._states[provider_id] = MCPProviderState(
            provider_id=provider_id,
            state="ready",
            enabled=self._enabled[provider_id],
            tool_count=previous.tool_count,
            last_discovered_at=previous.last_discovered_at,
            last_success_at=now,
            last_failure_at=previous.last_failure_at,
            last_latency_ms=round(
                (time.perf_counter() - started) * 1000,
                3,
            ),
            consecutive_failures=0,
            retry_after=None,
        )
        return result

    @staticmethod
    async def _call_tool(
        source: Any,
        remote_name: str,
        arguments: dict[str, Any],
        mode: str,
    ) -> Any:
        async with Client(source, mode=mode) as client:
            return await client.call_tool(remote_name, arguments)

    @staticmethod
    async def _list_tools(source: Any, mode: str) -> list[Any]:
        async with Client(source, mode=mode) as client:
            return list(await client.list_tools())

    @staticmethod
    def _map_tools(
        provider_id: str,
        tools: list[Any],
        tool_overrides: dict[str, dict[str, Any]] | None = None,
        routing_authority: str = "recommendation",
    ) -> list[CapabilityDescriptor]:
        descriptors: list[CapabilityDescriptor] = []
        seen_ids: dict[str, str] = {}
        for tool in tools:
            remote_name = tool.name
            override = (tool_overrides or {}).get(remote_name, {})
            if not isinstance(override, dict):
                raise ValueError(f"Invalid tool override for {remote_name}")
            public_name = override.get("public_name", remote_name)
            if not isinstance(public_name, str) or not public_name.strip():
                raise ValueError(f"Invalid public_name override for {remote_name}")
            capability_id = f"{provider_id}.{_capability_suffix(public_name)}"
            previous = seen_ids.get(capability_id)
            if previous is not None and previous != remote_name:
                raise ValueError(
                    "MCP tool names collide after capability normalization: "
                    f"{previous!r} and {remote_name!r} -> {capability_id}"
                )
            seen_ids[capability_id] = remote_name

            meta = tool.meta if isinstance(tool.meta, dict) else {}
            fastmcp_meta = meta.get("fastmcp") if isinstance(meta.get("fastmcp"), dict) else {}
            pla_artifacts = meta.get("pla_artifacts") if isinstance(meta.get("pla_artifacts"), dict) else {}
            raw_inputs = pla_artifacts.get("inputs", [])
            raw_outputs = pla_artifacts.get("outputs", [])
            if not isinstance(raw_inputs, list) or not all(isinstance(item, str) and item for item in raw_inputs):
                raise ValueError(f"Invalid pla_artifacts.inputs for {remote_name}")
            if not isinstance(raw_outputs, list) or not all(isinstance(item, str) and item for item in raw_outputs):
                raise ValueError(f"Invalid pla_artifacts.outputs for {remote_name}")
            artifact_contract = {
                "transport": "local_path",
                "inputs": list(raw_inputs),
                "outputs": list(raw_outputs),
            } if raw_inputs or raw_outputs else {}

            if "artifact_contract" in override:
                candidate = override["artifact_contract"]
                if not isinstance(candidate, dict):
                    raise ValueError(f"Invalid artifact_contract override for {remote_name}")
                transport = candidate.get("transport")
                if transport not in {"local_path", "file_uri"}:
                    raise ValueError(f"Unsupported artifact transport for {remote_name}: {transport!r}")
                inputs = candidate.get("inputs", [])
                input_arrays = candidate.get("input_arrays", [])
                outputs = candidate.get("outputs", [])
                output_paths = candidate.get("output_paths", {})
                result_paths = candidate.get("result_paths", [])
                policy = normalize_artifact_policy(candidate.get("policy"))
                if not isinstance(inputs, list) or not all(isinstance(item, str) and item for item in inputs):
                    raise ValueError(f"Invalid artifact contract inputs for {remote_name}")
                if not isinstance(input_arrays, list) or not all(
                    isinstance(item, str) and item for item in input_arrays
                ):
                    raise ValueError(
                        f"Invalid artifact contract input_arrays for {remote_name}"
                    )
                if set(inputs).intersection(input_arrays):
                    raise ValueError(
                        f"Artifact contract input field cannot be scalar and array for {remote_name}"
                    )
                if not isinstance(outputs, list) or not all(isinstance(item, str) and item for item in outputs):
                    raise ValueError(f"Invalid artifact contract outputs for {remote_name}")
                if not isinstance(output_paths, dict):
                    raise ValueError(f"Invalid artifact contract output_paths for {remote_name}")
                if not isinstance(result_paths, list):
                    raise ValueError(f"Invalid artifact contract result_paths for {remote_name}")
                normalized_output_paths: dict[str, dict[str, str]] = {}
                for argument_name, specification in output_paths.items():
                    if not isinstance(argument_name, str) or not argument_name:
                        raise ValueError(f"Invalid managed output argument for {remote_name}")
                    if isinstance(specification, str):
                        specification = {"filename": specification}
                    if not isinstance(specification, dict):
                        raise ValueError(
                            f"Invalid output path specification for {remote_name}.{argument_name}"
                        )
                    filename = specification.get("filename")
                    mime_type = specification.get("mime_type")
                    if (
                        not isinstance(filename, str)
                        or not filename
                        or Path(filename).name != filename
                    ):
                        raise ValueError(
                            f"Invalid managed output filename for {remote_name}.{argument_name}"
                        )
                    normalized = {"filename": filename}
                    if mime_type is not None:
                        normalized["mime_type"] = normalize_mime_type(mime_type)
                    normalized_output_paths[argument_name] = normalized

                normalized_result_paths: list[dict[str, Any]] = []
                for index, specification in enumerate(result_paths):
                    if not isinstance(specification, dict):
                        raise ValueError(
                            f"Invalid result path specification for {remote_name}[{index}]"
                        )
                    unknown_fields = set(specification) - {
                        "pattern",
                        "group",
                        "root",
                        "remove_source",
                        "mime_type",
                    }
                    if unknown_fields:
                        raise ValueError(
                            f"Unknown result path fields for {remote_name}: "
                            + ", ".join(sorted(unknown_fields))
                        )
                    pattern = specification.get("pattern")
                    group = specification.get("group", "path")
                    root = specification.get("root")
                    remove_source = specification.get("remove_source", False)
                    mime_type = specification.get("mime_type")
                    if (
                        not isinstance(pattern, str)
                        or not pattern
                        or len(pattern) > 1024
                    ):
                        raise ValueError(
                            f"Invalid result path pattern for {remote_name}"
                        )
                    try:
                        compiled = re.compile(pattern)
                    except re.error as exc:
                        raise ValueError(
                            f"Invalid result path regex for {remote_name}"
                        ) from exc
                    if (
                        not isinstance(group, str)
                        or not group
                        or group not in compiled.groupindex
                    ):
                        raise ValueError(
                            f"Invalid result path group for {remote_name}"
                        )
                    if not isinstance(root, str) or not root:
                        raise ValueError(
                            f"Invalid result path root for {remote_name}"
                        )
                    root_path = Path(root)
                    if root_path.is_absolute() or ".." in root_path.parts:
                        raise ValueError(
                            f"Result path root must stay inside PLA root for {remote_name}"
                        )
                    if not isinstance(remove_source, bool):
                        raise ValueError(
                            f"Invalid result path remove_source for {remote_name}"
                        )
                    normalized_result = {
                        "pattern": pattern,
                        "group": group,
                        "root": root_path.as_posix(),
                        "remove_source": remove_source,
                    }
                    if mime_type is not None:
                        normalized_result["mime_type"] = normalize_mime_type(
                            mime_type
                        )
                    normalized_result_paths.append(normalized_result)

                artifact_contract = {
                    "transport": transport,
                    "inputs": list(inputs),
                    "input_arrays": list(input_arrays),
                    "outputs": list(outputs),
                    "output_paths": normalized_output_paths,
                    "result_paths": normalized_result_paths,
                    "policy": policy,
                }
                raw_inputs = list(inputs) + list(input_arrays)
                raw_outputs = list(outputs)

            raw_tags = fastmcp_meta.get("tags", [])
            tags = ["mcp"]
            if isinstance(raw_tags, (list, tuple, set)):
                tags.extend(
                    tag.casefold()
                    for tag in raw_tags
                    if isinstance(tag, str) and tag.strip()
                )

            override_tags = override.get("tags", [])
            if isinstance(override_tags, (list, tuple, set)):
                tags.extend(
                    tag.casefold()
                    for tag in override_tags
                    if isinstance(tag, str) and tag.strip()
                )
            input_schema = override.get("input_schema")
            if input_schema is not None and not isinstance(input_schema, dict):
                raise ValueError(f"Invalid input_schema override for {remote_name}")
            output_schema = override.get("output_schema")
            if output_schema is not None and not isinstance(output_schema, dict):
                raise ValueError(f"Invalid output_schema override for {remote_name}")
            risk_level = override.get("risk_level", "privileged")
            requires_confirmation = override.get("requires_confirmation", True)
            requires_transaction = override.get("requires_transaction", False)
            routing = override.get("routing", {})
            if not isinstance(routing, dict):
                raise ValueError(f"Invalid routing metadata for {remote_name}")
            if not isinstance(requires_transaction, bool):
                raise ValueError(
                    f"Invalid requires_transaction override for {remote_name}"
                )

            descriptors.append(CapabilityDescriptor(
                id=capability_id,
                provider_id=provider_id,
                remote_name=remote_name,
                title=(override.get("title") or tool.title or remote_name),
                description=(
                    override.get("description")
                    or tool.description
                    or f"External MCP tool {remote_name}."
                ),
                input_schema=dict(
                    input_schema
                    if input_schema is not None
                    else (tool.input_schema or {"type": "object", "properties": {}})
                ),
                output_schema=dict(
                    output_schema
                    if output_schema is not None
                    else (tool.output_schema or {})
                ),
                artifact_inputs=tuple(raw_inputs),
                artifact_outputs=bool(
                    raw_outputs
                    or artifact_contract.get("output_paths")
                    or artifact_contract.get("result_paths")
                ),
                artifact_contract=artifact_contract,
                risk_level=risk_level,
                requires_confirmation=bool(requires_confirmation),
                requires_transaction=requires_transaction,
                tags=tuple(dict.fromkeys(tags)),
                routing_authority=routing_authority,
                routing=dict(routing),
            ))
        return descriptors
