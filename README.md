# NotEditor

NotEditor는 PDF 문서 합치기와 Samsung Notes 필기 옮기기를 한 화면에서 제공하는 도구입니다.
Windows 데스크톱 앱과 Docker 기반 웹앱이 같은 PDF·SDOCX 처리 엔진을 사용합니다.

## 기능

### 문서 합치기

- 여러 PDF에서 필요한 쪽을 선택하고 드래그해 순서 변경
- `1-3, 5, 8-` 형식의 빠른 쪽 범위 선택
- 모든 원본 쪽을 연속 스크롤로 미리보기
- 텍스트·벡터·이미지·링크·주석·양식 위젯을 가능한 범위에서 보존
- 미리보기만 PNG로 렌더하고 결과 PDF는 원본 페이지 객체를 복사

### 필기 옮기기

- 필기·형광펜이 들어 있는 Samsung Notes `.sdocx`를 새 PDF 배경으로 이전
- 쪽 추가·삭제 시 본문 지문과 순서를 이용해 공통 쪽 자동 매칭
- 페이지 크기나 여백 변경 시 본문 위치 기준 자동 정렬
- 실제 필기를 원본·새 배경 위에 겹쳐 저장 전에 확인
- 원본 SDOCX와 PDF를 수정하지 않고 새 `.sdocx`로 저장

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

환경 변수:

| 변수 | 기본값 | 설명 |
|---|---:|---|
| `PORT` | `8000` | 웹 서버 포트 |
| `NOTEDITOR_HOST` | `0.0.0.0` | 바인드 주소 |
| `NOTEDITOR_MAX_UPLOAD_MB` | `512` | 파일 하나의 최대 업로드 크기 |
| `NOTEDITOR_SESSION_TTL` | `7200` | 비활성 세션 만료 시간(초) |

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

`v0.4.0` 같은 태그를 푸시하면 `.github/workflows/release.yml`이 다음 작업을 자동 수행합니다.

1. 전체 테스트 실행
2. PyInstaller로 독립 실행 폴더 생성
3. Inno Setup으로 `NotEditor-Setup-<버전>.exe` 생성
4. GitHub Release에 설치 파일 첨부

로컬에서는 Python 개발 의존성과 Inno Setup 6을 설치한 뒤 같은 과정을 실행할 수 있습니다.

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m noteditor.make_icon
.\venv\Scripts\pyinstaller.exe --noconfirm NotEditor.spec
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "installer\NotEditor.iss"
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
- 원본 파일은 읽기 전용으로 열며 사용자가 요청한 결과 외에는 영구 파일을 만들지 않습니다.

제3자 코드 고지는 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)를 확인하세요.
