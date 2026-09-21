"""Capability data models for PLA v0.10.

The capability layer describes executable abilities but does not choose or invoke them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


CAPABILITY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+$")
PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

RiskLevel = str

ROUTING_AUTHORITIES = frozenset({
    "recommendation",
    "preferred",
    "enforced",
})
_ROUTING_RELATION_KEYS = frozenset({
    "preferred_over",
    "fallback_for",
    "supersedes",
})


def _validate_routing_relation(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    unknown = set(value) - {"capability_id", "when"}
    if unknown:
        raise ValueError(
            f"{label} has unknown fields: " + ", ".join(sorted(unknown))
        )
    capability_id = value.get("capability_id")
    if not isinstance(capability_id, str) or not CAPABILITY_ID_RE.fullmatch(
        capability_id
    ):
        raise ValueError(f"{label}.capability_id must be a valid capability id")

    when = value.get("when")
    if when is None:
        return
    if not isinstance(when, dict):
        raise TypeError(f"{label}.when must be an object")
    unknown_when = set(when) - {"argument", "contains_any", "equals_any"}
    if unknown_when:
        raise ValueError(
            f"{label}.when has unknown fields: "
            + ", ".join(sorted(unknown_when))
        )
    argument = when.get("argument")
    if not isinstance(argument, str) or not argument.strip():
        raise ValueError(f"{label}.when.argument must be a non-empty string")

    matcher_count = 0
    for field_name in ("contains_any", "equals_any"):
        raw = when.get(field_name)
        if raw is None:
            continue
        matcher_count += 1
        if (
            not isinstance(raw, list)
            or not raw
            or any(not isinstance(item, str) or not item.strip() for item in raw)
        ):
            raise ValueError(
                f"{label}.when.{field_name} must be a non-empty string array"
            )
    if matcher_count != 1:
        raise ValueError(
            f"{label}.when must define exactly one of contains_any or equals_any"
        )


def _validate_routing_metadata(value: dict[str, Any]) -> None:
    unknown = set(value) - _ROUTING_RELATION_KEYS
    if unknown:
        raise ValueError(
            "routing has unknown fields: " + ", ".join(sorted(unknown))
        )
    for relation_name, relations in value.items():
        if not isinstance(relations, list):
            raise TypeError(f"routing.{relation_name} must be an array")
        for index, relation in enumerate(relations):
            _validate_routing_relation(
                relation,
                f"routing.{relation_name}[{index}]",
            )


@dataclass(frozen=True)
class CapabilityDescriptor:
    id: str
    provider_id: str
    remote_name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] = field(default_factory=dict)
    artifact_inputs: tuple[str, ...] = ()
    artifact_outputs: bool = False
    artifact_contract: dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = "read"
    requires_confirmation: bool = False
    requires_transaction: bool = False
    enabled: bool = True
    tags: tuple[str, ...] = ()
    routing_authority: str = "recommendation"
    routing: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not CAPABILITY_ID_RE.fullmatch(self.id):
            raise ValueError(f"Invalid capability id: {self.id!r}")
        if not PROVIDER_ID_RE.fullmatch(self.provider_id):
            raise ValueError(f"Invalid provider id: {self.provider_id!r}")
        expected_prefix = f"{self.provider_id}."
        if not self.id.startswith(expected_prefix):
            raise ValueError("Capability id must be namespaced by provider_id")
        for name, value in (
            ("remote_name", self.remote_name),
            ("title", self.title),
            ("description", self.description),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.input_schema, dict) or not isinstance(self.output_schema, dict):
            raise TypeError("input_schema and output_schema must be dictionaries")
        if not isinstance(self.artifact_contract, dict):
            raise TypeError("artifact_contract must be a dictionary")
        if any(not isinstance(item, str) or not item for item in self.artifact_inputs):
            raise ValueError("artifact_inputs must contain non-empty strings")
        if any(not isinstance(item, str) or not item for item in self.tags):
            raise ValueError("tags must contain non-empty strings")
        if self.risk_level not in {
            "read", "write_local", "write_external", "destructive", "privileged"
        }:
            raise ValueError(f"Unknown risk_level: {self.risk_level!r}")
        if self.routing_authority not in ROUTING_AUTHORITIES:
            raise ValueError(
                f"Unknown routing_authority: {self.routing_authority!r}"
            )
        if not isinstance(self.routing, dict):
            raise TypeError("routing must be a dictionary")
        _validate_routing_metadata(self.routing)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "title": self.title,
            "description": self.description,
            "artifact_inputs": list(self.artifact_inputs),
            "artifact_outputs": self.artifact_outputs,
            "risk_level": self.risk_level,
            "requires_confirmation": self.requires_confirmation,
            "requires_transaction": self.requires_transaction,
            "enabled": self.enabled,
            "tags": list(self.tags),
        }

    def detail(self) -> dict[str, Any]:
        value = self.summary()
        value.update({
            "remote_name": self.remote_name,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "artifact_contract": self.artifact_contract,
            "routing_authority": self.routing_authority,
            "routing": self.routing,
        })
        return value
