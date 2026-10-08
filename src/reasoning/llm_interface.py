from abc import ABC, abstractmethod
from typing import Any


class LLMReasoner(ABC):
    """Interface shared by all reasoning backends."""

    @abstractmethod
    async def decide(
        self,
        task: str,
        observations: Any,
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Return a structured action and its arguments."""
        raise NotImplementedError
