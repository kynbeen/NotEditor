# summary.ai 합치기 인계 계약 판 1 감사

**감사일:** 2026-08-31  
**대상 명세:** `docs/specs/2026-08-31-01-summary-ai-합치기-인계.md`  
**판정:** 성공 기준 9개 모두 확인

## 검증 결과

- NotEditor: `venv\Scripts\python.exe -m unittest discover -s tests` **183개 통과**
- 정적 검사: `node --check noteditor/static/app.js`, `git diff --check` 통과
- summary.ai: `PYTHONDONTWRITEBYTECODE=1`로 `pipeline.tests.test_noteditor_link` **18개 통과**
- 실제 왕복: summary.ai가 만든 handoff를 NotEditor가 열어 3쪽 PDF와 사이드카를 저장했다. 이어
  summary.ai가 사이드카를 읽고 같은 합치기를 재현해 결과 SHA-256
  `20c8abea5d01ebd62e043f9528d9bea9b17f18513c8b3976434e4f571f6f384e`와 3쪽을 확인했다.
- 패키지: PyInstaller가 `NotEditor.exe`와 `NotEditorLocalWeb.exe`를 함께 만들었고,
  `NotEditor.exe --open-plan <handoff.json>` 프로세스가 계획 인자를 받은 상태로 정상 유지됐다.
- 경계 확인: 작업 전후 `summary.ai`와 `workspace` Git 상태에 변경이 없었고 모든 파일 수정은
  NotEditor 저장소 안에서만 이뤄졌다.

## 핵심 계약 근거

| 계약 | 확인 내용 |
|---|---|
| 계획 입력 | 판 번호, 절대 경로, 존재하는 PDF, 쪽 범위, 별도 출력 경로를 검증한다. |
| 세션 매핑 | 중복 경로는 한 번만 등록하고 매 실행의 새 문서 ID로 선택·순서를 만든다. |
| 화면 적용 | 제목·문서·선택·순서·고정 결과 이름을 첫 화면에 적용하며 일반 웹은 빈 계획을 유지한다. |
| 저장 순서 | PDF 성공 뒤에만 임시 파일과 교체 방식으로 사이드카를 기록한다. 실패 시 오래된 사이드카를 남기지 않는다. |
| 정직한 재현 | 계약 판 1로 손실 없이 표현할 수 없는 교차 재진입·역순 편집은 저장 전에 거부한다. |
| 결정론적 PDF | 서로 다른 프로세스에서도 같은 원본·순서가 같은 PDF 해시를 내도록 결정론적 ID 저장을 사용한다. |
