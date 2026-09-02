"""비율이 다른 대상 PDF로 실기 검증 세트를 만든다.

원본 필기 파일(Samsung Notes `.sdocx`, Notewise `.notewise`, Goodnotes `.goodnotes`)의
내장 PDF를 꺼내, **같은 내용을 더 긴 페이지에 다시 앉힌 대상 PDF**를 만든 뒤 필기를 옮긴다.
결과 파일을 각 앱으로 가져가 페이지 전 영역·필기 위치·편집 가능 여부를 확인하는 것이 목적이다.

원본은 읽기만 한다. 만들어지는 것은 전부 출력 폴더 아래에 있다.

    python tools/make_ratio_check_set.py <원본 필기 파일> [...] --out tmp/실기검증
"""
from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf

from noteditor.goodnotes_archive import background_pdf, read_document, safe_members
from noteditor.handwriting_transfer import inspect_transfer, transfer_handwriting, output_suffix

# 세로로 이만큼 늘린다. 원본 본문은 가운데에 그대로 두고 위아래로 여백이 생긴다.
HEIGHT_GROWTH = 1.30


def embedded_pdf_bytes(source: Path) -> bytes:
    """필기 파일 안에 들어 있는 배경 PDF를 꺼낸다."""
    suffix = source.suffix.lower()
    from zipfile import ZipFile

    if suffix == ".goodnotes":
        with ZipFile(source) as archive:
            document = read_document(archive, safe_members(archive))
            return background_pdf(archive, document)
    with ZipFile(source) as archive:
        names = archive.namelist()
        if suffix == ".sdocx":
            name = next(n for n in names if n.startswith("media/") and n.endswith(".pdf"))
        elif suffix == ".notewise":
            name = next(n for n in names if n.startswith("pdf/"))
        else:
            raise SystemExit(f"지원하지 않는 형식입니다: {source.name}")
        return archive.read(name)


def taller_target(embedded: bytes, destination: Path) -> tuple[Path, tuple[float, float], tuple[float, float]]:
    """같은 내용을 위아래로 더 긴 페이지 상자에 담은 대상 PDF를 만든다.

    쪽을 새 문서에 ``show_pdf_page`` 로 다시 그리면 원본 쪽이 통째로 하나의 XObject 가 되어
    본문 상자가 **쪽 전체**로 잡힌다. 그러면 정렬 추정이 "본문이 줄었다"고 읽어 엉뚱한 배율을
    고른다. 실제 강의자료가 그렇지 않으므로, 여기서는 내용을 건드리지 않고 페이지 상자만
    늘려 본문이 원래 자리에 그대로 있게 한다.
    """
    with pymupdf.open(stream=embedded, filetype="pdf") as target:
        first = target[0].rect
        before = (round(first.width, 1), round(first.height, 1))
        for page in target:
            rect = page.rect
            margin = rect.height * (HEIGHT_GROWTH - 1) / 2
            page.set_mediabox(
                pymupdf.Rect(rect.x0, rect.y0 - margin, rect.x1, rect.y1 + margin)
            )
        after = (round(first.width, 1), round(first.height * HEIGHT_GROWTH, 1))
        target.save(destination)
    return destination, before, after


def build(source: Path, out_root: Path) -> dict:
    folder = out_root / source.suffix.lower().lstrip(".")
    folder.mkdir(parents=True, exist_ok=True)

    # 원본은 손대지 않는다. 검증 폴더 안에 사본을 두고 그 사본으로만 작업한다.
    copied = folder / f"원본{source.suffix.lower()}"
    shutil.copy2(source, copied)

    embedded = embedded_pdf_bytes(copied)
    target_pdf, before, after = taller_target(embedded, folder / "대상-세로로-늘린.pdf")

    inspection = inspect_transfer(copied, target_pdf)
    output = folder / f"결과-비율바뀜{output_suffix(copied)}"
    result = transfer_handwriting(copied, target_pdf, output)

    with pymupdf.open(target_pdf) as document:
        target_pages = document.page_count
    return {
        "원본": source.name,
        "형식": source.suffix.lower(),
        "원본 쪽 크기": f"{before[0]} x {before[1]}",
        "대상 쪽 크기": f"{after[0]} x {after[1]}",
        "대상 쪽 수": target_pages,
        "정렬 방식": getattr(inspection, "mode", "?"),
        "결과 파일": str(output),
        "저장 보고": {k: v for k, v in result.items() if k != "path"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path, help="원본 필기 파일들")
    parser.add_argument("--out", type=Path, default=Path("tmp/실기검증"), help="출력 폴더")
    args = parser.parse_args()

    out_root = args.out.resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    failures = 0
    for source in args.sources:
        source = source.expanduser().resolve()
        print(f"\n=== {source.name} ===")
        if not source.is_file():
            print("  건너뜀: 파일이 없습니다.")
            failures += 1
            continue
        try:
            for key, value in build(source, out_root).items():
                print(f"  {key}: {value}")
        except Exception:
            failures += 1
            traceback.print_exc()
    print(f"\n출력 폴더: {out_root}")
    return 1 if failures else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
