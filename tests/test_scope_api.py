"""판 4 — 강의록 진도 범위를 summary.ai 에 물어보는 자리.

족첵 범위(`range_hint`)는 이 앱이 직접 짚지만(그림 맞추기), 강의록 진도 범위는 전사본을
읽고 강의의 흐름을 판단하는 문제라 LLM 이 필요하다. LLM 호출은 summary.ai 만 하므로
여기서는 **물어보고 받아 채우는 것**까지만 한다.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from noteditor.app import ComposerApi
from noteditor.engine import ComposerSession
from noteditor.merge_handoff import (
    CONTRACT_VERSION,
    RANGE_CONTRACT_VERSION,
    ScopeApi,
    load_merge_plan,
    request_scope,
)


def make_pdf(path: Path, labels: list[str]) -> None:
    document = pymupdf.open()
    for label in labels:
        page = document.new_page()
        page.insert_text((50, 50), label)
    document.save(path)
    document.close()


class ScopeApiPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_root = self.root / "imports" / "lecture"
        self.input_root.mkdir(parents=True)
        self.lecture = self.input_root / "강의록.pdf"
        self.reference = self.root / "bundle" / "lecture.pdf"
        self.reference.parent.mkdir()
        self.output = self.root / "result" / "merged.pdf"
        make_pdf(self.lecture, ["A1", "A2", "A3"])
        make_pdf(self.reference, ["A1"])

    def tearDown(self):
        self.temp.cleanup()

    def write_plan(self, **updates) -> Path:
        payload = {
            "version": CONTRACT_VERSION,
            "mode": "merge",
            "title": "병리학 강의록 합치기",
            "input_root": str(self.input_root.resolve()),
            "output_path": str(self.output.resolve()),
            "parts": [],
            "scope_api": {"url": "http://127.0.0.1:8700/api/scope-hint", "token": "tok"},
        }
        payload.update(updates)
        path = self.root / "handoff.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_a_merge_plan_carries_the_ask_address(self):
        plan = load_merge_plan(self.write_plan())
        self.assertEqual(plan.scope_api,
                         ScopeApi("http://127.0.0.1:8700/api/scope-hint", "tok"))

    def test_only_this_pc_is_accepted_as_the_address(self):
        """계획 파일은 다른 프로그램이 만든 텍스트다. 거기 적힌 곳으로 무엇이든 보내지 않는다."""
        for bad in ({"url": "http://example.com/api/scope-hint", "token": "t"},
                    {"url": "https://127.0.0.1/api/scope-hint", "token": "t"},
                    {"url": "http://127.0.0.1:8700/api/scope-hint", "token": ""},
                    {"url": "", "token": "t"},
                    "문자열",
                    None):
            with self.subTest(bad=bad):
                plan = load_merge_plan(self.write_plan(scope_api=bad))
                self.assertIsNone(plan.scope_api)

    def test_the_review_plan_never_carries_it(self):
        """검토 모드에는 고칠 범위가 이미 기록되어 있다."""
        plan = load_merge_plan(self.write_plan(
            mode="review", parts=[{"path": str(self.lecture.resolve()), "pages": ""}],
            reference_path=str(self.reference.resolve()), origin="merged",
            decision_path=str((self.root / "decision.json").resolve())))
        self.assertIsNone(plan.scope_api)

    def test_an_older_plan_ignores_the_new_field(self):
        plan = load_merge_plan(self.write_plan(version=RANGE_CONTRACT_VERSION))
        self.assertIsNone(plan.scope_api)

    def test_the_range_hint_of_版3_still_survives_the_bump(self):
        """`>= CONTRACT_VERSION` 으로 물으면 판을 올릴 때마다 앞 판의 칸이 조용히 사라진다."""
        plan = load_merge_plan(self.write_plan(
            version=RANGE_CONTRACT_VERSION,
            range_hint={"lecture_path": str(self.reference.resolve())}))
        self.assertIsNotNone(plan.range_hint)
        self.assertEqual(plan.range_hint.lecture, self.reference.resolve())


class RequestScopeTests(unittest.TestCase):
    def _answer(self, payload: dict) -> dict:
        class FakeResponse:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def read(self_inner):
                return json.dumps(payload).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            return request_scope(ScopeApi("http://127.0.0.1:8700/api/scope-hint", "t"),
                                 Path("강의록.pdf"))

    def test_a_normal_answer_is_normalised(self):
        got = self._answer({"pages": " 23-46 ", "confidence": 0.86, "uncertain": False,
                            "evidence": [{"page": 23, "quote": "여기부터"}]})
        self.assertEqual(got, {"pages": "23-46", "confidence": 0.86, "uncertain": False})

    def test_a_missing_range_is_an_empty_string_not_an_error(self):
        """못 짚는 것과 창이 안 열리는 것은 무게가 다르다."""
        got = self._answer({"pages": None, "confidence": None, "uncertain": True})
        self.assertEqual(got, {"pages": "", "confidence": 0.0, "uncertain": True})

    def test_a_network_failure_is_reported_as_a_readable_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("연결 거부")):
            with self.assertRaisesRegex(Exception, "물어보지 못했습니다"):
                request_scope(ScopeApi("http://127.0.0.1:8700/api/scope-hint", "t"),
                              Path("강의록.pdf"))


class SuggestScopeApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_root = self.root / "imports" / "lecture"
        self.input_root.mkdir(parents=True)
        self.lecture = self.input_root / "강의록.pdf"
        make_pdf(self.lecture, ["A1", "A2", "A3"])
        self.output = self.root / "result" / "merged.pdf"
        self.plan_path = self.root / "handoff.json"
        self.plan_path.write_text(json.dumps({
            "version": CONTRACT_VERSION, "mode": "merge", "title": "강의록 합치기",
            "input_root": str(self.input_root.resolve()),
            "output_path": str(self.output.resolve()),
            "parts": [{"path": str(self.lecture.resolve()), "pages": ""}],
            "scope_api": {"url": "http://127.0.0.1:8700/api/scope-hint", "token": "tok"},
        }, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_the_screen_is_told_it_can_ask(self):
        api = ComposerApi(ComposerSession(), self.plan_path)
        try:
            plan = api.startup_plan()["plan"]
            self.assertTrue(plan["can_suggest_scope"])
            self.assertFalse(plan["can_suggest_ranges"])
        finally:
            api._close()

    def test_the_answer_comes_back_with_the_document_it_belongs_to(self):
        api = ComposerApi(ComposerSession(), self.plan_path)
        try:
            plan = api.startup_plan()["plan"]
            document_id = plan["sources"][0]["id"]
            with patch("noteditor.app.request_scope",
                       return_value={"pages": "23-46", "confidence": 0.86,
                                     "uncertain": False}) as ask:
                got = api.suggest_scope(document_id)
            self.assertTrue(got["ok"], got)
            self.assertEqual(got["pages"], "23-46")
            self.assertEqual(got["document_id"], document_id)
            self.assertEqual(Path(ask.call_args.args[1]), self.lecture.resolve())
        finally:
            api._close()

    def test_an_unknown_document_is_refused(self):
        api = ComposerApi(ComposerSession(), self.plan_path)
        try:
            api.startup_plan()
            got = api.suggest_scope("없는-아이디")
            self.assertFalse(got["ok"])
            self.assertIn("찾지 못했습니다", got["error"])
        finally:
            api._close()

    def test_without_an_address_the_screen_gets_a_reason_not_a_crash(self):
        self.plan_path.write_text(json.dumps({
            "version": CONTRACT_VERSION, "mode": "merge", "title": "강의록 합치기",
            "input_root": str(self.input_root.resolve()),
            "output_path": str(self.output.resolve()),
            "parts": [{"path": str(self.lecture.resolve()), "pages": ""}],
        }, ensure_ascii=False), encoding="utf-8")
        api = ComposerApi(ComposerSession(), self.plan_path)
        try:
            plan = api.startup_plan()["plan"]
            self.assertFalse(plan["can_suggest_scope"])
            got = api.suggest_scope(plan["sources"][0]["id"])
            self.assertFalse(got["ok"])
            self.assertIn("물어볼 곳이 없습니다", got["error"])
        finally:
            api._close()


class ScopeScreenWiringTests(unittest.TestCase):
    """화면이 실제로 물어보고 채우는가 — 파일 하나만 읽으면 알 수 있는 것들."""

    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "noteditor" / "static"
        cls.js = (static / "app.js").read_text(encoding="utf-8")

    def test_the_scope_is_asked_for_and_filled_into_the_selection(self):
        self.assertIn('callApi("suggest_scope", doc.id)', self.js)
        self.assertIn("setDocumentSelection(doc, parsed.indices)", self.js)
        # 제안일 뿐이므로 확신이 낮으면 그렇게 말한다.
        self.assertIn("확신이 낮습니다", self.js)

    def test_the_same_button_serves_both_kinds_of_range(self):
        self.assertIn("plan.can_suggest_ranges || plan.can_suggest_scope", self.js)
        self.assertIn('void suggestForPlan();', self.js)

    def test_several_pdfs_are_not_guessed_between(self):
        """물어보는 것은 LLM 한 번씩이다. 어느 것이 이번 차시인지 짐작해 여러 번 부르지 않는다."""
        self.assertIn("강의록 PDF 가 하나일 때만", self.js)


if __name__ == "__main__":
    unittest.main()
