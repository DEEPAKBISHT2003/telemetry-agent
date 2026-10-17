"""Regression tests ensuring Antigravity sources, hooks, and collectors remain unimpacted."""

import os
from pathlib import Path
import tempfile
import unittest

from ai_telemetry_agent.config.settings import load_settings
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.integrations.hooks.installer import get_global_hook_status
from ai_telemetry_agent.sources.antigravity import (
    ANTIGRAVITY_AI_TELEMETRY_STATUS,
    AntigravitySource,
)


class TestAntigravityRegression(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_file = Path(self.temp_dir.name) / ".env"
        self.base_env = "MEMBER_ID=TEST_USER\nDEVICE_ID=TEST_DEV\n"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_antigravity_status_preserved(self):
        """Antigravity status constant should remain GEN_METADATA_LOCAL_SUPPORTED."""
        self.assertEqual(ANTIGRAVITY_AI_TELEMETRY_STATUS, "GEN_METADATA_LOCAL_SUPPORTED")

    def test_antigravity_mode_initializes_sqlite_source(self):
        """In 'antigravity' mode, TelemetryCollector should instantiate AntigravitySource with SQLite provider."""
        self.env_file.write_text(f"{self.base_env}TELEMETRY_SOURCE=antigravity\n", encoding="utf-8")
        settings = load_settings(env_file=str(self.env_file))
        collector = TelemetryCollector(settings)

        antigravity_sources = [s for s in collector.sources if isinstance(s, AntigravitySource)]
        self.assertEqual(len(antigravity_sources), 1)
        self.assertIsNotNone(antigravity_sources[0].sqlite_provider)

    def test_global_hook_status_structure(self):
        """get_global_hook_status should return all expected dictionary keys without error."""
        dummy_file = Path(self.temp_dir.name) / "hooks.json"
        status = get_global_hook_status(hooks_file=dummy_file)
        self.assertIn("config_path", status)
        self.assertIn("config_exists", status)
        self.assertIn("telemetry_hook_installed", status)
        self.assertIn("handler_valid", status)
        self.assertFalse(status["config_exists"])


if __name__ == "__main__":
    unittest.main()
