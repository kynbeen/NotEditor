from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pymupdf

from noteditor.app import ComposerApi, run
from noteditor.engine import ComposerSession
from noteditor.merge_handoff import (
    CONTRACT_VERSION,
    LEGACY_CONTRACT_VERSION,
    load_merge_plan,
    sidecar_path,
)


def make_pdf(path: Path, labels: list[str]) -> None:
    document = pymupdf.open()
    for label in labels:
        page = document.new_page()
        page.insert_text((50, 50), label)
    document.save(path)
    document.close()


class MergeHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_root = self.root / "imports" / "lecture"
        self.input_root.mkdir(parents=True)
        self.first = self.input_root / "첫 자료.pdf"
        self.second = self.input_root / "둘째 자료.pdf"
        self.reference = self.root / "bundle" / "lecture.pdf"
        self.reference.parent.mkdir()
        self.output = self.root / "result" / "merged.pdf"
        make_pdf(self.first, ["A1", "A2"])
        make_pdf(self.second, ["B1", "B2"])
        make_pdf(self.reference, ["A1", "A2"])

    def tearDown(self):
        self.temp.cleanup()

    def write_plan(self, **updates) -> Path:
        payload = {
            "version": LEGACY_CONTRACT_VERSION,
            "title": "병리학 1주차 — lecture",
            "output_path": str(self.output.resolve()),
            "parts": [
                {"path": str(self.first.resolve()), "pages": "2"},
                {"path": str(self.second.resolve()), "pages": ""},
            ],
        }
        payload.update(updates)
        path = self.root / "handoff.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def write_v2_plan(self, **updates) -> Path:
        payload = {
            "version": CONTRACT_VERSION,
            "mode": "merge",
            "title": "병리학 강의록 합치기",
            "input_root": str(self.input_root.resolve()),
            "output_path": str(self.output.resolve()),
            "parts": [],
        }
        payload.update(updates)
        path = self.root / "handoff-v2.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_load_rejects_unknown_versions_and_source_overwrite(self):
        with self.subTest("unknown version"):
            with self.assertRaisesRegex(Exception, "지원하지 않는"):
                load_merge_plan(self.write_plan(version=999))
        with self.subTest("source overwrite"):
            with self.assertRaisesRegex(Exception, "덮어쓸"):
                load_merge_plan(self.write_plan(output_path=str(self.first.resolve())))

    def test_startup_maps_paths_to_fresh_ids_and_preserves_part_order(self):
        api = ComposerApi(ComposerSession(), self.write_plan())
        try:
            response = api.startup_plan()
            self.assertTrue(response["ok"], response)
            plan = response["plan"]
            self.assertEqual(plan["title"], "병리학 1주차 — lecture")
            self.assertEqual(plan["output_path"], str(self.output.resolve()))
            self.assertEqual(len(plan["sources"]), 2)
            first_id, second_id = [source["id"] for source in plan["sources"]]
            self.assertEqual(plan["order"], [
                {"document_id": first_id, "page_index": 1},
                {"document_id": second_id, "page_index": 0},
                {"document_id": second_id, "page_index": 1},
            ])
            self.assertEqual(api.startup_plan(), response)
        finally:
            api._close()

    def test_save_uses_fixed_output_and_writes_actual_sidecar_after_pdf(self):
        api = ComposerApi(ComposerSession(), self.write_plan())
        try:
            startup = api.startup_plan()
            order = startup["plan"]["order"]
            saved = api.save_result(order, "ignored.pdf")
            self.assertTrue(saved["ok"], saved)
            self.assertFalse(saved["cancelled"])
            self.assertEqual(Path(saved["result"]["path"]), self.output.resolve())
            self.assertEqual(saved["result"]["page_count"], 3)
            self.assertTrue(self.output.is_file())
            sidecar = sidecar_path(self.output)
            self.assertTrue(sidecar.is_file())
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], LEGACY_CONTRACT_VERSION)
            self.assertEqual(payload["output"], str(self.output.resolve()))
            self.assertTrue(payload["noteditor_version"])
            self.assertTrue(payload["saved_at"].endswith("+00:00"))
            self.assertEqual(payload["parts"], [
                {"path": str(self.first.resolve()), "pages": "2"},
                {"path": str(self.second.resolve()), "pages": "1-2"},
            ])
        finally:
            api._close()

    def test_unrepresentable_manual_page_order_is_rejected_before_writing(self):
        plan = self.write_plan(parts=[
            {"path": str(self.first.resolve()), "pages": ""},
            {"path": str(self.second.resolve()), "pages": ""},
        ])
        api = ComposerApi(ComposerSession(), plan)
        try:
            startup = api.startup_plan()["plan"]
            order = list(startup["order"])
            order[0], order[1] = order[1], order[0]
            saved = api.save_result(order)
            self.assertFalse(saved["ok"])
            self.assertIn("손실 없이", saved["error"])
            self.assertFalse(self.output.exists())
            self.assertFalse(sidecar_path(self.output).exists())
        finally:
            api._close()

    def test_retry_removes_a_stale_sidecar_before_a_failed_pdf_write(self):
        api = ComposerApi(ComposerSession(), self.write_plan())
        try:
            order = api.startup_plan()["plan"]["order"]
            stale = sidecar_path(self.output)
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_text('{"stale": true}', encoding="utf-8")
            with patch.object(api._session, "build_pdf", side_effect=RuntimeError("합성 저장 실패")):
                saved = api.save_result(order)
            self.assertFalse(saved["ok"])
            self.assertFalse(stale.exists())
        finally:
            api._close()

    def test_invalid_startup_plan_does_not_leave_partial_documents(self):
        api = ComposerApi(ComposerSession(), self.write_plan(parts=[
            {"path": str(self.first.resolve()), "pages": "99"},
        ]))
        try:
            response = api.startup_plan()
            self.assertFalse(response["ok"])
            self.assertEqual(api._session.sources, [])
        finally:
            api._close()

    def test_v2_empty_merge_plan_keeps_input_root_and_requests_picker(self):
        plan_path = self.write_v2_plan()
        plan = load_merge_plan(plan_path)
        self.assertEqual(plan.version, CONTRACT_VERSION)
        self.assertEqual(plan.mode, "merge")
        self.assertEqual(plan.input_root, self.input_root.resolve())
        self.assertEqual(plan.parts, ())
        api = ComposerApi(ComposerSession(), plan_path)
        try:
            response = api.startup_plan()
            self.assertTrue(response["ok"], response)
            self.assertTrue(response["plan"]["auto_choose"])
            self.assertEqual(response["plan"]["sources"], [])
        finally:
            api._close()

    def test_v2_rejects_parts_outside_the_input_root(self):
        outside = self.root / "outside.pdf"
        make_pdf(outside, ["outside"])
        with self.assertRaisesRegex(Exception, "수집함 밖"):
            load_merge_plan(self.write_v2_plan(parts=[
                {"path": str(outside.resolve()), "pages": ""},
            ]))

    def test_review_plan_compares_pages_and_writes_skip_decision(self):
        decision = self.root / "result" / "decision.json"
        plan = self.write_v2_plan(
            mode="review",
            origin="selected",
            reference_path=str(self.reference.resolve()),
            decision_path=str(decision.resolve()),
            parts=[{"path": str(self.first.resolve()), "pages": ""}],
        )
        api = ComposerApi(ComposerSession(), plan)
        try:
            response = api.startup_plan()
            self.assertTrue(response["ok"], response)
            review = response["plan"]
            self.assertEqual(review["mode"], "review")
            self.assertEqual(review["origin"], "selected")
            self.assertEqual(review["comparison"]["matched_count"], 2)
            self.assertEqual(len(review["sources"]), 1)
            finished = api.finish_review("skip")
            self.assertTrue(finished["ok"], finished)
            payload = json.loads(decision.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], CONTRACT_VERSION)
            self.assertEqual(payload["decision"], "skip")
            self.assertFalse(self.output.exists())
        finally:
            api._close()

    def test_review_plan_limits_decisions_by_origin(self):
        decision = self.root / "result" / "decision.json"
        plan = self.write_v2_plan(
            mode="review", origin="selected",
            reference_path=str(self.reference.resolve()),
            decision_path=str(decision.resolve()),
            parts=[{"path": str(self.first.resolve()), "pages": ""}],
        )
        api = ComposerApi(ComposerSession(), plan)
        try:
            self.assertTrue(api.startup_plan()["ok"])
            response = api.finish_review("merge", [])
            self.assertFalse(response["ok"])
            self.assertIn("selected", response["error"])
            self.assertFalse(decision.exists())
        finally:
            api._close()

    def test_cli_forwards_open_plan_without_changing_debug(self):
        plan = self.write_plan()
        with patch("noteditor.__main__.configure_logging", return_value=self.root / "app.log"), \
                patch("noteditor.__main__.run") as run, \
                patch.object(sys, "argv", ["noteditor", "--debug", "--open-plan", str(plan)]):
            from noteditor.__main__ import main

            main()
        run.assert_called_once_with(debug=True, open_plan=str(plan))

    def test_desktop_window_title_includes_the_validated_plan_title(self):
        class FakeEvent:
            def __init__(self):
                self.callback = None

            def __iadd__(self, callback):
                self.callback = callback
                return self

        closed = FakeEvent()
        window = SimpleNamespace(events=SimpleNamespace(closed=closed))
        webview = SimpleNamespace(create_window=Mock(return_value=window), start=Mock())
        plan = self.write_plan()
        with patch.dict(sys.modules, {"webview": webview}), \
                patch("noteditor.app.configure_windows_app_identity"):
            run(open_plan=plan)
        self.assertEqual(webview.create_window.call_args.args[0], "NotEditor — 병리학 1주차 — lecture")
        self.assertIsNotNone(closed.callback)
        closed.callback()


if __name__ == "__main__":
    unittest.main()
