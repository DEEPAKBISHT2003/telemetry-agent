"""Base interface for all telemetry event sources."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class EventSource(ABC):
    """Abstract interface representing a telemetry event producer."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the event source (e.g. system, git, antigravity)."""
        pass

    @abstractmethod
    def start(self) -> None:
        """Initialize and start background workers or timers if any."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop any background listeners and release resources."""
        pass

    @abstractmethod
    def collect(self) -> List[Dict[str, Any]]:
        """Poll and return newly detected raw events as dictionaries."""
        pass
