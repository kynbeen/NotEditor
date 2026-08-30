# Goodnotes·Flexcil 편집 가능한 필기 옮기기 지원 조사

**조사일:** 2026-08-30  
**범위:** 공식 도움말에 공개된 내보내기·가져오기·백업 계약만 검토. 실제 파일 구조 분석과 앱
왕복 검증은 하지 않음.

## 결론

| 형식 | 현재 판정 | 근거 | NotEditor에 넣기 전 필수 조건 |
|---|---|---|---|
| Goodnotes | **추가 샘플 필요** | `.goodnotes` 문서는 다시 가져올 수 있는 편집 가능한 백업이지만, 확인한 공식 문서는 내부 펜 데이터·배경 PDF·쪽 대응 구조를 공개하지 않는다. PDF 백업은 앱에서 같은 방식으로 편집할 수 없다고 명시한다. | 실제 `.goodnotes` 원본/왕복 샘플에서 배경 교체, 쪽 재정렬, 펜 원시 데이터 보존을 구조·앱 양쪽에서 검증 |
| Flexcil | **추가 샘플 필요** | `.flex` 백업은 문서와 연결을 완전한 형태로 복원한다. 별도로 `Original PDF`는 필기·배경 포함 여부를 선택할 수 있고, 설정을 켜면 외부 PDF 주석도 편집할 수 있다. 다만 `.flex` 내부 구조와 PDF 주석의 Flexcil 펜 속성 왕복 보존 범위는 공식 문서에 없다. | `.flex` 백업과 `Original PDF` 샘플을 함께 받아 두 경로를 비교하고, 재가져온 획을 지우기·이동·속성 변경할 수 있는지 검증 |

둘 다 지금 `지원 가능`으로 표시하거나 구현하지 않는다. 평탄화된 PDF나 화면상 동일한 PDF는
편집 가능한 원본 필기 보존의 증거가 아니다.

## Goodnotes

### 공식 계약에서 확인한 것

- Apple 버전은 `Goodnotes document`, PDF(Editable/Flattened), 이미지를 내보낼 수 있다.
- `.goodnotes` 문서와 `.goodnotes.zip` 백업은 다시 가져올 수 있다. Android·Windows·Web도
  `.goodnotes` 문서 가져오기를 지원한다고 안내한다.
- 공식 자동 백업 안내는 `Goodnotes Document`를 다시 가져와 편집할 수 있는 백업으로 설명한다.
  반대로 PDF 백업은 평탄화되어 앱에서 필기를 지우는 등 같은 방식으로 편집할 수 없다고 명시한다.
- `Editable PDF`의 객체는 다른 PDF 뷰어에서 선택·이동·크기 변경할 수 있지만, 이를 Goodnotes로
  다시 가져왔을 때 원래 Goodnotes 펜 획으로 복원된다는 계약은 확인하지 못했다.

### 판정

지원 후보 입력은 PDF가 아니라 `.goodnotes`다. 공식 문서만으로는 문서 내부에서 배경 PDF,
페이지 ID·순서, 펜 획, 형광펜, 이미지·텍스트와 알 수 없는 객체를 구분하거나 원시 바이트를
보존하는 방법을 확정할 수 없다. 따라서 **추가 샘플 필요**다.

### 필요한 실제 샘플과 통과 조건

1. 최신 Goodnotes 6에서 같은 노트의 `.goodnotes`, Editable PDF, Flattened PDF를 각각 내보낸다.
2. 노트에는 압력·색·두께가 다른 펜, 형광펜, 도형, 이미지, 텍스트, 링크와 빈 필기 쪽을 넣는다.
3. 옛 PDF에는 있지만 새 PDF에는 없는 필기 쪽, 새 PDF 전용 쪽, 비슷한 슬라이드와 회전·여백 변화
   쪽을 포함한다.
4. 수정본을 Goodnotes로 다시 가져와 획 단위 지우기, 올가미 이동, 색·두께 변경과 페이지 재정렬이
   가능한지 확인한다.
5. 원본에 알 수 없는 객체가 있어도 수정하지 않은 데이터가 바이트 또는 의미 보존되는지 검사한다.
6. Apple과 Android·Windows의 `.goodnotes`가 같은 구조라는 증거가 없으므로 플랫폼별 샘플을
   따로 확보한다.

## Flexcil

### 공식 계약에서 확인한 것

- `.flex` 백업은 문서 전체와 문서 사이 연결을 포함한 완전한 형태로 다른 기기에서 복원할 수 있다.
- 일반 문서 내보내기의 `Original PDF`는 편집 가능한 PDF이며 텍스트 상자, 이미지, 필기, 원본 배경을
  각각 포함할지 선택할 수 있다. `Flattened PDF`는 더 이상 편집할 수 없다.
- `Import PDF with editable annotations` 실험 설정을 미리 켜면 다른 앱에서 만든 PDF 주석도 Flexcil에서
  편집할 수 있다고 공식 도움말이 안내한다.
- 선택 백업에서 PDF와 노트를 따로 백업하면 연결 링크가 사라질 수 있다는 구버전 안내도 있어,
  문서·필기·링크를 분리해 조용히 다시 조립하면 안 된다.

### 판정

Flexcil은 두 후보가 있다. 가장 강한 보존 후보는 `.flex`이고, 공개 표준에 가까운 후보는
`Original PDF`의 편집 가능한 주석이다. 후자는 새 PDF에 주석 객체만 복사하는 경로가 가능해 보이지만,
이는 공식 문서에서 확인한 기능을 바탕으로 한 **추론**이다. 펜 압력·도구 속성, 페이지 크기 변화,
Flexcil 자체 획의 재가져오기 왕복 보존은 아직 증명되지 않았다. 따라서 **추가 샘플 필요**다.

### 필요한 실제 샘플과 통과 조건

1. 같은 문서의 `.flex` 전체 백업, `Original PDF`(필기 포함·배경 포함), `Original PDF`(필기 포함·배경
   제외), Flattened PDF를 함께 받는다.
2. iOS와 Android 최신 버전에서 각각 만들고 앱 버전·`Import PDF with editable annotations` 설정을
   기록한다.
3. 펜·형광펜·도형·텍스트·이미지·내부 링크와 문서 간 참조 링크를 포함한다.
4. 새 배경에 옮긴 결과를 다시 가져와 획 지우기·올가미 이동·색·두께 변경, 링크, 대상 재정렬,
   옛 파일 전용 쪽 보존을 확인한다.
5. PDF 주석 경로가 하나라도 앱 고유 획 속성을 잃으면 `.flex` 구조 보존 경로만 후보로 남긴다.
6. 어느 경로도 알 수 없는 데이터와 필기 속성을 보존하지 못하면 `안전하게 지원 불가`로 바꾼다.

## 연결 가능한 서드파티 프로젝트

2026-08-30 현재 아래 공개 저장소를 확인했다. 모두 MIT이지만 앱 회사의 공식 프로젝트는 아니며,
현재 기능은 **읽기·표시·변환** 중심이다. NotEditor에 필요한 “수정한 네이티브 파일을 원래 앱이 다시
편집 가능하게 열기” writer는 어느 프로젝트에서도 확인하지 못했다.

| 프로젝트·확인 커밋 | 활용 가능한 부분 | 현재 연결 결정 |
|---|---|---|
| [`fakeminjun7321/goodnotes-pdf-engine`](https://github.com/fakeminjun7321/goodnotes-pdf-engine/tree/3d803ae78f6177d0ea75e952cbfbe79dcdec247a) | Goodnotes 6/7 protobuf 인덱스, 현재 페이지 순서, PDF·이미지 배경, 실험적 TPL 펜 렌더. Python 3.10+, MIT | **우선 참고 구현·검증 오라클.** 배경/페이지 판독은 가장 NotEditor와 가깝지만 writer가 없고 일부 객체를 지원하지 않으므로 런타임 의존성으로 바로 넣지 않음 |
| [`cable729/inkterop`](https://github.com/cable729/inkterop/tree/f16eb8d2a425637aab629e6cc90c00a18f17009f) | `.goodnotes`의 펜·색 읽기, 압력·기울기까지 표현 가능한 중간 형식, 역공학 문서·테스트. Python 3.12+, MIT | **두 번째 오라클.** NotEditor Docker Python과 버전은 맞지만 의존성이 크고 Goodnotes 쓰기는 미지원. 샘플 확보 뒤 두 파서의 결과를 교차 검증 |
| [`janptn/flexcil-backup-viewer`](https://github.com/janptn/flexcil-backup-viewer/tree/8b1c30f432a34ef315ef669cf5eb87a589ae6394) | `.flx`/백업 ZIP, `documents.list`, PDF 추출, 압력 기반 필기 표시. MIT | **Flexcil 1순위 참고 구현.** React/브라우저 reader이고 복원 가능한 `.flex` 출력은 하지 않으므로 파서 사실과 합성 fixture 생성에만 사용 |
| [`c0lbarator/FWebViewer`](https://github.com/c0lbarator/FWebViewer/tree/82a88e78d7c2722170c904b62cbd7159538ad161) | `.flx` 페이지·첨부·그림 JSON, base64 점 좌표 디코더, 이미지·오디오 레이어. MIT | **독립 교차 검증용.** README가 검토되지 않은 프로토타입이라고 경고하므로 직접 의존하지 않고 앞 프로젝트와 좌표 해석이 일치하는지만 확인 |

### 연결 방식

- 지금 제품 의존성이나 Git 서브모듈로 고정하지 않는다. 읽기 전용 코드만 연결해도 배포 크기와
  공급망은 늘지만 네이티브 결과 writer가 없어 사용자 기능은 완성되지 않기 때문이다.
- 실제 샘플이 생기면 별도 실험 브랜치에서 위 커밋을 고정한 **오라클 테스트**를 먼저 만든다.
  Goodnotes는 두 독립 파서가 페이지·배경·획 수·좌표를 같게 읽는지, Flexcil도 두 viewer가 같은
  페이지·획 좌표를 내는지 비교한다.
- 일치한 최소 파싱 로직만 MIT 고지와 출처를 남겨 NotEditor의 안전한 ZIP·protobuf 계층에 맞게
  포팅한다. 외부 프로젝트의 UI나 설치 프로그램은 연결하지 않는다.
- 최종 writer는 원본 엔트리의 알 수 없는 바이트를 보존하고 새 복사본만 만드는 NotEditor 규칙으로
  별도 구현한다. 출력물을 실제 앱에서 왕복 검증하기 전에는 UI 형식 목록에 추가하지 않는다.

## 검토한 공식 자료

- Goodnotes: [문서·페이지 내보내기](https://support.goodnotes.com/hc/en-us/articles/7353742824975-Export-documents-or-pages)
- Goodnotes: [파일 가져오기](https://support.goodnotes.com/hc/en-us/articles/7353717816463-Import-files-into-Goodnotes)
- Goodnotes: [자동 백업 형식과 편집 가능성](https://support.goodnotes.com/hc/en-us/articles/7352786555279-How-to-Set-Up-Auto-Backup-in-Goodnotes)
- Goodnotes: [Editable PDF와 Flattened PDF 차이](https://support.goodnotes.com/hc/en-us/articles/8537070839183-Differences-between-Editable-and-Flattened-PDF-Formats)
- Flexcil: [.flex 백업으로 기기 간 이동](https://support.flexcil.com/hc/en-us/articles/8424929523993-Move-Data-to-Another-Device)
- Flexcil: [문서 내보내기 옵션](https://support.flexcil.com/hc/en-us/articles/10486592347545-Export-document-s)
- Flexcil: [외부 PDF 주석 편집 설정](https://support.flexcil.com/hc/en-us/articles/7583047292825-How-to-edit-annotations-create-from-the-other-applications)
- Flexcil: [백업과 복원](https://www.flexcil.com/support/data-backup-and-restore/)

서드파티 저장소의 라이선스·기능·커밋은 위 표의 고정 링크를 기준으로 확인했다.
