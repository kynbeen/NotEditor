# PATH 등록과 `noteditor` 명령 인계

**날짜:** 2026-08-31
**기기:** LAPTOP
**관련 명세:** 없음 (명세 없이 진행한 작은 기능 추가)

## 현재 상태

저장소 루트의 `noteditor.cmd` 셰임(shim — 명령어 이름만으로 실행되게 하는 얇은 실행 파일)과
`install.ps1`의 PATH 등록 단계가 `3eb49c4`에 있고 `origin/main`까지 푸시됐다. 작업 트리는 깨끗하다.
이 기기에는 `C:\dev\NotEditor`가 사용자 PATH에 이미 등록돼 있어 아무 폴더에서나 `noteditor`로
데스크톱 앱이 뜬다. 전체 189개 테스트를 통과했다. summary.ai 쪽 대응 변경은 그 저장소의
`92b1a9c`에 있다.

## 이번 세션에 한 일

- summary.ai GUI 버튼이 NotEditor를 못 띄운다는 신고를 진단했다. **NotEditor 쪽 문제가 아니었다** —
  실행 중이던 summary.ai 서버가 기능 코드보다 5시간 30분 오래된 프로세스였다(자세한 내용은
  summary.ai 인계 참고).
- 루트에 `noteditor.cmd`를 추가했다. `venv\Scripts\pythonw.exe -m noteditor`로 띄우고, venv가 없으면
  `dist\NotEditor\NotEditor.exe`로 넘어간다.
- `install.ps1`에 `[5/5] PATH 등록` 단계를 넣었다. 이미 있으면 중복 추가하지 않는다.
- `.gitattributes`에 `*.cmd`·`*.bat`를 `eol=crlf`로 못 박았다.
- README에 `PATH 등록` 절을 추가했다 — 사용법, 셰임을 옮기면 안 되는 이유, ASCII·CRLF 제약.
- 이 기기에 PATH를 실제로 등록하고, 무관한 폴더(`C:\Users\ruxxl`)에서 `noteditor`로 창이 뜨는 것과
  summary.ai가 `source: "path"`로 찾는 것을 확인했다.

## 내린 결정과 근거

- **셰임을 저장소 루트에 둔다.** summary.ai가 `shutil.which("noteditor")`로 찾은 파일의 **부모
  폴더를 NotEditor 루트로 간주**하기 때문이다. 셰임만 다른 bin 폴더에 두면 루트를 알 방법이 없고,
  헤드리스 재합치기에 필요한 venv 파이썬도 찾지 못한다.
- **배치 파일은 ASCII·CRLF로만 쓴다.** LF만 있는 `.cmd`를 cmd.exe가 오해해 `rem` 주석 줄을 명령으로
  실행하려 들었고("'ASCII' is not recognized"), OEM 코드페이지에서 한글 주석이 깨져 엉뚱한 파이썬을
  실행했다. 실제로 이 함정에 두 번 빠졌다(먼저 한글 주석, 다음 LF). 한글 설명은 README와 summary.ai
  파이썬 쪽에 둔다.
- 패키지 exe 대체 경로를 셰임에 넣었다. 체크아웃 없이 `dist`만 있는 상황에서도 같은 명령이 동작한다.

## 고려했다가 안 한 것

| 대안 | 왜 안 했나 |
|---|---|
| 셰임을 `%LOCALAPPDATA%\NotEditor\bin`에 두고 그 폴더만 PATH 등록 | 셰임 위치에서 NotEditor 루트를 되짚을 수 없어 루트를 적은 별도 파일이 필요해진다. |
| `noteditor.cmd` 주석을 한글로 유지 | cmd.exe가 OEM 코드페이지로 읽어 주석이 명령으로 깨져 실행된다. |
| 검증하려고 `install.ps1` 전체 실행 | pip 재설치와 아이콘·바로가기 재생성까지 일어난다. PATH 블록만 같은 코드로 따로 돌렸다. |
| `install-app.ps1`에도 PATH 단계 추가 | README가 `install.ps1`을 정식 설치 경로로 안내한다. 두 곳에 같은 로직을 두면 갈라진다. |

## 변경한 파일

| 파일 | 무엇을 / 왜 |
|---|---|
| `noteditor.cmd` | PATH용 셰임. pythonw → 패키지 exe 순으로 앱을 띄운다 |
| `install.ps1` | `[5/5] PATH 등록` 단계 |
| `.gitattributes` | `*.cmd`·`*.bat` 체크아웃 시 CRLF 보장 |
| `README.md` | `PATH 등록` 절 |

## 미해결 질문 / 블로커

- 없음.
- PATH는 **새로 여는 터미널부터** 적용된다. 이미 열려 있던 창과 그 창에서 띄운 프로세스는 옛 PATH를
  그대로 쓴다.
- `install.ps1`의 다른 단계(pip 설치·아이콘·바로가기)는 이번에 다시 돌리지 않았다. PATH 블록만
  같은 코드로 따로 실행해 확인했다.

## 다음 단계

1. 다른 기기에서 `install.ps1`을 한 번 돌려 PATH 단계가 처음부터 끝까지 도는지 확인한다.
2. summary.ai `feat/source-link-queue-order`를 `main`에 병합할지 정한다(11 커밋 앞섬).
3. 새 작업은 `/devkit:spec`으로 명세부터 만든다.

## 재개 시 읽어야 할 파일

- `noteditor.cmd`
- `install.ps1` (5단계)
- `README.md` 의 `PATH 등록` 절
- `C:\dev\summary.ai\docs\handoffs\2026-08-31-04-서버-낡음-진단과-PATH-탐색.md`
