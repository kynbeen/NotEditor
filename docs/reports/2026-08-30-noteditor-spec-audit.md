# NotEditor 안정화·시각적 쪽 정렬 성공 기준 감사

**감사일:** 2026-08-30  
**대상 명세:** `docs/specs/2026-08-30-01-NotEditor-안정화와-시각적-쪽-정렬.md`  
**판정:** 19개 성공 기준 모두 확인

## 실행 결과

- `venv\Scripts\python.exe -m unittest discover -s tests`: **183개 통과**
- `node --check noteditor/static/app.js`: **통과**
- `git diff --check`: **통과**
- Edge 실파일 검증: 100쪽 PDF 3개를 문서 합치기에 올려 초기 화면이 제한된 수의 이미지만
  요청하는 것을 확인했다. 100쪽 필기 비교 화면은 높이 648px에서 약 3개 행을 보이며, 다음 이미지
  로딩 완료를 기다리지 않고 3,600px를 즉시 연속 스크롤했다.
- Edge 드래그 검증: 1쪽을 2쪽 뒤로 이동할 때 두 대응 관계가 바뀐다는 앱 내부 경고를 확인했다.
  취소하면 `1,2,3,4`가 유지되고, 계속하면 `2,1,3,4`로 바뀌면서 영향 행이 수동·미확인 상태가
  되는 것을 확인했다.

## 성공 기준별 근거

| 단계 | 기준 | 상태 | 주된 근거 |
|---|---|---:|---|
| 1차 | 3×100쪽에서 브라우저가 모든 이미지를 동시에 요청하지 않음 | 확인 | 자동 부하 테스트와 함께 실제 Edge에서 초기 뷰포트 주변 이미지만 요청하는 것을 확인 |
| 1차 | 중복 렌더 공유와 LRU 상한 | 확인 | `tests/test_engine.py::test_concurrent_duplicate_preview_requests_share_one_render`, `test_preview_cache_evicts_old_entries_at_the_byte_limit` |
| 1차 | 부하 중 health 1초 이내 | 확인 | `tests/test_preview_load.py::test_three_hundred_pages_keep_rendering_bounded_and_health_fast`, `tests/test_web.py::test_health_handlers_never_wait_for_the_worker_pool` |
| 1차 | 페이지 안 실패·재시도, 전역 오류 반복 방지 | 확인 | `tests/test_static_ui.py::test_image_requests_are_lazy_bounded_deduplicated_and_retryable_inline`; GET 502/503/504만 제한 재시도하는 정적 계약 |
| 1차 | 업로드·분석 실패 뒤 선택 유지와 재분석 | 확인 | `tests/test_app.py::test_failed_analysis_keeps_both_files_and_can_retry_without_upload`, `tests/test_web.py::test_analysis_failure_keeps_web_uploads_and_retry_uses_the_same_files` |
| 1차 | 업로드부터 미리보기까지 단계 구분 | 확인 | `tests/test_static_ui.py::test_handwriting_upload_and_analysis_have_distinct_retryable_stages`, HTTP·데스크톱 상태 전이 테스트 |
| 1차 | 편집 이름과 형식별 확장자 | 확인 | `tests/test_static_ui.py::test_output_names_are_editable_without_letting_extensions_drift`, `test_saved_handwriting_name_follows_the_source_format`, 웹·데스크톱 저장 테스트 |
| 1차 | 통일된 도구별 초기화 | 확인 | `tests/test_static_ui.py::test_each_tool_clears_only_its_own_files`, `tests/test_web.py`와 `tests/test_app.py`의 양방향 격리 테스트; Edge 기본 화면 확인 |
| 1차 | 전체 자동 테스트 통과 | 확인 | 183개 단위·HTTP·정적 UI·성능·상태 전이 테스트 통과 |
| 2차 | 좌우 행과 한쪽 전용 빈 칸 | 확인 | `tests/test_page_plan.py::test_automatic_plan_highlights_uncertain_and_one_sided_rows`, `tests/test_static_ui.py::test_alignment_review_is_continuous_side_by_side_and_shows_actual_ink` |
| 2차 | 약 3쪽 높이, 100쪽 비차단 연속 스크롤 | 확인 | 실제 Edge에서 648px 비교 영역, 약 3개 행, 3,600px 즉시 스크롤과 뷰포트 주변 지연 로딩 확인 |
| 2차 | 드래그 경고, 취소 무변경, 진행 결과 | 확인 | 실제 Edge에서 앱 내부 경고의 영향 2개를 확인하고 취소 시 `1,2,3,4`, 진행 시 `2,1,3,4` 및 영향 행 미확인 전환 확인 |
| 2차 | 불확실·한쪽 전용·수동 행 강조와 개별 확인 | 확인 | `tests/test_static_ui.py::test_page_review_supports_confirmation_and_target_drag_reordering`, `noteditor/static/app.css`의 상태별 행 스타일 |
| 2차 | 필기 보기 토글은 표시만 변경 | 확인 | `tests/test_static_ui.py::test_alignment_review_is_continuous_side_by_side_and_shows_actual_ink`; CSS로 오버레이만 숨기며 저장 계획은 변경하지 않음 |
| 2차 | 미확인 저장 최종 경고·명시적 승인 | 확인 | `tests/test_app.py::test_page_plan_requires_confirmation_then_forwards_the_validated_plan`, `tests/test_web.py::test_web_page_plan_requires_explicit_unconfirmed_approval`; 경고에 쪽 번호 포함 |
| 2차 | Samsung Notes·Notewise 혼합 행 결과 계약 | 확인 | `tests/test_sdocx_rebuild.py::test_rebuild_accepts_confirmed_target_reorder_and_preserves_source_only_ink`, `tests/test_notewise_transfer.py::test_confirmed_plan_can_reorder_target_pages`, `test_source_only_annotated_page_is_preserved_with_its_old_background` |
| 2차 | 원본·대상 미덮어쓰기 | 확인 | `tests/test_notewise_transfer.py::test_rejects_overwriting_source`와 형식별 출력 경로 검사; 웹 결과는 별도 임시 결과를 다운로드한 뒤 결과 사본만 정리 |
| 3차 | Goodnotes·Flexcil 판정과 샘플 목록 | 확인 | `docs/research/2026-08-30-goodnotes-flexcil-support.md` |
| 3차 | 검증 전 UI 지원 약속 금지 | 확인 | UI 입력 형식은 `.sdocx`·`.notewise`로 유지; 조사 문서는 두 형식을 모두 `추가 샘플 필요`로 판정 |

실물 SDOCX·Notewise·오창모 PDF 및 실제 Samsung Notes·Notewise 앱 왕복 검증은 명세의 범위 밖이며,
완료한 합성 PDF·브라우저 검증과 구분한다.
