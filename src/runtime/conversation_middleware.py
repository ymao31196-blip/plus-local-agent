"""Automatic, privacy-preserving ChatGPT session attribution at the MCP tool boundary.

Uses the host-provided _meta["openai/session"] only when present. Never
guesses a conversation identity from transport connections, request IDs,
tool arguments, IP addresses, or model-generated registration keys.
"""
from __future__ import annotations

from uuid import uuid4

from fastmcp.server.middleware import Middleware

from runtime import conversation_activity


class AutoConversationMiddleware(Middleware):
    """Record one start/terminal pair per real MCP tool call; fail open."""

    async def on_call_tool(self, context, call_next):
        fastmcp_context = context.fastmcp_context
        identity = conversation_activity.session_identity(fastmcp_context)
        # The Desktop UI polls its own Runtime about every 10 seconds. Those
        # calls have no ChatGPT session and must not create missing-ID events.
        # Do not use this hint for policy, permissions, or authentication.
        if conversation_activity.is_desktop_management_request(fastmcp_context):
            return await call_next(context)
        subject = conversation_activity.safe_tool_name(
            getattr(context.message, "name", None)
        )
        call_id = uuid4().hex

        conversation_activity.emit_tool_activity(
            "conversation.tool_started",
            correlation_id=call_id,
            tool=subject,
            fingerprint=identity["fingerprint"],
        )
        try:
            result = await call_next(context)
        except BaseException:
            conversation_activity.emit_tool_activity(
                "conversation.tool_failed",
                correlation_id=call_id,
                tool=subject,
                fingerprint=identity["fingerprint"],
            )
            raise
        else:
            conversation_activity.emit_tool_activity(
                "conversation.tool_finished",
                correlation_id=call_id,
                tool=subject,
                fingerprint=identity["fingerprint"],
            )
            return result
