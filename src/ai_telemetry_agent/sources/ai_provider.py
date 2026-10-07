"""Provider-independent AI Telemetry Interface and Multi-Capture Provider Abstractions."""

from abc import ABC, abstractmethod
import datetime
from typing import Any, Dict, List, Optional

from ai_telemetry_agent.core.event import AITelemetryPayload, EventType
from ai_telemetry_agent.logging.structured import get_logger, sanitize_data

logger = get_logger("telemetry.source.ai_provider")


class AIEventBuilder:
    """Helper to construct strict, schema-compliant AI telemetry payloads with null safety."""

    @staticmethod
    def build_run_started(
        provider: Optional[str] = None,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        capture_method: Optional[str] = None,
        repository: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        started_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = AITelemetryPayload(
            provider=provider,
            model=model,
            session_id=session_id,
            run_id=run_id,
            started_at=started_at,
            agent_name=agent_name,
            capture_method=capture_method,
            metadata=sanitize_data(metadata or {}),
        )
        ctx: Dict[str, Any] = {"session_id": session_id}
        if repository:
            ctx["repository"] = repository

        return {
            "event_type": EventType.AI_RUN_STARTED,
            "context": ctx,
            "payload": payload.to_dict(),
        }

    @staticmethod
    def build_run_completed(
        provider: Optional[str] = None,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        started_at: Optional[str] = None,
        duration_ms: Optional[float] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        cache_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        cost_usd: Optional[float] = None,
        turns: Optional[int] = None,
        agent_name: Optional[str] = None,
        capture_method: Optional[str] = None,
        repository: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        completed_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = AITelemetryPayload(
            provider=provider,
            model=model,
            session_id=session_id,
            run_id=run_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_tokens=cache_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            turns=turns,
            agent_name=agent_name,
            capture_method=capture_method,
            metadata=sanitize_data(metadata or {}),
        )
        ctx: Dict[str, Any] = {"session_id": session_id}
        if repository:
            ctx["repository"] = repository

        return {
            "event_type": EventType.AI_RUN_COMPLETED,
            "context": ctx,
            "payload": payload.to_dict(),
        }

    @staticmethod
    def build_run_failed(
        provider: Optional[str] = None,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        error_message: Optional[str] = None,
        capture_method: Optional[str] = None,
        repository: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        meta = dict(metadata or {})
        if error_message:
            meta["error"] = error_message

        payload = AITelemetryPayload(
            provider=provider,
            model=model,
            session_id=session_id,
            run_id=run_id,
            capture_method=capture_method,
            metadata=sanitize_data(meta),
        )
        ctx: Dict[str, Any] = {"session_id": session_id}
        if repository:
            ctx["repository"] = repository

        return {
            "event_type": EventType.AI_RUN_FAILED,
            "context": ctx,
            "payload": payload.to_dict(),
        }

    @staticmethod
    def build_tool_call(
        tool_name: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        duration_ms: Optional[float] = None,
        capture_method: Optional[str] = None,
        repository: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = AITelemetryPayload(
            session_id=session_id,
            run_id=run_id,
            tool_name=tool_name,
            duration_ms=duration_ms,
            capture_method=capture_method,
            metadata=sanitize_data(metadata or {}),
        )
        ctx: Dict[str, Any] = {"session_id": session_id}
        if repository:
            ctx["repository"] = repository

        return {
            "event_type": EventType.AI_TOOL_CALL,
            "context": ctx,
            "payload": payload.to_dict(),
        }

    @staticmethod
    def build_turn(
        turns: int,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        capture_method: Optional[str] = None,
        repository: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = AITelemetryPayload(
            session_id=session_id,
            model=model,
            turns=turns,
            capture_method=capture_method,
            metadata=sanitize_data(metadata or {}),
        )
        ctx: Dict[str, Any] = {"session_id": session_id}
        if repository:
            ctx["repository"] = repository

        return {
            "event_type": EventType.AI_TURN,
            "context": ctx,
            "payload": payload.to_dict(),
        }


# Capture Method Providers


class AntigravityTelemetryProvider(ABC):
    """Base abstract provider for capturing Antigravity AI telemetry."""

    @property
    @abstractmethod
    def capture_method_name(self) -> str:
        """Name of the capture mechanism."""
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Whether this capture provider is verified and available."""
        pass

    @abstractmethod
    def collect_events(self) -> List[Dict[str, Any]]:
        """Fetch newly captured events."""
        pass


class OfficialExtensionProvider(AntigravityTelemetryProvider):
    """Provider for official IDE extension telemetry events (currently not exposed by Antigravity)."""

    @property
    def capture_method_name(self) -> str:
        return "official_extension"

    @property
    def is_available(self) -> bool:
        return False

    def collect_events(self) -> List[Dict[str, Any]]:
        return []


class MCPProvider(AntigravityTelemetryProvider):
    """Provider for MCP server telemetry."""

    def __init__(self):
        self._buffered_events: List[Dict[str, Any]] = []

    @property
    def capture_method_name(self) -> str:
        return "mcp"

    @property
    def is_available(self) -> bool:
        return True

    def record_mcp_tool_invocation(
        self,
        tool_name: str,
        session_id: Optional[str] = None,
        repository: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = AIEventBuilder.build_tool_call(
            tool_name=tool_name,
            session_id=session_id,
            capture_method=self.capture_method_name,
            repository=repository,
            metadata={"mcp_transport": "stdio"},
        )
        self._buffered_events.append(event)

    def collect_events(self) -> List[Dict[str, Any]]:
        events = list(self._buffered_events)
        self._buffered_events.clear()
        return events


class HookProvider(AntigravityTelemetryProvider):
    """Provider for Antigravity lifecycle hooks (.agents/hooks.json)."""

    def __init__(self):
        self._buffered_events: List[Dict[str, Any]] = []

    @property
    def capture_method_name(self) -> str:
        return "hooks"

    @property
    def is_available(self) -> bool:
        return True

    def record_hook_event(
        self,
        hook_name: str,
        conversation_id: Optional[str] = None,
        model_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        step_idx: Optional[int] = None,
        error: Optional[str] = None,
        repository: Optional[Dict[str, Any]] = None,
    ) -> None:
        if hook_name in ("PreInvocation", "pre_invocation"):
            event = AIEventBuilder.build_run_started(
                provider="antigravity",
                model=model_name,
                session_id=conversation_id,
                capture_method=self.capture_method_name,
                repository=repository,
                metadata={"step_idx": step_idx} if step_idx is not None else {},
            )
        elif hook_name in ("Stop", "stop"):
            if error:
                event = AIEventBuilder.build_run_failed(
                    provider="antigravity",
                    model=model_name,
                    session_id=conversation_id,
                    error_message=error,
                    capture_method=self.capture_method_name,
                    repository=repository,
                )
            else:
                event = AIEventBuilder.build_run_completed(
                    provider="antigravity",
                    model=model_name,
                    session_id=conversation_id,
                    capture_method=self.capture_method_name,
                    repository=repository,
                )
        elif hook_name in ("PreToolUse", "PostToolUse", "pre_tool_use", "post_tool_use"):
            event = AIEventBuilder.build_tool_call(
                tool_name=tool_name or "unknown_tool",
                session_id=conversation_id,
                capture_method=self.capture_method_name,
                repository=repository,
                metadata={"hook": hook_name, "step_idx": step_idx, "error": error},
            )
        else:
            return

        self._buffered_events.append(event)

    def collect_events(self) -> List[Dict[str, Any]]:
        events = list(self._buffered_events)
        self._buffered_events.clear()
        return events


class ProxyProvider(AntigravityTelemetryProvider):
    """Provider for an HTTP/API Gateway proxy capturing exact token & cost headers."""

    @property
    def capture_method_name(self) -> str:
        return "proxy_gateway"

    @property
    def is_available(self) -> bool:
        return False

    def collect_events(self) -> List[Dict[str, Any]]:
        return []


class UnsupportedProvider(AntigravityTelemetryProvider):
    """Fallback provider when no capture mechanism is supported."""

    @property
    def capture_method_name(self) -> str:
        return "unsupported"

    @property
    def is_available(self) -> bool:
        return False

    def collect_events(self) -> List[Dict[str, Any]]:
        return []
