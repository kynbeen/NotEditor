from __future__ import annotations

import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pymupdf

from noteditor.app import ComposerApi, run
from noteditor.engine import ComposerSession
from noteditor.page_match import MatchResult, PagePair


class FakeWindow:
    def __init__(self, responses):
        self.responses = list(responses)
        self.dialog_calls = []

    def create_file_dialog(self, *_args, **kwargs):
        self.dialog_calls.append(kwargs)
        return self.responses.pop(0)

    def toggle_fullscreen(self):
        self.fullscreen_toggled = True


class FakeEvent:
    def __init__(self):
        self.callback = None

    def __iadd__(self, callback):
        self.callback = callback
        return self


class ComposerApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.source = self.root / "source.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((50, 50), "API TEST")
        document.save(self.source)
        document.close()
        self.api = ComposerApi(ComposerSession())
        self.webview = types.SimpleNamespace(FileDialog=types.SimpleNamespace(OPEN="open", SAVE="save"))

    def tearDown(self):
        self.api._close()
        self.folder.cleanup()

    def wait_for_handwriting_analysis(self) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.api.handwriting_status()
            if status["analysis"]["state"] != "running":
                return status
            time.sleep(0.01)
        self.fail("필기 분석이 끝나지 않았습니다.")

    def test_choose_parse_and_save_contract(self):
        output = self.root / "saved.pdf"
        self.api._bind_window(FakeWindow([(str(self.source),), str(output)]))
        with patch.dict(sys.modules, {"webview": self.webview}):
            chosen = self.api.choose_pdfs()
            self.assertTrue(chosen["ok"])
            source = chosen["added"][0]
            self.assertEqual(self.api.parse_range("1", 1), {"ok": True, "indices": [0]})
            saved = self.api.save_result([
                {"document_id": source["id"], "page_index": 0}
            ], "saved.pdf")
        self.assertTrue(saved["ok"])
        self.assertFalse(saved["cancelled"])
        self.assertTrue(output.exists())

    def test_cancelled_save_does_not_write(self):
        self.api._bind_window(FakeWindow([None]))
        with patch.dict(sys.modules, {"webview": self.webview}):
            result = self.api.save_result([], "nothing.pdf")
        self.assertEqual(result, {"ok": True, "cancelled": True})

    def test_health_contract_is_available_before_file_selection(self):
        result = self.api.health()
        self.assertTrue(result["ok"])
        # 릴리스 빌드는 `0.5.0`, 개발 체크아웃은 `0.5.0+3.gbf90fcf` 처럼 뒤에 빌드 정보가 붙는다.
        self.assertRegex(result["version"], r"^\d+(\.\d+)*(\+[0-9a-z.]+)?$")

    def test_fullscreen_toggle_uses_the_native_window(self):
        window = FakeWindow([])
        self.api._bind_window(window)
        self.assertTrue(self.api.toggle_fullscreen()["ok"])
        self.assertTrue(window.fullscreen_toggled)

    def test_desktop_window_starts_maximized(self):
        closed = FakeEvent()
        window = SimpleNamespace(events=SimpleNamespace(closed=closed))
        webview = SimpleNamespace(
            create_window=Mock(return_value=window),
            start=Mock(),
        )
        with patch.dict(sys.modules, {"webview": webview}), \
                patch("noteditor.app.configure_windows_app_identity"):
            run()

        self.assertTrue(webview.create_window.call_args.kwargs["maximized"])
        self.assertEqual(webview.create_window.call_args.kwargs["min_size"], (1080, 680))
        self.assertTrue(webview.create_window.call_args.args[1].endswith("index.html#desktop"))
        self.assertTrue(webview.start.call_args.kwargs["http_server"])
        self.assertIsNotNone(closed.callback)
        closed.callback()

    def test_native_objects_are_not_exposed_as_public_api_attributes(self):
        self.assertFalse(hasattr(self.api, "window"))
        self.assertFalse(hasattr(self.api, "session"))
        for name in dir(self.api):
            if name.startswith("_"):
                continue
            self.assertTrue(callable(getattr(self.api, name)), name)

    def test_handwriting_transfer_file_dialog_flow(self):
        source = self.root / "annotated.sdocx"
        target = self.root / "target.pdf"
        output = self.root / "target-필기.sdocx"
        source.write_bytes(b"zip")
        target.write_bytes(b"%PDF")
        inspection = SimpleNamespace(as_dict=lambda: {
            "source_name": source.name,
            "target_name": target.name,
            "page_count": 2,
            "annotated_page_count": 1,
            "stroke_cache_count": 1,
        })
        self.api._bind_window(FakeWindow([(str(source),), (str(target),), str(output)]))
        with patch.dict(sys.modules, {"webview": self.webview}), \
                patch("noteditor.app.inspect_transfer", return_value=inspection), \
                patch("noteditor.app.transfer_handwriting", return_value={
                    "path": str(output), "page_count": 2,
                }) as transfer:
            selected_source = self.api.choose_handwriting_source()
            selected_target = self.api.choose_handwriting_target()
            selected_target = self.wait_for_handwriting_analysis()
            saved = self.api.save_handwriting_transfer("target-필기.sdocx")

        self.assertTrue(selected_source["ok"])
        self.assertFalse(selected_source["ready"])
        self.assertTrue(selected_target["ready"])
        self.assertTrue(saved["ok"])
        self.assertFalse(saved["cancelled"])
        transfer.assert_called_once_with(source.resolve(), target.resolve(), output.resolve())

    def test_failed_analysis_keeps_both_files_and_can_retry_without_upload(self):
        source = self.root / "annotated.sdocx"
        target = self.root / "target.pdf"
        source.write_bytes(b"zip")
        target.write_bytes(b"%PDF")
        inspection = SimpleNamespace(as_dict=lambda: {"page_count": 2})
        attempts = 0
        stages: list[str] = []

        def inspect(_source, _target, *, progress):
            nonlocal attempts
            attempts += 1
            progress("structure")
            stages.append("structure")
            progress("matching")
            stages.append("matching")
            if attempts == 1:
                raise RuntimeError("합성 분석 실패")
            progress("alignment")
            stages.append("alignment")
            progress("preview")
            stages.append("preview")
            return inspection

        with patch("noteditor.app.inspect_transfer", side_effect=inspect):
            self.api._set_handwriting_path("source", source)
            self.api._set_handwriting_path("target", target)
            failed = self.wait_for_handwriting_analysis()
            self.assertEqual(failed["analysis"]["state"], "error")
            self.assertEqual(failed["source_name"], source.name)
            self.assertEqual(failed["target_name"], target.name)

            retried = self.api.retry_handwriting_analysis()
            self.assertTrue(retried["ok"])
            ready = self.wait_for_handwriting_analysis()

        self.assertTrue(ready["ready"], ready)
        self.assertEqual(attempts, 2)
        self.assertEqual(
            stages,
            ["structure", "matching", "structure", "matching", "alignment", "preview"],
        )
        self.assertTrue(source.exists())
        self.assertTrue(target.exists())

    def test_save_dialog_keeps_dots_in_the_name_and_follows_the_source_format(self):
        """이름 안의 마침표를 확장자로 오해해 뒷부분을 잘라내면 안 된다."""
        cases = [
            ("annotated.sdocx", "강의자료 v1.2-필기", "강의자료 v1.2-필기.sdocx"),
            ("annotated.notewise", "강의자료 v1.2-필기", "강의자료 v1.2-필기.notewise"),
            ("annotated.notewise", "이미-붙은.sdocx", "이미-붙은.notewise"),
        ]
        for source_name, suggested, expected in cases:
            with self.subTest(source=source_name, suggested=suggested):
                source = self.root / source_name
                target = self.root / "target.pdf"
                output = self.root / expected
                source.write_bytes(b"zip")
                target.write_bytes(b"%PDF")
                inspection = SimpleNamespace(as_dict=dict)
                window = FakeWindow([(str(source),), (str(target),), str(output)])
                self.api._bind_window(window)
                with patch.dict(sys.modules, {"webview": self.webview}), \
                        patch("noteditor.app.inspect_transfer", return_value=inspection), \
                        patch("noteditor.app.transfer_handwriting", return_value={
                            "path": str(output), "page_count": 1,
                        }):
                    self.api.choose_handwriting_source()
                    self.api.choose_handwriting_target()
                    self.wait_for_handwriting_analysis()
                    saved = self.api.save_handwriting_transfer(suggested)

                self.assertTrue(saved["ok"], saved)
                self.assertEqual(window.dialog_calls[-1]["save_filename"], expected)
                self.api.reset_handwriting_transfer()

    def test_reset_documents_clears_previews_but_never_the_other_tool(self):
        """도구별로 따로 비워야 한다. 한쪽을 정리하다 다른 쪽 선택이 사라지면 안 된다."""
        self.api.add_paths([str(self.source)])
        self.api.page_image(self.api._session.sources[0].id, 0, "thumbnail")
        self.api._handwriting_source = self.root / "annotated.sdocx"
        self.api._handwriting_target = self.root / "target.pdf"
        self.assertNotEqual(self.api._session._preview_cache, {})

        result = self.api.reset_documents()

        self.assertTrue(result["ok"], result)
        self.assertEqual(self.api._session.sources, [])
        self.assertEqual(self.api._session._preview_cache, {})
        # 임시 폴더가 8.3 단축 경로로 잡히는 환경이 있어(예: CI 러너의 RUNNER~1),
        # 앱이 정규화한 경로와 맞춰 본다.
        self.assertEqual(result["cleared"], [str(self.source.resolve())])
        # 필기 옮기기는 그대로다.
        self.assertEqual(self.api._handwriting_source, self.root / "annotated.sdocx")
        self.assertEqual(self.api._handwriting_target, self.root / "target.pdf")

    def test_reset_handwriting_never_touches_the_merge_tool(self):
        self.api.add_paths([str(self.source)])
        self.api._handwriting_source = self.root / "annotated.sdocx"

        result = self.api.reset_handwriting_transfer()

        self.assertTrue(result["ok"], result)
        self.assertIsNone(self.api._handwriting_source)
        self.assertEqual(
            [source.path for source in self.api._session.sources], [self.source.resolve()]
        )

    def test_reset_documents_does_not_delete_the_users_own_files(self):
        """데스크톱에서는 목록의 경로가 사용자의 원본이다. 절대 지우면 안 된다."""
        self.api.add_paths([str(self.source)])
        self.api.reset_documents()
        self.assertTrue(self.source.exists())

    def test_handwriting_preview_returns_background_and_ink_data_uris(self):
        source = self.root / "annotated.sdocx"
        target = self.root / "target.pdf"
        source.write_bytes(b"zip")
        target.write_bytes(b"%PDF")
        inspection = SimpleNamespace(page_count=3, alignment=None, as_dict=lambda: {"page_count": 3})
        self.api._handwriting_source = source
        self.api._handwriting_target = target
        with patch("noteditor.app.inspect_transfer", return_value=inspection) as inspect, \
                patch("noteditor.app.preview_transfer",
                      return_value=(b"\x89PNG-before", b"\x89PNG-after", b"\x89PNG-ink", 17)) as preview:
            first = self.api.handwriting_preview(1)
            second = self.api.handwriting_preview(99)

        self.assertTrue(first["ok"])
        self.assertEqual(first["index"], 1)
        self.assertEqual(first["page_count"], 3)
        self.assertTrue(first["before"].startswith("data:image/png;base64,"))
        self.assertTrue(first["after"].startswith("data:image/png;base64,"))
        self.assertTrue(first["ink"].startswith("data:image/png;base64,"))
        self.assertEqual(first["stroke_count"], 17)
        # 범위를 벗어난 쪽 번호는 마지막 쪽으로 좁힌다.
        self.assertEqual(second["index"], 2)
        # 파일이 그대로면 검사 결과를 다시 계산하지 않는다.
        inspect.assert_called_once()
        self.assertEqual(preview.call_count, 2)

    def test_manual_handwriting_mapping_is_validated_and_forwarded(self):
        source = self.root / "annotated.sdocx"
        target = self.root / "target.pdf"
        output = self.root / "target-필기.sdocx"
        source.write_bytes(b"zip")
        target.write_bytes(b"%PDF")
        automatic = SimpleNamespace()
        inspection = SimpleNamespace(
            mode="rebuild",
            source_page_count=3,
            match=automatic,
            as_dict=lambda: {"mode": "rebuild"},
        )
        manual = SimpleNamespace()
        self.api._handwriting_source = source
        self.api._handwriting_target = target
        self.api._bind_window(FakeWindow([str(output)]))
        with patch.dict(sys.modules, {"webview": self.webview}), \
                patch("noteditor.app.inspect_transfer", return_value=inspection), \
                patch("noteditor.page_match.match_from_target_mapping",
                      return_value=manual) as convert, \
                patch("noteditor.app.transfer_handwriting",
                      return_value={"path": str(output)}) as transfer:
            saved = self.api.save_handwriting_transfer("target-필기.sdocx", [0, None, 2])

        self.assertTrue(saved["ok"])
        convert.assert_called_once_with(3, [0, None, 2], automatic)
        transfer.assert_called_once()
        args, kwargs = transfer.call_args
        # Windows CI의 임시 경로는 같은 파일을 8.3 짧은 경로로 돌려줄 수 있다.
        self.assertTrue(args[0].samefile(source))
        self.assertTrue(args[1].samefile(target))
        self.assertEqual(args[2], output.resolve())
        self.assertEqual(kwargs, {"match_override": manual})

    def test_page_plan_requires_confirmation_then_forwards_the_validated_plan(self):
        source = self.root / "annotated.sdocx"
        target = self.root / "target.pdf"
        output = self.root / "target-필기.sdocx"
        source.write_bytes(b"zip")
        target.write_bytes(b"%PDF")
        automatic = MatchResult((PagePair(0, 0, 0.1, 0.9), PagePair(1, 1, 0.8, 0.01)))
        inspection = SimpleNamespace(
            mode="rebuild",
            source_page_count=2,
            page_count=2,
            match=automatic,
            as_dict=lambda: {"mode": "rebuild"},
        )
        payload = [
            {"source_index": 0, "target_index": 1, "confirmed": True},
            {"source_index": 1, "target_index": 0, "confirmed": False},
        ]
        self.api._handwriting_source = source
        self.api._handwriting_target = target
        self.api._bind_window(FakeWindow([str(output), str(output)]))
        with patch.dict(sys.modules, {"webview": self.webview}), \
                patch("noteditor.app.inspect_transfer", return_value=inspection), \
                patch("noteditor.app.transfer_handwriting",
                      return_value={"path": str(output)}) as transfer:
            rejected = self.api.save_handwriting_transfer("target-필기.sdocx", payload)
            saved = self.api.save_handwriting_transfer(
                "target-필기.sdocx", payload, allow_unconfirmed=True
            )

        self.assertFalse(rejected["ok"])
        self.assertIn("확인하지 않은", rejected["error"])
        self.assertTrue(saved["ok"])
        plan = transfer.call_args.kwargs["plan_override"]
        self.assertEqual(
            [(slot.source_index, slot.target_index) for slot in plan.slots],
            [(0, 1), (1, 0)],
        )
        self.assertEqual(saved["result"]["warnings"], [
            "확인하지 않은 쪽 대응 1개를 사용자 승인으로 저장했습니다: 원본 2쪽 ↔ 새 PDF 1쪽"
        ])


if __name__ == "__main__":
    unittest.main()
