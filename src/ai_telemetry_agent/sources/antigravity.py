"""Antigravity AI telemetry adapter and integration interface."""

import datetime
from typing import Any, Dict, List, Optional

from ai_telemetry_agent.core.event import AITelemetryPayload, EventType
from ai_telemetry_agent.logging.structured import get_logger, sanitize_data
from ai_telemetry_agent.sources.ai_provider import (
    AntigravityTelemetryProvider,
    HookProvider,
    MCPProvider,
)
from ai_telemetry_agent.sources.antigravity_probe import (
    AntigravityCapabilities,
    AntigravityCapabilityProbe,
)
from ai_telemetry_agent.sources.base import EventSource

logger = get_logger("telemetry.source.antigravity")

ANTIGRAVITY_AI_TELEMETRY_STATUS = "HOOK_SUPPORTED_TOKENS_REQUIRE_GATEWAY"


class AntigravitySource(EventSource):
    """Adapter for Antigravity AI usage telemetry.

    Adheres strictly to the AI telemetry schema without fabricating tokens or costs.
    If metrics are not explicitly provided by a valid integration source, fields remain NULL.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._started = False
        self._buffered_events: List[Dict[str, Any]] = []
        self._probe = AntigravityCapabilityProbe()
        self._capabilities: Optional[AntigravityCapabilities] = None
        self.hook_provider = HookProvider()
        self.mcp_provider = MCPProvider()

    @property
    def name(self) -> str:
        return "antigravity"

    @property
    def status(self) -> str:
        return ANTIGRAVITY_AI_TELEMETRY_STATUS

    def probe_capabilities(self) -> AntigravityCapabilities:
        self._capabilities = self._probe.probe()
        return self._capabilities

    def start(self) -> None:
        self._started = True
        caps = self.probe_capabilities()

        # Emit antigravity detected event
        if caps.antigravity_detected:
            self._buffered_events.append({
                "event_type": EventType.ANTIGRAVITY_DETECTED,
                "payload": caps.to_dict(),
            })

        logger.info(
            "antigravity_source_initialized",
            status=self.status,
            enabled=self.enabled,
            ide_detected=caps.ide_detected,
            hooks_available=caps.hooks_available,
            mcp_available=caps.mcp_available,
        )

    def stop(self) -> None:
        self._started = False

    def emit_ai_event(
        self,
        event_type: str,
        payload: AITelemetryPayload,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Adapter hook to emit legitimate AI telemetry events."""
        if not self._started or not self.enabled:
            return

        event_dict = {
            "event_type": event_type,
            "context": context or {},
            "payload": payload.to_dict(),
        }
        self._buffered_events.append(event_dict)

    def collect(self) -> List[Dict[str, Any]]:
        if not self._started or not self.enabled:
            return []

        events: List[Dict[str, Any]] = []

        if self._buffered_events:
            events.extend(self._buffered_events)
            self._buffered_events.clear()

        # Collect events from active capture providers
        events.extend(self.hook_provider.collect_events())
        events.extend(self.mcp_provider.collect_events())

        return events
