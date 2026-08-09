"""Integration contract tests that do not require a running Home Assistant."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from custom_components.kc761.const import (
    CMD_GET_DEVICE_INFO,
    CMD_GET_STA,
    FORBIDDEN_WRITE_COMMANDS,
)


ROOT = Path(__file__).parents[1]


class IntegrationStaticTests(unittest.TestCase):
    def test_manifest_is_rc_and_local_polling(self) -> None:
        manifest = json.loads(
            (ROOT / "custom_components" / "kc761" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["version"], "0.1.0-rc.1")
        self.assertEqual(manifest["homeassistant"], "2026.7.0")
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(manifest["iot_class"], "local_polling")

    def test_only_two_read_commands_are_client_api(self) -> None:
        self.assertEqual({CMD_GET_STA, CMD_GET_DEVICE_INFO}, {0x53, 0x54})
        self.assertEqual(FORBIDDEN_WRITE_COMMANDS, {0x62, 0x63, 0x64, 0x66})

    def test_no_service_or_spectrum_entity_files(self) -> None:
        integration = ROOT / "custom_components" / "kc761"
        self.assertFalse((integration / "services.yaml").exists())
        self.assertFalse(any(path.name == "spectrum.py" for path in integration.iterdir()))
