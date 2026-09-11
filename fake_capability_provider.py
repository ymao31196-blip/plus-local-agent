"""Deterministic fake provider used to validate the v0.10 capability plane."""

from __future__ import annotations

from capability_models import CapabilityDescriptor


class FakeCapabilityProvider:
    provider_id = "fake"

    def discover(self) -> list[CapabilityDescriptor]:
        return [
            CapabilityDescriptor(
                id="fake.pdf.compress",
                provider_id=self.provider_id,
                remote_name="compress_pdf",
                title="Compress PDF",
                description="Compress a PDF artifact while preserving it as a document.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "input_artifact": {"type": "string"},
                        "quality": {
                            "type": "string",
                            "enum": ["recommended", "maximum"],
                            "default": "recommended",
                        },
                    },
                    "required": ["input_artifact"],
                    "additionalProperties": False,
                },
                output_schema={"type": "object"},
                artifact_inputs=("input_artifact",),
                artifact_outputs=True,
                risk_level="write_local",
                tags=("pdf", "compress", "document"),
            ),
            CapabilityDescriptor(
                id="fake.word.create",
                provider_id=self.provider_id,
                remote_name="create_word",
                title="Create Word Document",
                description="Create a Word document from structured content.",
                input_schema={
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                    "additionalProperties": False,
                },
                output_schema={"type": "object"},
                artifact_outputs=True,
                risk_level="write_local",
                tags=("word", "docx", "document"),
            ),
            CapabilityDescriptor(
                id="fake.image.resize",
                provider_id=self.provider_id,
                remote_name="resize_image",
                title="Resize Image",
                description="Resize an image artifact to requested dimensions.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "input_artifact": {"type": "string"},
                        "width": {"type": "integer", "minimum": 1},
                        "height": {"type": "integer", "minimum": 1},
                    },
                    "required": ["input_artifact", "width", "height"],
                    "additionalProperties": False,
                },
                output_schema={"type": "object"},
                artifact_inputs=("input_artifact",),
                artifact_outputs=True,
                risk_level="write_local",
                tags=("image", "resize"),
            ),
        ]
