"""AI Telemetry Agent - Local developer telemetry agent for AI usage and engineering signals."""

__version__ = "0.1.0"
__package_name__ = "ai-telemetry-agent"

from ai_telemetry_agent.config.settings import Settings, load_settings
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.core.event import TelemetryEvent, EventType
from ai_telemetry_agent.storage.sqlite_store import SQLiteEventStore

__all__ = [
    "__version__",
    "__package_name__",
    "Settings",
    "load_settings",
    "TelemetryCollector",
    "TelemetryEvent",
    "EventType",
    "SQLiteEventStore",
]
