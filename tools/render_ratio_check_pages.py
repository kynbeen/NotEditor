"""검증 세트의 원본과 결과를 같은 쪽끼리 PNG로 뽑아 눈으로 대조할 수 있게 한다.

각 필기 파일을 **자기 자신의 배경 PDF**에 대고 미리보기하므로, 그려지는 것은 그 파일이
실제로 담고 있는 필기다. 결과 쪽이 새 비율 안에서 본문 위 제자리에 있으면 성공이다.

    python tools/render_ratio_check_pages.py tmp/실기검증
"""
from __future__ import annotations

import argparse
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from noteditor.handwriting_transfer import inspect_transfer, preview_transfer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_ratio_check_set import embedded_pdf_bytes  # noqa: E402

# 필기가 있는 쪽을 찾을 때 이 정도까지만 훑는다. 없으면 그냥 첫 쪽을 쓴다.
SCAN_LIMIT = 40


def composite(background: bytes, ink: bytes) -> Image.Image:
    with Image.open(BytesIO(background)) as base, Image.open(BytesIO(ink)) as layer:
        canvas = base.convert("RGBA")
        canvas.alpha_composite(layer.convert("RGBA").resize(canvas.size))
        return canvas.convert("RGB")


def first_annotated_page(note: Path, background: Path, inspection) -> int:
    for index in range(min(inspection.page_count, SCAN_LIMIT)):
        try:
            _before, _after, _ink, strokes = preview_transfer(note, background, index, inspection)
        except Exception:
            continue
        if strokes:
            return index
    return 0


def render(note: Path, out_png: Path, page_index: int | None = None) -> tuple[int, int]:
    background = note.with_name(f"{note.stem}-배경.pdf")
    background.write_bytes(embedded_pdf_bytes(note))
    inspection = inspect_transfer(note, background)
    index = first_annotated_page(note, background, inspection) if page_index is None else page_index
    _before, after, ink, strokes = preview_transfer(note, background, index, inspection)
    composite(after, ink).save(out_png)
    return index, strokes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="make_ratio_check_set.py 가 만든 폴더")
    args = parser.parse_args()

    root = args.root.resolve()
    failures = 0
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        original = next((p for p in folder.glob("원본.*") if p.suffix != ".pdf"), None)
        result = next((p for p in folder.glob("결과-비율바뀜.*") if p.suffix != ".pdf"), None)
        if original is None or result is None:
            continue
        print(f"\n=== {folder.name} ===")
        try:
            index, strokes = render(original, folder / "대조-원본.png")
            print(f"  원본 {index + 1}쪽, 획 {strokes}개 → 대조-원본.png")
            index, strokes = render(result, folder / "대조-결과.png", index)
            print(f"  결과 {index + 1}쪽, 획 {strokes}개 → 대조-결과.png")
        except Exception as exc:
            failures += 1
            print(f"  실패: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
