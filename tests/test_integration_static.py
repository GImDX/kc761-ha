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
PUBLIC_DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "ARCHITECTURE.md",
    ROOT / "docs" / "DECISIONS.md",
    ROOT / "docs" / "ENTITY_MODEL.md",
    ROOT / "docs" / "PROTOCOL_EVIDENCE.md",
    ROOT / "docs" / "RC_TEST_RESULTS.md",
    ROOT / "docs" / "TEST_RELEASE_PLAN.md",
)


def json_shape(value):
    """Return only JSON keys and container/value types."""
    if isinstance(value, dict):
        return {key: json_shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_shape(item) for item in value]
    return type(value).__name__


class IntegrationStaticTests(unittest.TestCase):
    def test_manifest_is_rc_and_local_polling(self) -> None:
        manifest = json.loads((ROOT / "custom_components" / "kc761" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.1.0-rc.1")
        self.assertNotIn("homeassistant", manifest)
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(manifest["iot_class"], "local_polling")
        hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(hacs, {"name": "KC761x", "homeassistant": "2026.7.0"})
        brand_dir = ROOT / "custom_components" / "kc761" / "brand"
        self.assertTrue((brand_dir / "icon.png").is_file())

    def test_only_two_read_commands_are_client_api(self) -> None:
        self.assertEqual({CMD_GET_STA, CMD_GET_DEVICE_INFO}, {0x53, 0x54})
        self.assertEqual(FORBIDDEN_WRITE_COMMANDS, {0x62, 0x63, 0x64, 0x66})

    def test_no_service_or_spectrum_entity_files(self) -> None:
        integration = ROOT / "custom_components" / "kc761"
        self.assertFalse((integration / "services.yaml").exists())
        self.assertFalse(any(path.name == "spectrum.py" for path in integration.iterdir()))

    def test_translation_keys_match_canonical_strings(self) -> None:
        integration = ROOT / "custom_components" / "kc761"
        canonical = json.loads((integration / "strings.json").read_text(encoding="utf-8"))
        self.assertIn(
            "create_raw_entities",
            canonical["options"]["step"]["init"]["data"],
        )
        for language in ("en", "zh-Hans"):
            translated = json.loads((integration / "translations" / f"{language}.json").read_text(encoding="utf-8"))
            self.assertEqual(json_shape(translated), json_shape(canonical))

    def test_public_docs_do_not_disclose_environment_details(self) -> None:
        forbidden = (
            "192.168.8.29",
            "192.168.8.121",
            "ssh_password.txt",
            "access_token",
            "refresh_token",
            "7601-0000-000001",
        )
        for path in PUBLIC_DOCS:
            text = path.read_text(encoding="utf-8")
            for value in forbidden:
                self.assertNotIn(value, text, f"{value!r} leaked in {path.name}")
