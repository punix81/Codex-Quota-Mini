"""Explicit registration at the application boundary, independent collection."""

from abc import ABC, abstractmethod
from collections.abc import Callable
import time

from .models import Capability, ErrorCode, ProviderSnapshot, State, identifier


class ProviderAdapter(ABC):
    provider_id: str
    account_id: str
    capabilities: tuple[Capability, ...]

    @abstractmethod
    def fetch(self) -> ProviderSnapshot:
        """Return normalized metrics; missing supported metrics remain explicit."""

    def close(self) -> None:
        """Release resources owned by the adapter."""


class ProviderRegistry:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.adapters: dict[tuple[str, str], ProviderAdapter] = {}
        self.clock = clock

    def register(self, adapter: ProviderAdapter) -> None:
        identifier(adapter.provider_id)
        identifier(adapter.account_id)
        key = (adapter.provider_id, adapter.account_id)
        if key in self.adapters:
            raise ValueError("Provider/account already registered")
        self.adapters[key] = adapter

    def collect(self) -> tuple[ProviderSnapshot, ...]:
        results = []
        for key, adapter in self.adapters.items():
            try:
                result = adapter.fetch()
                if (result.provider_id, result.account_id) != key:
                    raise ValueError("Adapter returned a different identity")
            except Exception:
                # Never expose exception text (which may contain credentials).
                result = ProviderSnapshot(*key, int(self.clock()), State.ERROR,
                                          error=ErrorCode.INTERNAL)
            results.append(result)
        return tuple(results)

    def close(self) -> None:
        for adapter in self.adapters.values():
            adapter.close()
