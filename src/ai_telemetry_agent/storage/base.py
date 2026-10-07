"""Abstract base class for telemetry event storage."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ai_telemetry_agent.core.event import TelemetryEvent
else:
    TelemetryEvent = Any


class EventStore(ABC):
    """Abstract interface for local event persistence."""

    @abstractmethod
    def save_event(self, event: TelemetryEvent) -> bool:
        """Save a single normalized event. Returns True if saved, False if duplicate/error."""
        pass

    @abstractmethod
    def get_event_by_id(self, event_id: str) -> Optional[TelemetryEvent]:
        """Retrieve an event by its unique ID."""
        pass

    @abstractmethod
    def get_events(
        self,
        limit: int = 50,
        offset: int = 0,
        event_type: Optional[str] = None,
        member_id: Optional[str] = None,
        repository_name: Optional[str] = None,
        session_id: Optional[str] = None,
        descending: bool = True
    ) -> List[TelemetryEvent]:
        """Query stored events with filtering and pagination."""
        pass

    @abstractmethod
    def get_count(self, event_type: Optional[str] = None, repository_name: Optional[str] = None) -> int:
        """Return the total number of events stored."""
        pass

    @abstractmethod
    def get_sessions(
        self,
        repository_name: Optional[str] = None,
        member_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Query and aggregate distinct session records with repository metrics."""
        pass

    @abstractmethod
    def get_repository_summary(self, repository_name: Optional[str] = None) -> Dict[str, Any]:
        """Calculate aggregated session, AI run, and tool call counts per repository."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Cleanly close storage resources."""
        pass
