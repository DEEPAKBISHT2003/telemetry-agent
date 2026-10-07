"""Tests for Antigravity Capability Probe."""

from pathlib import Path
import pytest
from src.sources.antigravity_probe import AntigravityCapabilities, AntigravityCapabilityProbe


def test_probe_detects_existing_antigravity():
    probe = AntigravityCapabilityProbe()
    caps = probe.probe()

    assert isinstance(caps, AntigravityCapabilities)
    assert caps.antigravity_detected is True
    assert caps.ide_detected is True
    assert caps.extension_detected is False  # Verified: No public extension event stream
    assert caps.official_ai_event_stream is False
    assert caps.token_metrics_available is False
    assert caps.cost_metrics_available is False
    # Hooks & MCP are supported architectural features
    assert caps.hooks_available is True
    assert caps.mcp_available is True


def test_probe_with_custom_paths(tmp_path):
    fake_home = tmp_path / "user_home"
    fake_home.mkdir()

    # Case 1: Empty directory (no Antigravity)
    probe1 = AntigravityCapabilityProbe(user_home=fake_home)
    caps1 = probe1.probe()
    assert caps1.antigravity_detected is False
    assert caps1.ide_detected is False
    assert caps1.hooks_available is False

    # Case 2: Create .gemini and .gemini/antigravity-ide
    gemini_dir = fake_home / ".gemini"
    ide_dir = gemini_dir / "antigravity-ide"
    ide_dir.mkdir(parents=True)

    probe2 = AntigravityCapabilityProbe(user_home=fake_home)
    caps2 = probe2.probe()
    assert caps2.antigravity_detected is True
    assert caps2.ide_detected is True
    assert caps2.hooks_available is True
