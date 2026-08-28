from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteditor import stamp_version, version


class NormalizeTagTests(unittest.TestCase):
    def test_accepts_release_tags_and_strips_the_v(self):
        self.assertEqual(version.normalize_tag("v0.5.0"), "0.5.0")
        self.assertEqual(version.normalize_tag("0.5.0"), "0.5.0")
        self.assertEqual(version.normalize_tag(" v1.2.3.4 "), "1.2.3.4")

    def test_rejects_anything_that_is_not_a_version(self):
        for value in ("main", "", "v", "release-0.5.0", "v0.5.0-rc1"):
            with self.subTest(value=value):
                self.assertIsNone(version.normalize_tag(value))


class DescribeTests(unittest.TestCase):
    def test_tag_itself_becomes_a_plain_version(self):
        self.assertEqual(version.version_from_describe("v0.5.0"), "0.5.0")

    def test_commits_after_the_tag_stay_visible(self):
        self.assertEqual(
            version.version_from_describe("v0.5.0-3-gbf90fcf"), "0.5.0+3.gbf90fcf"
        )

    def test_uncommitted_changes_are_marked(self):
        self.assertEqual(version.version_from_describe("v0.5.0-dirty"), "0.5.0+dirty")
        self.assertEqual(
            version.version_from_describe("v0.5.0-2-gabc1234-dirty"),
            "0.5.0+2.gabc1234.dirty",
        )

    def test_repository_without_tags_reports_the_commit_only(self):
        self.assertEqual(version.version_from_describe("bf90fcf"), "0.0.0+bf90fcf")
        self.assertEqual(version.version_from_describe("bf90fcf-dirty"), "0.0.0+bf90fcf.dirty")


class ResolveVersionTests(unittest.TestCase):
    def test_environment_override_wins(self):
        with patch.dict("os.environ", {"NOTEDITOR_VERSION": "9.9.9"}):
            self.assertEqual(version.resolve_version(), "9.9.9")

    def test_stamped_file_is_used_before_git(self):
        with patch.dict("os.environ", {"NOTEDITOR_VERSION": ""}), \
                patch.object(version, "_stamped_version", return_value="0.5.0"), \
                patch.object(version, "describe", return_value="v0.4.0") as described:
            self.assertEqual(version.resolve_version(), "0.5.0")
        described.assert_not_called()

    def test_falls_back_to_git_then_to_unknown(self):
        with patch.dict("os.environ", {"NOTEDITOR_VERSION": ""}), \
                patch.object(version, "_stamped_version", return_value=None):
            with patch.object(version, "describe", return_value="v0.5.0-1-gabc1234"):
                self.assertEqual(version.resolve_version(), "0.5.0+1.gabc1234")
            with patch.object(version, "describe", return_value=None), \
                    patch.dict("os.environ", {"RENDER_GIT_COMMIT": ""}):
                self.assertEqual(version.resolve_version(), version.UNKNOWN_VERSION)

    def test_deploy_platform_commit_is_better_than_unknown(self):
        with patch.dict("os.environ",
                        {"NOTEDITOR_VERSION": "", "RENDER_GIT_COMMIT": "bf90fcf1234567"}), \
                patch.object(version, "_stamped_version", return_value=None), \
                patch.object(version, "describe", return_value=None):
            self.assertEqual(version.resolve_version(), "0.0.0+bf90fcf")

    def test_the_running_package_reports_a_usable_version(self):
        import noteditor

        self.assertRegex(noteditor.__version__, r"^\d+(\.\d+)*(\+.+)?$")


class StampVersionTests(unittest.TestCase):
    def test_a_release_tag_is_written_verbatim(self):
        with tempfile.TemporaryDirectory() as folder:
            written = stamp_version.stamp(stamp_version.resolve("v0.5.0"), Path(folder))
            self.assertIn('__version__ = "0.5.0"', written.read_text(encoding="utf-8"))

    def test_a_branch_name_falls_back_to_the_checkout(self):
        with patch.object(stamp_version, "describe", return_value="v0.5.0-2-gabc1234"):
            self.assertEqual(stamp_version.resolve("main"), "0.5.0+2.gabc1234")

    def test_stamped_file_is_importable_python(self):
        with tempfile.TemporaryDirectory() as folder:
            written = stamp_version.stamp("0.5.0+2.gabc1234", Path(folder))
            namespace: dict = {}
            exec(compile(written.read_text(encoding="utf-8"), str(written), "exec"), namespace)
            self.assertEqual(namespace["__version__"], "0.5.0+2.gabc1234")


if __name__ == "__main__":
    unittest.main()
