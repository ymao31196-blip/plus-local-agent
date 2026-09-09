from abc import ABC, abstractmethod
from typing import Any

from llm_interface import LLMReasoner


class BaseReasoner(LLMReasoner, ABC):

    @abstractmethod
    async def decide(
        self,
        task: str,
        observations: Any,
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raise NotImplementedError
