"""Behavioral contract checks for the native Lovelace editor/card surface."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class FrontendCardContractTests(unittest.TestCase):
    def test_card_contract_with_node_when_available(self) -> None:
        node = shutil.which("node") or shutil.which("node.exe")
        if node is None:
            self.skipTest("Node.js is not available on PATH; run tests/frontend_card_contract.mjs in the release gate")
        result = subprocess.run(
            [node, str(ROOT / "tests" / "frontend_card_contract.mjs")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
