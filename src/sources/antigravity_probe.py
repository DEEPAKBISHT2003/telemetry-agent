"""Antigravity environment capability probe.

Safely detects and inspects local Antigravity installation, MCP configuration,
Hooks support, and official telemetry capabilities without scraping private internal files.
"""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class AntigravityCapabilities:
    antigravity_detected: bool
    ide_detected: bool
    extension_detected: bool
    mcp_available: bool
    hooks_available: bool
    official_ai_event_stream: bool
    token_metrics_available: bool
    cost_metrics_available: bool
    tool_events_available: bool
    session_events_available: bool
    gemini_root: Optional[str] = None
    ide_root: Optional[str] = None
    mcp_config_path: Optional[str] = None
    hooks_config_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AntigravityCapabilityProbe:
    """Safely probes the local developer machine for verified Antigravity integration capabilities."""

    def __init__(self, user_home: Optional[Path] = None, workspace_root: Optional[Path] = None):
        self.user_home = (user_home or Path.home()).resolve()
        self.workspace_root = (workspace_root or Path.cwd()).resolve()

    def probe(self) -> AntigravityCapabilities:
        gemini_dir = self.user_home / ".gemini"
        ide_dir = gemini_dir / "antigravity-ide"
        config_dir = gemini_dir / "config"

        antigravity_detected = gemini_dir.is_dir()
        ide_detected = ide_dir.is_dir()

        # Check MCP availability (mcp_config.json in global config or ide root)
        mcp_paths = [
            config_dir / "mcp_config.json",
            ide_dir / "mcp_config.json",
            self.workspace_root / ".agents" / "mcp_config.json",
        ]
        found_mcp_path = None
        for p in mcp_paths:
            if p.is_file():
                found_mcp_path = str(p)
                break
        mcp_available = found_mcp_path is not None or antigravity_detected

        # Check Hooks availability
        hooks_paths = [
            self.workspace_root / ".agents" / "hooks.json",
            config_dir / "hooks.json",
        ]
        found_hooks_path = None
        for p in hooks_paths:
            if p.is_file():
                found_hooks_path = str(p)
                break
        # Hooks are an architecturally supported feature of the Antigravity agent system
        hooks_available = ide_detected or antigravity_detected

        # Tool events & session lifecycle events are officially exposed through hooks.json
        tool_events_available = hooks_available
        session_events_available = hooks_available

        # Extensions: there is no public VSCode/IDE extension API for AI event streaming
        extension_detected = False

        # Token & cost metrics are NOT exposed via hooks or public event streams
        official_ai_event_stream = False
        token_metrics_available = False
        cost_metrics_available = False

        return AntigravityCapabilities(
            antigravity_detected=antigravity_detected,
            ide_detected=ide_detected,
            extension_detected=extension_detected,
            mcp_available=mcp_available,
            hooks_available=hooks_available,
            official_ai_event_stream=official_ai_event_stream,
            token_metrics_available=token_metrics_available,
            cost_metrics_available=cost_metrics_available,
            tool_events_available=tool_events_available,
            session_events_available=session_events_available,
            gemini_root=str(gemini_dir) if antigravity_detected else None,
            ide_root=str(ide_dir) if ide_detected else None,
            mcp_config_path=found_mcp_path,
            hooks_config_path=found_hooks_path,
        )


def probe_local_capabilities() -> Dict[str, Any]:
    """Helper function to run capability probe and return dictionary."""
    probe = AntigravityCapabilityProbe()
    return probe.probe().to_dict()
