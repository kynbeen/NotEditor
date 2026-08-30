# NotEditor 안정화·시각적 쪽 정렬 성공 기준 감사

**감사일:** 2026-08-30  
**대상 명세:** `docs/specs/2026-08-30-01-NotEditor-안정화와-시각적-쪽-정렬.md`  
**판정:** 19개 성공 기준 중 16개 확인, Edge 실파일 상호작용 3개 대기

## 실행 결과

- `venv\Scripts\python.exe -m unittest discover -s tests`: **159개 통과**
- `node --check noteditor/static/app.js`: **통과**
- `git diff --check`: **통과**
- Edge `http://127.0.0.1:8000/`: 문서 합치기·필기 옮기기 기본 화면, 도구 전환, 편집 가능한 저장
  이름 입력, 도구별 초기화 버튼 배치를 확인했다.
- Edge 파일 선택: ChatGPT 브라우저 확장 프로그램의 로컬 파일 URL 접근 권한이 꺼져 있어 file
  chooser가 열리지 않았다. 독립 Playwright로 우회하지 않았으며 업로드 뒤 동작 3개는 미확인으로
  남겼다.

## 성공 기준별 근거

| 단계 | 기준 | 상태 | 주된 근거 |
|---|---|---:|---|
| 1차 | 3×100쪽에서 브라우저가 모든 이미지를 동시에 요청하지 않음 | 대기 | `tests/test_preview_load.py::test_three_hundred_pages_keep_rendering_bounded_and_health_fast`, `tests/test_static_ui.py::test_image_requests_are_lazy_bounded_deduplicated_and_retryable_inline`; 실제 Edge 네트워크 동작은 파일 선택 권한 해제 뒤 확인 필요 |
| 1차 | 중복 렌더 공유와 LRU 상한 | 확인 | `tests/test_engine.py::test_concurrent_duplicate_preview_requests_share_one_render`, `test_preview_cache_evicts_old_entries_at_the_byte_limit` |
| 1차 | 부하 중 health 1초 이내 | 확인 | `tests/test_preview_load.py::test_three_hundred_pages_keep_rendering_bounded_and_health_fast`, `tests/test_web.py::test_health_handlers_never_wait_for_the_worker_pool` |
| 1차 | 페이지 안 실패·재시도, 전역 오류 반복 방지 | 확인 | `tests/test_static_ui.py::test_image_requests_are_lazy_bounded_deduplicated_and_retryable_inline`; GET 502/503/504만 제한 재시도하는 정적 계약 |
| 1차 | 업로드·분석 실패 뒤 선택 유지와 재분석 | 확인 | `tests/test_app.py::test_failed_analysis_keeps_both_files_and_can_retry_without_upload`, `tests/test_web.py::test_analysis_failure_keeps_web_uploads_and_retry_uses_the_same_files` |
| 1차 | 업로드부터 미리보기까지 단계 구분 | 확인 | `tests/test_static_ui.py::test_handwriting_upload_and_analysis_have_distinct_retryable_stages`, HTTP·데스크톱 상태 전이 테스트 |
| 1차 | 편집 이름과 형식별 확장자 | 확인 | `tests/test_static_ui.py::test_output_names_are_editable_without_letting_extensions_drift`, `test_saved_handwriting_name_follows_the_source_format`, 웹·데스크톱 저장 테스트 |
| 1차 | 통일된 도구별 초기화 | 확인 | `tests/test_static_ui.py::test_each_tool_clears_only_its_own_files`, `tests/test_web.py`와 `tests/test_app.py`의 양방향 격리 테스트; Edge 기본 화면 확인 |
| 1차 | 전체 자동 테스트 통과 | 확인 | 159개 단위·HTTP·정적 UI·성능·상태 전이 테스트 통과 |
| 2차 | 좌우 행과 한쪽 전용 빈 칸 | 확인 | `tests/test_page_plan.py::test_automatic_plan_highlights_uncertain_and_one_sided_rows`, `tests/test_static_ui.py::test_alignment_review_is_continuous_side_by_side_and_shows_actual_ink` |
| 2차 | 약 3쪽 높이, 100쪽 비차단 연속 스크롤 | 대기 | CSS 높이와 `IntersectionObserver` 계약은 정적 테스트로 확인. 실제 Edge 업로드·스크롤은 파일 선택 권한 해제 뒤 확인 필요 |
| 2차 | 드래그 경고, 취소 무변경, 진행 결과 | 대기 | `tests/test_page_plan.py::test_moving_a_target_reorders_output_and_repairs_against_fixed_sources`와 정적 UI 계약은 통과. 실제 drag/confirm 양 갈래는 파일 선택 권한 해제 뒤 확인 필요 |
| 2차 | 불확실·한쪽 전용·수동 행 강조와 개별 확인 | 확인 | `tests/test_static_ui.py::test_page_review_supports_confirmation_and_target_drag_reordering`, `noteditor/static/app.css`의 상태별 행 스타일 |
| 2차 | 필기 보기 토글은 표시만 변경 | 확인 | `tests/test_static_ui.py::test_alignment_review_is_continuous_side_by_side_and_shows_actual_ink`; CSS로 오버레이만 숨기며 저장 계획은 변경하지 않음 |
| 2차 | 미확인 저장 최종 경고·명시적 승인 | 확인 | `tests/test_app.py::test_page_plan_requires_confirmation_then_forwards_the_validated_plan`, `tests/test_web.py::test_web_page_plan_requires_explicit_unconfirmed_approval`; 경고에 쪽 번호 포함 |
| 2차 | Samsung Notes·Notewise 혼합 행 결과 계약 | 확인 | `tests/test_sdocx_rebuild.py::test_rebuild_accepts_confirmed_target_reorder_and_preserves_source_only_ink`, `tests/test_notewise_transfer.py::test_confirmed_plan_can_reorder_target_pages`, `test_source_only_annotated_page_is_preserved_with_its_old_background` |
| 2차 | 원본·대상 미덮어쓰기 | 확인 | `tests/test_notewise_transfer.py::test_rejects_overwriting_source`와 형식별 출력 경로 검사; 웹 결과는 별도 임시 결과를 다운로드한 뒤 결과 사본만 정리 |
| 3차 | Goodnotes·Flexcil 판정과 샘플 목록 | 확인 | `docs/research/2026-08-30-goodnotes-flexcil-support.md` |
| 3차 | 검증 전 UI 지원 약속 금지 | 확인 | UI 입력 형식은 `.sdocx`·`.notewise`로 유지; 조사 문서는 두 형식을 모두 `추가 샘플 필요`로 판정 |

## 남은 브라우저 검증 절차

Edge 확장 프로그램에서 로컬 파일 접근을 허용한 뒤 아래 세 항목만 실행하면 된다.

1. 합성 PDF 3개×100쪽을 올려 초기·스크롤 중 이미지 요청 수와 동시 요청 상한을 확인한다.
2. 100쪽 필기 비교 화면에서 로딩을 기다리지 않고 연속 스크롤하며 기본 뷰포트에 약 3개 행이
   보이는지 확인한다.
3. 새 PDF 쪽을 드래그해 경고에서 취소한 경우 무변경, 계속한 경우 영향 행의 순서·대응·확인 상태가
   함께 바뀌는지 확인한다.

실물 SDOCX·Notewise·오창모 PDF 및 실제 Samsung Notes·Notewise 앱 왕복 검증은 명세의 범위 밖이며,
이번 합성 검증 완료 여부와 구분한다.
