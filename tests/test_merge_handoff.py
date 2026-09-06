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

    def test_range_hint_carries_the_lecture_and_its_neighbours(self):
        plan = load_merge_plan(self.write_v2_plan(range_hint={
            "lecture_path": str(self.reference.resolve()),
            "other_lecture_paths": [str(self.second.resolve()), "상대경로.pdf",
                                    str(self.root / "없는파일.pdf")],
        }))
        self.assertIsNotNone(plan.range_hint)
        self.assertEqual(self.reference.resolve(), plan.range_hint.lecture)
        # 없는 파일과 상대경로는 조용히 버린다 — 있으면 좋은 값이지 필수가 아니다.
        self.assertEqual((self.second.resolve(),), plan.range_hint.others)

    def test_a_broken_range_hint_does_not_stop_the_merge(self):
        """자동 제안이 안 되는 것과 창이 안 열리는 것은 무게가 전혀 다르다."""
        for bad in ("not-a-dict", {}, {"lecture_path": ""},
                    {"lecture_path": "상대경로.pdf"},
                    {"lecture_path": str(self.root / "없는파일.pdf")}):
            plan = load_merge_plan(self.write_v2_plan(range_hint=bad))
            self.assertIsNone(plan.range_hint, str(bad))

    def test_version_2_plans_still_get_their_own_fields_checked(self):
        """`== CONTRACT_VERSION` 으로 물으면 판을 올릴 때마다 옛 판의 검사가 사라진다."""
        payload = json.loads(self.write_v2_plan().read_text(encoding="utf-8"))
        payload["version"] = 2
        payload.pop("input_root")
        path = self.root / "handoff-old.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(Exception, "input_root"):
            load_merge_plan(path)

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

    def test_a_handoff_window_can_close_itself_but_a_plain_one_cannot(self):
        """저장한 뒤 summary.ai 로 돌아가려면 이 창이 스스로 닫혀야 한다.

        다만 사용자가 그냥 실행한 NotEditor 까지 웹 화면이 닫을 수 있으면 안 된다.
        """
        api = ComposerApi(ComposerSession(), self.write_plan())
        try:
            api.startup_plan()
            window = SimpleNamespace(destroy=Mock())
            api._bind_window(window)
            self.assertTrue(api.close_window()["ok"])
            window.destroy.assert_called_once_with()
        finally:
            api._close()

        plain = ComposerApi(ComposerSession())
        try:
            plain._bind_window(SimpleNamespace(destroy=Mock()))
            response = plain.close_window()
            self.assertFalse(response["ok"])
            self.assertIn("인계로 열린 창에서만", response["error"])
        finally:
            plain._close()

    def test_a_merged_review_plan_reports_the_page_range_it_recorded(self):
        """합쳐서 만든 자료는 "고른 범위 안팎이 바뀌었나"가 핵심이라 화면이 범위를 알아야 한다."""
        api = ComposerApi(ComposerSession(), self.write_plan(parts=[
            {"path": str(self.first.resolve()), "pages": "2"},
            {"path": str(self.second.resolve()), "pages": "1-2"},
        ]))
        try:
            recorded = api.startup_plan()["plan"]["recorded_ranges"]
            self.assertEqual([entry["pages"] for entry in recorded], ["2", "1-2"])
            self.assertEqual([entry["page_indexes"] for entry in recorded], [[1], [0, 1]])
            self.assertEqual(len({entry["document_id"] for entry in recorded}), 2)
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
            finished = api.finish_review("skip", None, "none")
            self.assertTrue(finished["ok"], finished)
            payload = json.loads(decision.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], CONTRACT_VERSION)
            self.assertEqual(payload["decision"], "skip")
            self.assertEqual(payload["change"], "none")
            self.assertEqual(payload["changed_pages"], [])
            self.assertFalse(self.output.exists())
        finally:
            api._close()

    def test_suggest_ranges_picks_this_lectures_pages_out_of_the_exam(self):
        """족첵은 강의록 쪽을 그대로 싣고 뒤에 문제를 붙인다 — 그 그림을 찾으면 된다."""
        lecture = self.root / "bundle" / "this-lecture.pdf"
        make_pdf(lecture, ["LEC-1", "LEC-2", "LEC-3"])
        previous = self.root / "bundle" / "prev-lecture.pdf"
        make_pdf(previous, ["PREV-1"])
        following = self.root / "bundle" / "next-lecture.pdf"
        make_pdf(following, ["NEXT-1", "NEXT-2"])
        exam = self.input_root / "족첵.pdf"
        # 앞 강의 강의록·문제 → 내 강의록 3쪽 → 내 문제 2쪽 → 다음 강의 강의록
        make_pdf(exam, ["PREV-1", "PREV-Q", "LEC-1", "LEC-2", "LEC-3",
                        "Q-1", "Q-2", "NEXT-1", "NEXT-2"])

        path = self.write_v2_plan(range_hint={
            "lecture_path": str(lecture.resolve()),
            "other_lecture_paths": [str(previous.resolve()), str(following.resolve())],
        })
        api = ComposerApi(ComposerSession(), path,
                          preloaded_startup_plan=load_merge_plan(path))
        try:
            self.assertTrue(api.startup_plan()["ok"])
            self.assertTrue(api.add_paths([str(exam.resolve())])["ok"])
            response = api.suggest_ranges()
            self.assertTrue(response["ok"], response)
            proposal = response["proposals"][0]
            # 내 강의록 쪽(3-5)에 뒤따르는 문제 쪽(6-7)까지. 앞으로는 늘리지 않는다 —
            # 2쪽은 앞 강의의 문제다. 뒤는 다음 강의가 시작되는 8쪽에서 끊긴다.
            self.assertEqual("3-7", proposal["pages"])
            self.assertFalse(proposal["uncertain"])
        finally:
            api._close()

    def test_suggest_ranges_needs_a_lecture_and_some_documents(self):
        path = self.write_v2_plan()
        api = ComposerApi(ComposerSession(), path,
                          preloaded_startup_plan=load_merge_plan(path))
        try:
            self.assertTrue(api.startup_plan()["ok"])
            response = api.suggest_ranges()
            self.assertFalse(response["ok"])
            self.assertIn("강의록", response["error"])
        finally:
            api._close()

    def test_review_records_what_changed_and_which_pages(self):
        """`decision` 은 파일을 어떻게 갈아 끼우는가, `change` 는 무엇이 바뀌었는가다."""
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
            finished = api.finish_review("refresh", None, "questions", [4, 3, 3, 0])
            self.assertTrue(finished["ok"], finished)
            payload = json.loads(decision.read_text(encoding="utf-8"))
            self.assertEqual(payload["change"], "questions")
            self.assertEqual(payload["changed_pages"], [3, 4])
        finally:
            api._close()

    def test_skip_and_no_change_have_to_agree(self):
        """어긋난 조합을 남기면 읽는 쪽이 어느 쪽이 진짜였는지 알 수 없다."""
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
            self.assertFalse(api.finish_review("skip", None, "content")["ok"])
            self.assertFalse(api.finish_review("refresh", None, "none")["ok"])
            self.assertFalse(api.finish_review("refresh", None, "nope")["ok"])
            self.assertFalse(decision.exists())
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
