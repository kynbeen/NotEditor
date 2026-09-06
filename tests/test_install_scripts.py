from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OnlineInstallerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "install-online.ps1").read_text(encoding="utf-8")

    def test_documented_silent_and_wizard_switches_are_supported(self):
        self.assertIn("[switch]$Silent", self.script)
        self.assertIn("[switch]$Wizard", self.script)
        self.assertIn("$Silent -and $Wizard", self.script)

    def test_download_is_checked_for_mz_header_before_execution(self):
        header_check = self.script.index("$First -ne 0x4D -or $Second -ne 0x5A")
        execute = self.script.index("Start-Process -FilePath $Destination")
        self.assertLess(header_check, execute)


if __name__ == "__main__":
    unittest.main()
