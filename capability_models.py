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
    enabled: bool = True
    tags: tuple[str, ...] = ()

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
        })
        return value
