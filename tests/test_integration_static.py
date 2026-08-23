"""Integration contract tests that do not require a running Home Assistant."""

from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import unittest
from pathlib import Path

from custom_components.kc761.const import (
    CMD_GET_CAL_DATA,
    CMD_GET_DEVICE_INFO,
    CMD_GET_MC_DATA,
    CMD_GET_STA,
    FORBIDDEN_WRITE_COMMANDS,
    READ_COMMANDS,
    VERSION,
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
        self.assertEqual(manifest["version"], VERSION)
        self.assertEqual(VERSION, "0.2.0-alpha.22")
        self.assertEqual(manifest["dependencies"], ["frontend", "http"])
        self.assertNotIn("homeassistant", manifest)
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(manifest["iot_class"], "local_polling")
        hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(hacs, {"name": "KC761x", "homeassistant": "2026.7.0"})
        brand_dir = ROOT / "custom_components" / "kc761" / "brand"
        self.assertTrue((brand_dir / "icon.png").is_file())

    def test_frontend_and_manifest_versions_are_coherent(self) -> None:
        manifest = json.loads(
            (ROOT / "custom_components" / "kc761" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        frontend = (
            ROOT
            / "custom_components"
            / "kc761"
            / "frontend"
            / "kc761-spectrum-card.js"
        ).read_text(encoding="utf-8")
        self.assertIn(f'const CARD_VERSION = "{VERSION}";', frontend)
        self.assertIn(
            'f"/kc761/kc761-spectrum-card.js?v={VERSION}"',
            (ROOT / "custom_components" / "kc761" / "__init__.py").read_text(
                encoding="utf-8"
            ),
        )
        self.assertEqual(manifest["version"], VERSION)

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
            "data-cursor",
            'type="range"',
            'aria-label="ROI start channel"',
            'aria-label="ROI end channel"',
            "data-cursor-slider",
            'aria-label="Data cursor channel"',
            "aria-valuetext",
            "_setData",
            "_dataCursorChannel",
            "_updateDataCursorUI",
            "_updateRoiSliderUI",
            "_applyPresentationMode",
            "const dialogPreview = this._preview && !path",
            "_ensureLayoutObserver",
            "_updateCompactHeight",
            "compact-height",
            "_openEditor",
            'new EventConstructor("ll-edit-card"',
            'searchParams.set("edit", "1")',
            "cursorNearPeak",
            "containLabel: true",
            "COLOR_FALLBACKS",
            "_refreshColors",
            "--graph-color-${index}",
            "--color-${paletteIndex}",
            "--kc761-spectrum-curve",
            "renderMode: \"html\"",
            "showMinLabel: true",
            "showMaxLabel: true",
            "nameLocation: \"end\"",
            "nameRotate: 0",
            "verticalAlign: \"bottom\"",
            "formatter: (value) =>",
            "grid-auto-rows:58px",
            "height:58px",
            ":host{display:block;min-width:0;min-height:0;height:100%;overflow:hidden}",
            (
                "ha-card{box-sizing:border-box;height:100%;min-height:0;padding:16px;overflow:auto;"
                "display:flex;flex-direction:column}"
            ),
            "getConfigForm",
            "getEntitySuggestion",
            "getGridOptions",
            'columns: "full"',
            "getBoundingClientRect",
            "return 9;",
            ".roi-slider{position:relative;min-width:250px;flex:1 1 300px",
            ".data-slider{position:relative;width:100%;min-width:0;flex:none",
            ".chart-wrap{position:relative;margin-top:8px;height:370px;flex:0 1 370px",
            "validateConfig",
            "show_controls",
            "show_summary",
            "show_legend",
            "show_actions",
            "show_peak",
            "documentationURL",
            "preview: false",
            "CARD_DOCUMENTATION_URL",
            "_applyVisibility",
            "_resetEntityRuntime",
            "y-axis:configured",
            "y-axis:selected",
            "_setSummaryValue",
            "aria-label",
            "title = text",
            "white-space:nowrap",
            "registerKC761SpectrumCard",
            'globalThis.customElements',
            'Array.isArray(hostWindow.customCards)',
            "EDIT_HANDOFF_STORAGE_KEY",
            "EDIT_HANDOFF_TTL_MS",
            "EDIT_HANDOFF_GRACE_MS",
            "EDIT_HANDOFF_REGISTRY_KEY",
            "sessionStorage",
            "_editorLocationKey",
            "_configFingerprint",
            "_writePendingEditorHandoff",
            "_readPendingEditorHandoff",
            "_tryConsumeEditorHandoff",
            "_consumeEditorHandoff",
            "_resolveEditorHandoff",
            "_scheduleEditorHandoffRetry",
            "_unregisterEditorHandoffCandidate",
        ):
            self.assertIn(feature, frontend)
        for summary_id in ("acquired", "runtime", "total", "peak", "roi-total", "cursor-value"):
            self.assertIn(f'this._setSummaryValue("#{summary_id}"', frontend)
        self.assertNotIn('entity: "sensor.kc761_spectrum"', frontend)
        self.assertNotIn("roi-start-cursor", frontend)
        self.assertNotIn("roi-end-cursor", frontend)
        self.assertNotIn("chart-cursor", frontend)
        self.assertNotIn("pointerdown", frontend)
        self.assertNotIn("Math.ceil(height / 50)", frontend)
        self.assertNotIn(".roi-slider,.data-slider{position", frontend)
        self.assertNotIn(".data-slider{position:relative;min-width:250px;flex:1 1 300px", frontend)
        self.assertNotIn("height:430px;flex:0 1 430px", frontend)
        self.assertNotIn("_attachCursor", frontend)
        self.assertNotIn("_setCursorFromClient", frontend)
        self.assertNotIn('id="data-cursor"', frontend)
        self.assertNotIn("container:kc761-card / size", frontend)
        self.assertNotIn("@container", frontend)
        self.assertNotIn('type=\"number\" min=\"0\" max=\"2047\"', frontend)
        self.assertNotIn("GRID.left", frontend)
        self.assertNotIn("GRID.right", frontend)
        self.assertNotIn("min_rows", frontend)
        self.assertNotIn("rows: 8", frontend)
        self.assertIn('renderer: "svg"', frontend)
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

    def test_frontend_javascript_syntax_when_node_is_available(self) -> None:
        """Use the host Node executable as an optional, path-independent gate."""
        node = shutil.which("node") or shutil.which("node.exe")
        if node is None:
            self.skipTest("Node.js is not available on PATH; run `node --check` in the release gate")
        card = ROOT / "custom_components" / "kc761" / "frontend" / "kc761-spectrum-card.js"
        result = subprocess.run(
            [node, "--check", str(card)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
