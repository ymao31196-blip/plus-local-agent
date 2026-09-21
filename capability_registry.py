"""Deterministic capability registry for PLA v0.10."""

from __future__ import annotations

from collections import defaultdict
import re
from typing import Iterable

from capability_models import CapabilityDescriptor


TOKEN_RE = re.compile(r"[a-z0-9_\-\.]+", re.IGNORECASE)


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityDescriptor] = {}
        self._provider_members: dict[str, set[str]] = defaultdict(set)
        self._provider_enabled: dict[str, bool] = {}

    def register_provider(
        self,
        provider_id: str,
        capabilities: Iterable[CapabilityDescriptor],
        *,
        enabled: bool = True,
    ) -> None:
        incoming = list(capabilities)
        if any(item.provider_id != provider_id for item in incoming):
            raise ValueError("Every capability must match the registering provider_id")

        ids = [item.id for item in incoming]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Provider {provider_id!r} returned duplicate capability ids")

        foreign_conflicts = [
            capability_id
            for capability_id in ids
            if capability_id in self._capabilities
            and self._capabilities[capability_id].provider_id != provider_id
        ]
        if foreign_conflicts:
            raise ValueError(
                "Capability id conflict across providers: "
                + ", ".join(sorted(foreign_conflicts))
            )

        previous = self._provider_members.get(provider_id, set())
        for capability_id in previous:
            self._capabilities.pop(capability_id, None)

        self._provider_members[provider_id] = set(ids)
        self._provider_enabled[provider_id] = bool(enabled)
        for descriptor in incoming:
            self._capabilities[descriptor.id] = descriptor

    def set_provider_enabled(self, provider_id: str, enabled: bool) -> None:
        if provider_id not in self._provider_members:
            raise ValueError(f"Unknown provider: {provider_id}")
        self._provider_enabled[provider_id] = bool(enabled)

    def remove_provider(self, provider_id: str) -> None:
        if provider_id not in self._provider_members:
            raise ValueError(f"Unknown provider: {provider_id}")
        for capability_id in self._provider_members.pop(provider_id):
            self._capabilities.pop(capability_id, None)
        self._provider_enabled.pop(provider_id, None)

    def describe(self, capability_id: str) -> dict:
        descriptor = self._capabilities.get(capability_id)
        if descriptor is None:
            raise ValueError(f"Unknown capability: {capability_id}")
        value = descriptor.detail()
        value["available"] = bool(
            descriptor.enabled and self._provider_enabled.get(descriptor.provider_id, False)
        )
        return value

    def search(
        self,
        query: str,
        *,
        provider_id: str | None = None,
        include_unavailable: bool = False,
        limit: int = 20,
    ) -> dict:
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if provider_id is not None and provider_id not in self._provider_members:
            raise ValueError(f"Unknown provider: {provider_id}")

        tokens = [token.casefold() for token in TOKEN_RE.findall(query)]
        ranked: list[tuple[int, str, CapabilityDescriptor, bool]] = []
        for descriptor in self._capabilities.values():
            if provider_id is not None and descriptor.provider_id != provider_id:
                continue
            available = bool(
                descriptor.enabled and self._provider_enabled.get(descriptor.provider_id, False)
            )
            if not available and not include_unavailable:
                continue

            haystack = " ".join((
                descriptor.id,
                descriptor.provider_id,
                descriptor.remote_name,
                descriptor.title,
                descriptor.description,
                " ".join(descriptor.tags),
            )).casefold()
            if tokens and not all(token in haystack for token in tokens):
                continue

            score = 0
            for token in tokens:
                if token in descriptor.id.casefold():
                    score += 8
                if token in descriptor.title.casefold():
                    score += 5
                if token in descriptor.tags:
                    score += 4
                if token in descriptor.description.casefold():
                    score += 2
            ranked.append((-score, descriptor.id, descriptor, available))

        ranked.sort(key=lambda item: (item[0], item[1]))
        selected = ranked[:limit]
        return {
            "query": query,
            "provider_id": provider_id,
            "capabilities": [
                {**descriptor.summary(), "available": available}
                for _score, _id, descriptor, available in selected
            ],
            "match_count": len(ranked),
            "returned_count": len(selected),
            "truncated": len(ranked) > limit,
        }

    def snapshot(
        self,
        *,
        include_unavailable: bool = True,
    ) -> dict:
        """Return a complete deterministic registry snapshot without search limits."""
        capabilities: list[dict] = []
        for capability_id in sorted(self._capabilities):
            descriptor = self._capabilities[capability_id]
            available = bool(
                descriptor.enabled
                and self._provider_enabled.get(descriptor.provider_id, False)
            )
            if not available and not include_unavailable:
                continue
            capabilities.append(
                {
                    **descriptor.summary(),
                    "remote_name": descriptor.remote_name,
                    "routing_authority": descriptor.routing_authority,
                    "routing": descriptor.routing,
                    "available": available,
                }
            )

        providers = dict(sorted(self._provider_enabled.items()))
        return {
            "capability_count": len(capabilities),
            "provider_count": len(providers),
            "providers": providers,
            "capabilities": capabilities,
        }

    def provider_status(self) -> dict[str, bool]:
        return dict(sorted(self._provider_enabled.items()))
