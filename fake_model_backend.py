from collections.abc import Iterable

from model_backend import ModelBackend


class FakeModelResponsesExhausted(RuntimeError):
    """Raised when a scripted backend has no response left to return."""


class FakeModelBackend(ModelBackend):
    """Return scripted responses in order without performing any reasoning."""

    def __init__(self, responses: Iterable[str]) -> None:
        self._responses = list(responses)
        self.received_prompts: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.received_prompts.append(prompt)
        if not self._responses:
            raise FakeModelResponsesExhausted(
                "FakeModelBackend response queue is exhausted"
            )
        return self._responses.pop(0)
