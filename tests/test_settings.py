"""Unit tests for configuration and settings validation."""

import os
from pathlib import Path
import tempfile
import unittest

from ai_telemetry_agent.config.settings import (
    ConfigurationError,
    Settings,
    load_settings,
    VALID_TELEMETRY_SOURCES,
)


class TestSettingsValidation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_file = Path(self.temp_dir.name) / ".env"
        self.base_env = "MEMBER_ID=TEST_USER\nDEVICE_ID=TEST_DEV\n"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_telemetry_source(self):
        """Default telemetry source should be 'antigravity' for backward compatibility."""
        self.env_file.write_text(self.base_env, encoding="utf-8")
        settings = load_settings(env_file=str(self.env_file))
        self.assertEqual(settings.telemetry_source, "antigravity")
        self.assertTrue(settings.kiro_monitor_enabled)

    def test_valid_telemetry_sources(self):
        """All documented sources ('antigravity', 'kiro_ide', 'kiro_cli', 'auto') should be accepted."""
        for src in ("antigravity", "kiro_ide", "kiro_cli", "auto"):
            with self.subTest(source=src):
                self.env_file.write_text(f"{self.base_env}TELEMETRY_SOURCE={src}\n", encoding="utf-8")
                settings = load_settings(env_file=str(self.env_file))
                self.assertEqual(settings.telemetry_source, src)

    def test_invalid_telemetry_source_raises(self):
        """Invalid source values must raise a ConfigurationError with clear context."""
        self.env_file.write_text(f"{self.base_env}TELEMETRY_SOURCE=invalid_source_123\n", encoding="utf-8")
        with self.assertRaises(ConfigurationError) as cm:
            load_settings(env_file=str(self.env_file))
        self.assertIn("Invalid TELEMETRY_SOURCE", str(cm.exception))
        self.assertIn("invalid_source_123", str(cm.exception))

    def test_kiro_sessions_dir_override(self):
        """KIRO_SESSIONS_DIR environment variable and .env override should take effect."""
        custom_dir = Path(self.temp_dir.name) / "custom_kiro"
        self.env_file.write_text(
            f"{self.base_env}TELEMETRY_SOURCE=kiro_cli\nKIRO_SESSIONS_DIR={custom_dir}\n",
            encoding="utf-8",
        )
        settings = load_settings(env_file=str(self.env_file))
        self.assertEqual(settings.telemetry_source, "kiro_cli")
        self.assertEqual(str(settings.kiro_sessions_dir), str(custom_dir.resolve()))

    def test_kiro_monitor_enabled_boolean_parsing(self):
        """KIRO_MONITOR_ENABLED should parse truthy and falsy values correctly."""
        for val, expected in (("false", False), ("0", False), ("no", False), ("true", True), ("1", True)):
            with self.subTest(val=val, expected=expected):
                self.env_file.write_text(f"{self.base_env}KIRO_MONITOR_ENABLED={val}\n", encoding="utf-8")
                settings = load_settings(env_file=str(self.env_file))
                self.assertEqual(settings.kiro_monitor_enabled, expected)


if __name__ == "__main__":
    unittest.main()
