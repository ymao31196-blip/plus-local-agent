"""Artifact resource policy for PLA v0.19.

Capability manifests may tighten these limits but cannot exceed PLA hard caps.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any


MIB = 1024 * 1024

DEFAULT_MAX_INPUT_BYTES = 64 * MIB
DEFAULT_MAX_OUTPUT_BYTES = 64 * MIB
DEFAULT_MAX_TOTAL_OUTPUT_BYTES = 128 * MIB
DEFAULT_MAX_OUTPUT_ARTIFACTS = 8
DEFAULT_OUTPUT_TTL_SECONDS = 600

HARD_MAX_INPUT_BYTES = 256 * MIB
HARD_MAX_OUTPUT_BYTES = 256 * MIB
HARD_MAX_TOTAL_OUTPUT_BYTES = 512 * MIB
HARD_MAX_OUTPUT_ARTIFACTS = 32
HARD_MAX_OUTPUT_TTL_SECONDS = 3600

_MIME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/(?:[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*|\*)$"
)


def normalize_mime_type(value: str) -> str:
    if not isinstance(value, str) or not _MIME_RE.fullmatch(value.strip()):
        raise ValueError(f"Invalid MIME type: {value!r}")
    return value.strip().casefold()


def mime_matches(mime_type: str, allowed: list[str] | tuple[str, ...]) -> bool:
    actual = normalize_mime_type(mime_type)
    for candidate in allowed:
        normalized = normalize_mime_type(candidate)
        if normalized == actual:
            return True
        if normalized.endswith("/*") and actual.startswith(normalized[:-1]):
            return True
    return False


def validate_artifact_content(path: Path, mime_type: str) -> None:
    """Validate lightweight signatures for formats PLA explicitly understands."""
    normalized = normalize_mime_type(mime_type)
    if normalized.startswith("text/"):
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"Artifact content does not match MIME type {normalized!r}"
            ) from exc
        return

    if normalized == "application/pdf":
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise ValueError(
                    "Artifact content does not match MIME type 'application/pdf'"
                )
        return

    office_members = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            {"[Content_Types].xml", "word/document.xml"},
        "application/vnd.openxmlformats-officedocument.presentationml.presentation":
            {"[Content_Types].xml", "ppt/presentation.xml"},
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            {"[Content_Types].xml", "xl/workbook.xml"},
    }
    required = office_members.get(normalized)
    if required is None:
        return
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(
            f"Artifact content does not match MIME type {normalized!r}"
        ) from exc
    if not required <= names:
        raise ValueError(
            f"Artifact content does not match MIME type {normalized!r}"
        )


def _bounded_int(
    value: Any,
    *,
    label: str,
    default: int,
    hard_max: int,
) -> int:
    if value is None:
        return default
    if type(value) is not int or not 1 <= value <= hard_max:
        raise ValueError(f"{label} must be between 1 and {hard_max}")
    return value


def _mime_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be an array of MIME types")
    normalized = [normalize_mime_type(item) for item in value]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} contains duplicate MIME types")
    return normalized


def normalize_artifact_policy(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("artifact policy must be an object")

    unknown = set(value) - {
        "max_input_bytes",
        "max_output_bytes",
        "max_total_output_bytes",
        "max_output_artifacts",
        "output_ttl_seconds",
        "allowed_input_mime_types",
        "allowed_output_mime_types",
    }
    if unknown:
        raise ValueError(
            "Unknown artifact policy fields: " + ", ".join(sorted(unknown))
        )

    max_output_bytes = _bounded_int(
        value.get("max_output_bytes"),
        label="max_output_bytes",
        default=DEFAULT_MAX_OUTPUT_BYTES,
        hard_max=HARD_MAX_OUTPUT_BYTES,
    )
    max_total_output_bytes = _bounded_int(
        value.get("max_total_output_bytes"),
        label="max_total_output_bytes",
        default=DEFAULT_MAX_TOTAL_OUTPUT_BYTES,
        hard_max=HARD_MAX_TOTAL_OUTPUT_BYTES,
    )
    if max_total_output_bytes < max_output_bytes:
        raise ValueError(
            "max_total_output_bytes must be >= max_output_bytes"
        )

    return {
        "max_input_bytes": _bounded_int(
            value.get("max_input_bytes"),
            label="max_input_bytes",
            default=DEFAULT_MAX_INPUT_BYTES,
            hard_max=HARD_MAX_INPUT_BYTES,
        ),
        "max_output_bytes": max_output_bytes,
        "max_total_output_bytes": max_total_output_bytes,
        "max_output_artifacts": _bounded_int(
            value.get("max_output_artifacts"),
            label="max_output_artifacts",
            default=DEFAULT_MAX_OUTPUT_ARTIFACTS,
            hard_max=HARD_MAX_OUTPUT_ARTIFACTS,
        ),
        "output_ttl_seconds": _bounded_int(
            value.get("output_ttl_seconds"),
            label="output_ttl_seconds",
            default=DEFAULT_OUTPUT_TTL_SECONDS,
            hard_max=HARD_MAX_OUTPUT_TTL_SECONDS,
        ),
        "allowed_input_mime_types": _mime_list(
            value.get("allowed_input_mime_types"),
            "allowed_input_mime_types",
        ),
        "allowed_output_mime_types": _mime_list(
            value.get("allowed_output_mime_types"),
            "allowed_output_mime_types",
        ),
    }
