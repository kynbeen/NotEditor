# NotEditor

NotEditor는 PDF 문서 합치기와 Samsung Notes·Notewise 필기 옮기기를 한 화면에서 제공하는 도구입니다.
Windows 데스크톱 앱과 Docker 기반 웹앱이 같은 PDF·필기 문서 처리 엔진을 사용합니다.

## 기능

### 문서 합치기

- 여러 PDF에서 필요한 쪽을 선택하고 드래그해 순서 변경
- `1-3, 5, 8-` 형식의 빠른 쪽 범위 선택
- 모든 원본 쪽을 연속 스크롤로 미리보기
- 텍스트·벡터·이미지·링크·주석·양식 위젯을 가능한 범위에서 보존
- 미리보기만 PNG로 렌더하고 결과 PDF는 원본 페이지 객체를 복사

### 필기 옮기기

- 필기·형광펜이 들어 있는 Samsung Notes `.sdocx`를 새 PDF 배경으로 이전
- 필기가 들어 있는 Notewise `.notewise`를 새 PDF 배경으로 이전
- 쪽 추가·삭제 시 본문 지문과 순서를 이용해 공통 쪽 자동 매칭
- 페이지 크기나 여백 변경 시 본문 위치 기준 자동 정렬
- 실제 필기를 원본·새 배경 위에 겹쳐 저장 전에 확인
- 원본 SDOCX와 PDF를 수정하지 않고 새 `.sdocx`로 저장
- 원본 Notewise와 PDF를 수정하지 않고 새 `.notewise`로 저장

## 가장 쉬운 Windows 설치

GitHub Releases에서 최신 `NotEditor-Setup-<버전>.exe`를 내려받아 실행합니다. 설치 프로그램은
시작 메뉴와 선택적으로 바탕화면에 바로가기를 만듭니다. 현재 저장소가 비공개라서 Release 접근
권한이 없는 사용자에게는 설치 파일을 별도로 전달해야 합니다.

소스 압축 파일을 받은 사용자는 PowerShell에서 아래 한 줄로 설치할 수도 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Python 3.12 이상이 필요하며, 전용 `venv` 생성·의존성 설치·아이콘 생성·바로가기 등록을 한 번에
처리합니다. 기존 `setup.ps1`과 `install-app.ps1`은 개발 및 이전 설치 흐름과의 호환을 위해 남아
있습니다.

## 데스크톱 개발 실행

```powershell
.\install.ps1
.\venv\Scripts\python.exe -m noteditor --debug
```

앱 로그는 `%LOCALAPPDATA%\NotEditor\app.log`에 기록됩니다.

데스크톱 앱도 내장 로컬 HTTP 서버에서 동일한 PWA 셸을 실행합니다. 시작할 때 창을 최대화하며
`F11`로 테두리 없는 전체화면을 켜거나 끌 수 있습니다. 문서 선택과 저장은 기존처럼 로컬 네이티브
대화상자와 Python 브리지를 사용합니다.

## 로컬 웹 앱 실행

Windows 설치 후 `NotEditor 로컬 웹` 바로가기를 실행하면 배포 서버 대신 사용자 PC 안에서만
NotEditor 웹 서버가 시작됩니다. Edge 또는 Chrome의 독립 앱 모드 창이 열리며, 배포 웹앱과 같은
브라우저 파일 업로드·다운로드 흐름을 사용합니다. 서버는 `http://127.0.0.1:8765`에만 열리므로
같은 네트워크의 다른 기기에서는 접근할 수 없습니다.

소스 체크아웃에서는 다음 명령으로 같은 실행기를 확인할 수 있습니다.

```powershell
.\venv\Scripts\python.exe -m noteditor.local_web
```

기존 `NotEditor` 바로가기는 pywebview 데스크톱 앱을 계속 열며, 브라우저에서 설치한 원격 PWA도
변경되지 않습니다. 로컬 웹 전용 포트를 다른 프로그램이 사용 중이면
`%LOCALAPPDATA%\NotEditor\local-web.log`에 진단 내용을 남기고 실행을 중단합니다.

## summary.ai 합치기·원본 비교 인계

summary.ai에서 `합치기`를 누르면 NotEditor가 지정된 로컬 수집함을 시작 경로로 바로 열어 여러
PDF의 쪽과 순서를 고르게 합니다. 이미 사용한 PDF의 수집함 원본이 바뀌면 실제 사용본과 현재
원본을 같은 쪽·다른 쪽 두 열로 비교하고, 온전한 파일은 `전체 갱신`, 합친 파일은 `합쳐서 갱신`,
기존 파일을 유지하려면 `넘어가기` 결정을 summary.ai에 돌려줍니다.

```powershell
.\venv\Scripts\pythonw.exe -m noteditor --open-plan C:\path\to\handoff.json
```

새 인계 계획은 workspace의 계약 판 2를 따르며 기존 판 1 합치기 계획도 계속 읽습니다. 합치기
결과는 계획의 `output_path`에 PDF를 먼저 기록하고 바로 옆에 `<결과>.merge.json` 사이드카를
남깁니다. 원본 검토 결정은 결과가 필요한 경우 그 저장까지 끝난 뒤 별도 결정 파일에 원자적으로
기록합니다. 기준 파일과 선택 대상은 지정된 로컬 수집함 경계 안에서만 다루며 Drive 파일을 직접
열거나 수정하지 않습니다. 일반 실행과 웹 실행은 이 옵션을 사용하지 않고 기존 저장 흐름을 유지합니다.

## 웹앱 실행

Docker가 있으면 다음 명령만 실행합니다.

```powershell
docker compose up --build
```

브라우저에서 `http://localhost:8000`을 엽니다. Docker 없이 개발 서버를 실행하려면:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-web.txt
.\venv\Scripts\python.exe -m noteditor.web
```

웹 업로드는 브라우저 세션별 임시 디렉터리에 격리됩니다. 기본 만료 시간은 2시간이며 서버 종료,
세션 만료 또는 필기 선택 초기화 시 정리됩니다. 결과 파일은 생성 직후 다운로드로 반환되고 서버의
임시 출력은 응답 완료 후 삭제됩니다.

웹앱은 PWA로 제공됩니다. 지원 브라우저의 주소창 또는 메뉴에서 NotEditor를 설치하면 독립 창과
앱 아이콘으로 실행할 수 있습니다. 서비스 워커는 UI 파일만 오프라인 캐시하며 `/api/` 요청,
업로드 문서와 변환 결과는 캐시하지 않습니다. 문서 작업에는 서버 연결이 필요합니다.

환경 변수:

| 변수 | 기본값 | 설명 |
|---|---:|---|
| `PORT` | `8000` | 웹 서버 포트 |
| `NOTEDITOR_HOST` | `0.0.0.0` | 바인드 주소 |
| `NOTEDITOR_MAX_UPLOAD_MB` | `512` | 파일 하나의 최대 업로드 크기 |
| `NOTEDITOR_SESSION_TTL` | `7200` | 비활성 작업공간 만료 시간(초) |
| `NOTEDITOR_MAX_SESSIONS` | `200` | 동시 작업공간 상한. 넘으면 가장 오래 쉰 것부터 정리 |
| `NOTEDITOR_SWEEP_INTERVAL` | `60` | 만료된 작업공간을 쓸어내는 주기(초) |
| `NOTEDITOR_PREVIEW_CONCURRENCY` | `2` | 프로세스 전체에서 동시에 렌더링할 미리보기 수 |
| `NOTEDITOR_PREVIEW_CACHE_MB` | `16` | 사용자 작업공간 하나의 미리보기 LRU 캐시 상한(MB) |
| `NOTEDITOR_ANALYSIS_CONCURRENCY` | `1` | 동시에 실행할 필기 문서 분석 작업 수 |

### 접속자별 작업공간

접속자마다 임시 폴더 하나가 배정되고, 올린 파일·미리보기·결과는 전부 그 안에서만 삽니다.
브라우저는 `HttpOnly` 쿠키로 자기 작업공간을 가리킵니다. 작업공간 안은 도구별로 다시
나뉩니다 — `uploads/documents/`와 `uploads/handwriting/`.

- **작업공간은 첫 화면과 API 요청에서만 만들어집니다.** 정적 자산과 상시 가동 핑은 만들지
  않습니다. 그러지 않으면 접속 한 번에 아무도 쓰지 않는 폴더가 여러 개 생깁니다.
- **사용자별 응답에는 `Cache-Control: no-store`와 `Vary: Cookie`가 붙습니다.** 중간 프록시가
  이걸 저장하면 다음 사람에게 남의 문서가, 첫 화면이라면 남의 세션 쿠키까지 건네집니다.
- **자동 정리**: 같은 자리에 파일을 다시 올리면 앞엣것을 지웁니다. 목록에서 뺀 문서는 사본까지
  지우고, 등록에 실패한 업로드도 남기지 않습니다. 비활성 작업공간은
  `NOTEDITOR_SWEEP_INTERVAL`마다 통째로 사라집니다.
- **수동 정리는 도구별입니다.** 문서 합치기의 `문서 비우기`와 필기 옮기기의 `선택 초기화`는
  각각 자기 폴더만 비웁니다. 한쪽을 정리해도 다른 쪽에서 고르던 파일은 그대로 남습니다.

실서비스에서는 Docker 이미지를 HTTPS 역방향 프록시 뒤에 두고, 프록시의 요청 본문 제한도
`NOTEDITOR_MAX_UPLOAD_MB` 이상으로 맞추세요. NotEditor는 로그인 기능을 제공하지 않으므로 공개
인터넷에 배포할 때는 호스팅 플랫폼이나 프록시에서 접근 제어를 추가하는 것을 권장합니다.

## Docker 배포

업체 종속 설정은 없습니다. 저장소 루트의 `Dockerfile`을 빌드할 수 있는 Render, Fly.io,
Cloud Run, Railway 또는 일반 컨테이너 서버에 배포할 수 있습니다.

저장소의 `render.yaml`은 서울과 가까운 싱가포르 리전의 Render 웹 서비스를 정의합니다.
무료 인스턴스의 메모리 한계를 고려해 파일 하나당 업로드 한도는 100MB, 비활성 세션 만료는
1시간으로 설정합니다. 업로드와 변환은 모두 Render 컨테이너 안에서 실행되며 사용자 PC에서
별도 서버를 실행할 필요가 없습니다.

```bash
docker build -t noteditor .
docker run --rm -p 8000:8000 noteditor
```

서버는 상태를 로컬 임시 저장소에만 두므로 여러 인스턴스로 확장할 때는 같은 사용자의 요청이 같은
인스턴스로 가도록 세션 고정(sticky session)을 사용해야 합니다.

## 테스트

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m unittest discover -s tests
node --check noteditor\static\app.js
```

## Windows 설치 파일 만들기

`v0.5.0` 같은 태그를 푸시하면 `.github/workflows/release.yml`이 다음 작업을 자동 수행합니다.

1. 전체 테스트 실행
2. 태그에서 버전을 확정해 앱과 설치 파일에 함께 새김
3. PyInstaller로 독립 실행 폴더 생성
4. Inno Setup으로 `NotEditor-Setup-<버전>.exe` 생성
5. GitHub Release에 설치 파일 첨부

### 버전은 어디서 오나

**깃 태그가 유일한 출처입니다.** 소스에 버전 번호를 적어 두지 않으므로 앱이 말하는 버전과
설치 파일 버전이 어긋날 일이 없습니다.

| 상황 | `/api/health` 와 앱 로그가 말하는 버전 |
| --- | --- |
| `v0.5.0` 태그로 만든 릴리스 | `0.5.0` |
| 태그 이후 3커밋 진행한 개발 체크아웃 | `0.5.0+3.gbf90fcf` |
| 커밋하지 않은 수정이 있는 상태 | 뒤에 `.dirty` 가 붙음 |
| 태그가 아직 없는 저장소 | `0.0.0+<커밋해시>` |
| 알 방법이 전혀 없을 때 | `0.0.0+unknown` |

`NOTEDITOR_VERSION` 환경변수를 주면 그 값이 무엇보다 우선합니다.

로컬에서는 Python 개발 의존성과 Inno Setup 6을 설치한 뒤 같은 과정을 실행할 수 있습니다.

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m noteditor.make_icon
$version = .\venv\Scripts\python.exe -m noteditor.stamp_version
.\venv\Scripts\pyinstaller.exe --noconfirm NotEditor.spec
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "/DAppVersion=$version" "installer\NotEditor.iss"
```

## 폴더 구조

```text
NotEditor/                 Git 저장소 루트
├─ noteditor/              import 가능한 Python 애플리케이션 패키지
│  └─ static/              데스크톱과 웹이 함께 쓰는 UI
├─ tests/                  엔진·UI·웹 API 테스트
├─ installer/              Windows 설치 프로그램 정의
├─ .github/workflows/      테스트 및 Release 자동화
├─ Dockerfile              웹 배포 이미지
└─ install.ps1             소스 기반 원터치 Windows 설치
```

저장소와 `noteditor` 패키지가 한 단계 중첩된 것은 의도된 구조입니다. Python 런타임 코드와 정적
자원을 하나의 import 패키지로 묶어 테스트·Docker·PyInstaller에서 같은 경로로 찾게 하고, 루트의
문서·설치·배포 파일과 섞이지 않게 합니다.

## 제한 및 안전

- 암호화된 PDF와 DRM 우회는 지원하지 않습니다.
- 페이지 구성이 바뀌므로 기존 디지털 서명은 유효하지 않게 됩니다.
- 여러 원본을 합칠 때 책갈피, 문서 첨부, 문서 단위 서명은 복사하지 않고 경고합니다.
- 필기 이전은 Samsung의 비공개 SDOCX 형식을 이용한 상호운용 기능입니다.
- Notewise 이전은 공개되지 않은 내보내기 형식을 관찰해 구현한 상호운용 기능입니다. PDF 배경
  교체, 페이지 추가·삭제, 본문 기준 자동 정렬, 펜·형광펜 미리보기를 지원합니다. 기존 쪽의 순서
  변경은 잘못된 필기 매칭을 막기 위해 거부하며, 펜·형광펜 외 Notewise 객체의 미리보기 렌더링은
  후속 지원 범위입니다.
- 원본 파일은 읽기 전용으로 열며 사용자가 요청한 결과 외에는 영구 파일을 만들지 않습니다.

제3자 코드 고지는 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)를 확인하세요.
