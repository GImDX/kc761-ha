"""Integration contract tests that do not require a running Home Assistant."""

from __future__ import annotations

import json
import hashlib
import unittest
from pathlib import Path

from custom_components.kc761.const import (
    CMD_GET_CAL_DATA,
    CMD_GET_DEVICE_INFO,
    CMD_GET_MC_DATA,
    CMD_GET_STA,
    FORBIDDEN_WRITE_COMMANDS,
    READ_COMMANDS,
    WRITE_COMMANDS,
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
    ROOT / "docs" / "V0_2_EXPANSION.md",
)


def json_shape(value):
    """Return only JSON keys and container/value types."""
    if isinstance(value, dict):
        return {key: json_shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_shape(item) for item in value]
    return type(value).__name__


class IntegrationStaticTests(unittest.TestCase):
    def test_manifest_is_alpha_and_local_polling(self) -> None:
        manifest = json.loads((ROOT / "custom_components" / "kc761" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.2.0-alpha.6")
        self.assertEqual(manifest["dependencies"], ["frontend", "http"])
        self.assertNotIn("homeassistant", manifest)
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(manifest["iot_class"], "local_polling")
        hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(hacs, {"name": "KC761x", "homeassistant": "2026.7.0"})
        brand_dir = ROOT / "custom_components" / "kc761" / "brand"
        self.assertTrue((brand_dir / "icon.png").is_file())

    def test_public_protocol_commands_match_the_expansion_scope(self) -> None:
        self.assertEqual({CMD_GET_STA, CMD_GET_DEVICE_INFO}, {0x53, 0x54})
        self.assertEqual({CMD_GET_MC_DATA, CMD_GET_CAL_DATA}, {0x52, 0x55})
        self.assertEqual(READ_COMMANDS, {0x52, 0x53, 0x54, 0x55})
        self.assertEqual(WRITE_COMMANDS, {0x62, 0x63, 0x66})
        self.assertEqual(FORBIDDEN_WRITE_COMMANDS, {0x64})

    def test_control_and_spectrum_files_are_present_without_services(self) -> None:
        integration = ROOT / "custom_components" / "kc761"
        self.assertFalse((integration / "services.yaml").exists())
        for relative in (
            "button.py",
            "select.py",
            "spectrum.py",
            "switch.py",
            "websocket_api.py",
            "frontend/kc761-spectrum-card.js",
            "frontend/echarts.esm.min.js",
            "frontend/echarts.LICENSE",
            "frontend/echarts.NOTICE",
            "frontend/echarts-THIRD-PARTY.md",
            "frontend/licenses/LICENSE-d3",
        ):
            self.assertTrue((integration / relative).is_file(), relative)
        frontend = (integration / "frontend" / "kc761-spectrum-card.js").read_text(encoding="utf-8")
        for feature in (
            "window.confirm",
            "Export CSV",
            "Saved spectrum",
            "Logarithmic",
            "ROI start channel",
            "Peak CH",
            "refresh_interval",
            "localStorage",
            "renderer: \"svg\"",
            "import(ECHARTS_URL)",
            "markArea",
            "markPoint",
            "markLine",
            "roi-start-cursor",
            "roi-end-cursor",
            "data-cursor",
            "pointerdown",
            'customElements.get("kc761-spectrum-card")',
            'window.customCards.some((card) => card.type === "kc761-spectrum-card")',
        ):
            self.assertIn(feature, frontend)
        vendor = (integration / "frontend" / "echarts.esm.min.js").read_bytes()
        self.assertEqual(
            hashlib.sha256(vendor).hexdigest(),
            "28515f26aa57f87eb1e4ed4eb446927eaf329b5f5e02b4bfd4b0264fea40d1da",
        )
        third_party = (integration / "frontend" / "echarts-THIRD-PARTY.md").read_text(encoding="utf-8")
        self.assertIn("echarts`\n- Version: `6.1.0`", third_party)
        self.assertIn(
            "sha512-q0yaFPggC9FUdsWH4blavRWFmxdrIodbkoKNAjJudAI6CA9gNPxHtV2RcZNEepZVlk4yvBYkOkbk6HIVpIyHZA==",
            third_party,
        )

    def test_translation_keys_match_canonical_strings(self) -> None:
        integration = ROOT / "custom_components" / "kc761"
        canonical = json.loads((integration / "strings.json").read_text(encoding="utf-8"))
        self.assertIn(
            "create_raw_entities",
            canonical["options"]["step"]["init"]["data"],
        )
        self.assertIn("time_sync_enabled", canonical["options"]["step"]["init"]["data"])
        self.assertIn("time_sync_interval", canonical["options"]["step"]["init"]["data"])
        self.assertIn("spectrum_save_enabled", canonical["options"]["step"]["init"]["data"])
        self.assertIn("spectrum_save_interval", canonical["options"]["step"]["init"]["data"])
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
