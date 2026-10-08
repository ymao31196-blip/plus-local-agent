from abc import ABC, abstractmethod


class ModelInputRequired(RuntimeError):
    """A backend needs its caller to complete an external input round trip."""


class ModelBackend(ABC):
    """Vendor-neutral interface for a text-generating model backend."""

    @abstractmethod
    async def generate(self, prompt: str) -> str:
        """Receive serialized reasoning context and return raw model text."""
        raise NotImplementedError
