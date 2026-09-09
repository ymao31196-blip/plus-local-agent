"""Model backend backed only by the current MCP client's sampling support."""

from __future__ import annotations

from typing import Any

import mcp_types
from mcp_types.version import is_version_at_least

from model_backend import ModelBackend, ModelInputRequired
from e2e_debug import E2EDebugTrace


SAMPLING_KEY = "agent_sampling"
SYSTEM_PROMPT = "Return only the strict JSON decision requested by the user prompt."


class SamplingUnsupported(RuntimeError):
    """The connected MCP client did not advertise sampling support."""


class SamplingRequired(ModelInputRequired):
    """The modern MCP request must suspend for an MRTR sampling response."""

    def __init__(self, request: mcp_types.CreateMessageRequest) -> None:
        super().__init__("MCP client sampling response required")
        self.request = request


class SamplingResponseError(RuntimeError):
    """The MCP client returned an explicit sampling error."""


class MCPSamplingBackend(ModelBackend):
    """Convert a prompt to raw client-model text, without provider credentials."""

    def __init__(
        self,
        ctx: Any,
        *,
        max_tokens: int = 1200,
        trace: E2EDebugTrace | None = None,
        sampling_sequence: int = 1,
    ) -> None:
        self.ctx = ctx
        self.max_tokens = max_tokens
        self.trace = trace
        self.sampling_sequence = sampling_sequence
        self._response_consumed = False

    def _request(self, prompt: str) -> mcp_types.CreateMessageRequest:
        return mcp_types.CreateMessageRequest(
            params=mcp_types.CreateMessageRequestParams(
                messages=[mcp_types.SamplingMessage(
                    role="user", content=mcp_types.TextContent(type="text", text=prompt)
                )],
                system_prompt=SYSTEM_PROMPT, temperature=0, max_tokens=self.max_tokens,
            )
        )

    @staticmethod
    def _extract_text(result: Any) -> str:
        if isinstance(result, mcp_types.ErrorData):
            raise SamplingResponseError(
                f"SamplingResponseError: MCP client rejected sampling: {result.message}"
            )
        text = getattr(getattr(result, "content", None), "text", None)
        if not isinstance(text, str):
            raise TypeError("MCP sampling response did not contain text content")
        return text

    async def generate(self, prompt: str) -> str:
        capabilities = self.ctx.session.client_capabilities
        if capabilities is None or capabilities.sampling is None:
            raise SamplingUnsupported(
                "SamplingUnsupported: MCP client did not declare sampling capability"
            )
        protocol_version = self.ctx.request_context.protocol_version
        if is_version_at_least(protocol_version, "2026-07-28"):
            responses = self.ctx.input_responses or {}
            if not self._response_consumed and SAMPLING_KEY in responses:
                self._response_consumed = True
                text = self._extract_text(responses[SAMPLING_KEY])
                if self.trace:
                    self.trace.emit(
                        "sampling_response",
                        sampling_sequence=self.sampling_sequence,
                        raw_model_response=text,
                    )
                return text
            if self.trace:
                self.trace.emit(
                    "sampling_requested",
                    sampling_sequence=self.sampling_sequence,
                    prompt=prompt,
                )
            raise SamplingRequired(self._request(prompt))
        if self.trace:
            self.trace.emit(
                "sampling_requested",
                sampling_sequence=self.sampling_sequence,
                prompt=prompt,
            )
        result = await self.ctx.session.create_message(
            messages=self._request(prompt).params.messages,
            system_prompt=SYSTEM_PROMPT, temperature=0, max_tokens=self.max_tokens,
            related_request_id=self.ctx.request_id,
        )
        text = self._extract_text(result)
        if self.trace:
            self.trace.emit(
                "sampling_response",
                sampling_sequence=self.sampling_sequence,
                raw_model_response=text,
            )
        return text
