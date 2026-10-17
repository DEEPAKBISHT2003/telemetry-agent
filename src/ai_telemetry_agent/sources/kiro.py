"""Unified Kiro Telemetry Source.

Coordinates Kiro IDE and Kiro CLI telemetry collection without mixing parsers or duplicating events.
Provides capability probing and state management across Kiro components.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from ai_telemetry_agent.core.event import EventType
from ai_telemetry_agent.logging.structured import get_logger
from ai_telemetry_agent.sources.base import EventSource
from ai_telemetry_agent.sources.kiro_cli_sessions import KiroCLISessionSource
from ai_telemetry_agent.sources.kiro_ide_sessions import KiroIDESessionSource

logger = get_logger("telemetry.source.kiro")

KIRO_AI_TELEMETRY_STATUS = "CREDITS_LOCAL_SUPPORTED"


class KiroSource(EventSource):
    """Event source coordinating Kiro telemetry collection based on configured mode."""

    def __init__(
        self,
        mode: str = "kiro_cli",
        enabled: bool = True,
        cli_sessions_dir: Optional[Path] = None,
        ide_sessions_dir: Optional[Path] = None,
        cli_state_file: Optional[Path] = None,
        ide_state_file: Optional[Path] = None,
    ):
        self.mode = mode
        self.enabled = enabled
        self._started = False

        self.cli_source = KiroCLISessionSource(
            enabled=(mode in ("kiro_cli", "auto") and enabled),
            sessions_dir=cli_sessions_dir,
            state_file=cli_state_file,
        )
        self.ide_source = KiroIDESessionSource(
            enabled=(mode in ("kiro_ide", "auto") and enabled),
            sessions_dir=ide_sessions_dir,
            state_file=ide_state_file,
        )

    @property
    def name(self) -> str:
        return "kiro"

    @property
    def status(self) -> str:
        return KIRO_AI_TELEMETRY_STATUS

    def start(self) -> None:
        self._started = True
        if self.mode == "kiro_cli":
            self.cli_source.start()
        elif self.mode == "kiro_ide":
            self.ide_source.start()
        elif self.mode == "auto":
            # In auto mode, check what exists and activate appropriately without duplicate collection
            cli_exists = self.cli_source.sessions_dir.exists()
            ide_exists = self.ide_source.sessions_dir.exists()
            if cli_exists and not ide_exists:
                self.cli_source.start()
            elif ide_exists and not cli_exists:
                self.ide_source.start()
            else:
                # Both exist: activate CLI as primary session source to prevent duplicate prompt counting
                self.cli_source.start()

        logger.info(
            "kiro_source_started",
            mode=self.mode,
            enabled=self.enabled,
            cli_active=self.cli_source._started,
            ide_active=self.ide_source._started,
        )

    def stop(self) -> None:
        self._started = False
        if self.cli_source._started:
            self.cli_source.stop()
        if self.ide_source._started:
            self.ide_source.stop()
        logger.info("kiro_source_stopped")

    def collect(self) -> List[Dict[str, Any]]:
        if not self._started or not self.enabled:
            return []

        events: List[Dict[str, Any]] = []
        if self.cli_source._started:
            events.extend(self.cli_source.collect())
        if self.ide_source._started:
            events.extend(self.ide_source.collect())
        return events

    def commit_state(self) -> bool:
        success = True
        if self.cli_source._started:
            if not self.cli_source.commit_state():
                success = False
        if self.ide_source._started:
            if not self.ide_source.commit_state():
                success = False
        return success
