from typing import Any

from decision_parser import parse_decision
from model_backend import ModelBackend
from prompt_builder import build_agent_prompt
from reasoner import BaseReasoner


class GenericLLMReasoner(BaseReasoner):
    """Turn generic agent context into a parsed decision via a backend."""

    def __init__(self, backend: ModelBackend) -> None:
        self.backend = backend

    async def decide(
        self,
        task: str,
        observations: Any,
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = build_agent_prompt(task, observations, tools)
        raw_response = await self.backend.generate(prompt)
        return parse_decision(raw_response)
