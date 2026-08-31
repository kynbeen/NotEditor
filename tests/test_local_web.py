from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from noteditor.local_web import (
    DEFAULT_LOCAL_WEB_PORT,
    LOCAL_WEB_APP_USER_MODEL_ID,
    LOCAL_WEB_INSTANCE,
    LocalWebLauncherError,
    browser_command,
    find_app_browser,
    is_local_noteditor,
    local_web_url,
    run_local_web,
    wait_until_ready,
)


class LocalWebLauncherTests(unittest.TestCase):
    def test_uses_a_stable_loopback_origin_and_separate_windows_identity(self):
        self.assertEqual(DEFAULT_LOCAL_WEB_PORT, 8765)
        self.assertEqual(local_web_url(), "http://127.0.0.1:8765/")
        self.assertEqual(LOCAL_WEB_APP_USER_MODEL_ID, "NotEditor.LocalWeb")

    def test_browser_command_uses_app_mode_and_a_dedicated_profile(self):
        command = browser_command(
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            local_web_url(),
            Path(r"C:\Users\test\AppData\Local\NotEditor\LocalWebProfile"),
        )
        self.assertIn("--app=http://127.0.0.1:8765/", command)
        self.assertIn(
            r"--user-data-dir=C:\Users\test\AppData\Local\NotEditor\LocalWebProfile",
            command,
        )
        self.assertIn("--disable-background-mode", command)

    def test_browser_discovery_uses_the_first_existing_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing.exe"
            edge = Path(folder) / "msedge.exe"
            edge.write_bytes(b"")
            self.assertEqual(find_app_browser((missing, edge)), edge.resolve())
        self.assertIsNone(find_app_browser(()))

    def test_health_marker_does_not_accept_a_generic_web_server(self):
        self.assertTrue(is_local_noteditor({
            "ok": True, "runtime": "web", "instance": LOCAL_WEB_INSTANCE,
        }))
        self.assertFalse(is_local_noteditor({
            "ok": True, "runtime": "web", "instance": "web",
        }))
        self.assertFalse(is_local_noteditor(None))

    @patch("noteditor.local_web._run_owned_server")
    @patch("noteditor.local_web.port_is_available", return_value=True)
    @patch("noteditor.local_web.probe_server", return_value=None)
    def test_free_port_starts_an_owned_server(self, _probe, _available, owned):
        self.assertEqual(run_local_web(), "owned")
        owned.assert_called_once_with(DEFAULT_LOCAL_WEB_PORT)

    @patch("noteditor.local_web._run_owned_server")
    @patch("noteditor.local_web.launch_app_browser")
    @patch("noteditor.local_web.probe_server")
    def test_second_launch_reuses_without_taking_server_ownership(
        self, probe, browser, owned
    ):
        probe.return_value = {
            "ok": True,
            "runtime": "web",
            "instance": LOCAL_WEB_INSTANCE,
        }
        self.assertEqual(run_local_web(), "reused")
        browser.assert_called_once_with(local_web_url())
        owned.assert_not_called()

    @patch("noteditor.local_web.port_is_available", return_value=False)
    @patch("noteditor.local_web.probe_server", return_value=None)
    def test_unknown_port_owner_is_rejected(self, _probe, _available):
        with self.assertRaisesRegex(LocalWebLauncherError, "다른 프로그램"):
            run_local_web()

    @patch("noteditor.local_web.port_is_available", return_value=True)
    @patch("noteditor.local_web.probe_server")
    def test_generic_noteditor_server_is_not_mistaken_for_the_launcher(
        self, probe, _available
    ):
        probe.return_value = {"ok": True, "runtime": "web", "instance": "web"}
        with self.assertRaisesRegex(LocalWebLauncherError, "다른 프로그램"):
            run_local_web()

    def test_invalid_port_is_rejected_before_any_network_action(self):
        for port in (0, 65536):
            with self.subTest(port=port), self.assertRaises(LocalWebLauncherError):
                run_local_web(port)

    @patch("noteditor.local_web.time.sleep")
    @patch("noteditor.local_web.probe_server")
    def test_readiness_waits_for_the_exact_local_instance(self, probe, _sleep):
        probe.side_effect = [
            None,
            {"ok": True, "runtime": "web", "instance": "web"},
            {"ok": True, "runtime": "web", "instance": LOCAL_WEB_INSTANCE},
        ]
        wait_until_ready(DEFAULT_LOCAL_WEB_PORT, SimpleNamespace(should_exit=False), 1)
        self.assertEqual(probe.call_count, 3)


class LocalWebPackagingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).parents[1]
        cls.spec = (root / "NotEditor.spec").read_text(encoding="utf-8")
        cls.inno = (root / "installer/NotEditor.iss").read_text(encoding="utf-8")
        cls.install = (root / "install.ps1").read_text(encoding="utf-8")
        cls.legacy_install = (root / "install-app.ps1").read_text(encoding="utf-8")

    def test_frozen_bundle_contains_the_local_web_executable(self):
        self.assertIn('"launch_web.pyw"', self.spec)
        self.assertIn('name="NotEditorLocalWeb"', self.spec)

    def test_inno_installer_creates_distinct_local_web_shortcuts(self):
        self.assertIn('Name: "{autoprograms}\\NotEditor 로컬 웹"', self.inno)
        self.assertIn('Filename: "{app}\\NotEditorLocalWeb.exe"', self.inno)
        self.assertIn('Name: "{autodesktop}\\NotEditor 로컬 웹"', self.inno)

    def test_source_installers_create_the_local_web_shortcut(self):
        for script in (self.install, self.legacy_install):
            with self.subTest(script=script[:30]):
                self.assertIn('"NotEditor 로컬 웹.lnk"', script)
                self.assertIn("-m noteditor.local_web", script)


if __name__ == "__main__":
    unittest.main()
